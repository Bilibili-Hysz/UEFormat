from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from .runtime_protocol import (
    MAX_FMODEL_IDENTITY_PATH_BYTES,
    MAX_FMODEL_TEXTURE_ID_BYTES,
    MAX_FMODEL_TEXTURES,
    _read_non_negative_count,
    _read_strict_legacy_fstring,
    _validate_max_count,
    _validate_top_level_count,
)

if TYPE_CHECKING:
    from io_scene_ueformat.importer.reader import FArchiveReader


class TextureResourceKind(str, Enum):
    """Discriminator for the three valid importer-side texture resource shapes."""

    PARAMETER = "parameter"
    LEGACY_EMBEDDED = "legacy-embedded"
    IDENTITY = "identity"


@dataclass(slots=True)
class _ParameterTextureResource:
    id: int
    unreal_object_path: str
    location_mode: int
    uri: str
    color_space: int
    packing: int
    uv_set: int


@dataclass(slots=True)
class _EmbeddedTextureResource:
    id: int
    unreal_object_path: str
    mime: str
    data: bytes


@dataclass(slots=True)
class _IdentityTextureResource:
    id: int
    unreal_object_path: str
    location_mode: int
    uri: str
    color_space: int
    packing: int
    uv_set: int
    stable_id: str
    expected_byte_length: int | None
    expected_content_hash: bytes
    identity_embedded: bool


TextureResourceVariant = _ParameterTextureResource | _EmbeddedTextureResource | _IdentityTextureResource


class TextureResource:
    """Compatibility facade over a closed parameter/embedded/identity union.

    Existing importer callers retain the historical flattened properties and
    constructor. New reader paths construct an explicit variant so fields from
    unrelated wire shapes cannot coexist inside one object.
    """

    __slots__ = ("_variant",)
    __hash__ = None
    __match_args__ = (
        "id",
        "unreal_object_path",
        "mime",
        "data",
        "location_mode",
        "uri",
        "color_space",
        "packing",
        "uv_set",
        "stable_id",
        "expected_byte_length",
        "expected_content_hash",
        "identity_embedded",
    )

    def __init__(
        self,
        id: int,
        unreal_object_path: str,
        mime: str = "",
        data: bytes = b"",
        location_mode: int = 0,
        uri: str = "",
        color_space: int = 0,
        packing: int = 0,
        uv_set: int = 0,
        stable_id: str = "",
        expected_byte_length: int | None = None,
        expected_content_hash: bytes = b"",
        identity_embedded: bool = False,
    ) -> None:
        # Before legacy payload fields were introduced, positional arguments 3
        # and 4 represented location_mode and uri. Preserve that source shape.
        if isinstance(mime, int) and not isinstance(mime, bool) and isinstance(data, str):
            if location_mode != 0 or uri:
                raise ValueError("Ambiguous TextureResource positional and named location fields")
            location_mode = mime
            uri = data
            mime = ""
            data = b""

        if not isinstance(mime, str):
            raise TypeError("TextureResource mime must be a string")
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("TextureResource data must be bytes-like")
        if not isinstance(expected_content_hash, (bytes, bytearray, memoryview)):
            raise TypeError("TextureResource expected_content_hash must be bytes-like")

        data_bytes = bytes(data)
        hash_bytes = bytes(expected_content_hash)
        has_embedded_fields = bool(mime or data_bytes)
        has_identity_fields = bool(
            stable_id
            or expected_byte_length is not None
            or hash_bytes
            or identity_embedded
        )
        has_parameter_fields = bool(
            location_mode
            or uri
            or color_space
            or packing
            or uv_set
        )

        if has_embedded_fields:
            if has_parameter_fields or has_identity_fields:
                raise ValueError("Legacy embedded TextureResource cannot contain parameter or identity fields")
            self._variant = _EmbeddedTextureResource(id, unreal_object_path, mime, data_bytes)
            return

        if has_identity_fields:
            if not stable_id and (expected_byte_length is not None or hash_bytes or identity_embedded):
                raise ValueError("Identity TextureResource integrity fields require stable_id")
            if expected_byte_length is not None and expected_byte_length < 0:
                raise ValueError("Identity TextureResource expected_byte_length must be non-negative")
            if hash_bytes and len(hash_bytes) != 32:
                raise ValueError("Identity TextureResource expected_content_hash must contain 32 bytes")
            self._variant = _IdentityTextureResource(
                id,
                unreal_object_path,
                location_mode,
                uri,
                color_space,
                packing,
                uv_set,
                stable_id,
                expected_byte_length,
                hash_bytes,
                identity_embedded,
            )
            return

        self._variant = _ParameterTextureResource(
            id,
            unreal_object_path,
            location_mode,
            uri,
            color_space,
            packing,
            uv_set,
        )

    @classmethod
    def _from_variant(cls, variant: TextureResourceVariant) -> TextureResource:
        resource = cls.__new__(cls)
        resource._variant = variant
        return resource

    @classmethod
    def parameter(
        cls,
        id: int,
        unreal_object_path: str,
        *,
        location_mode: int,
        uri: str,
        color_space: int = 0,
        packing: int = 0,
        uv_set: int = 0,
    ) -> TextureResource:
        return cls._from_variant(_ParameterTextureResource(
            id,
            unreal_object_path,
            location_mode,
            uri,
            color_space,
            packing,
            uv_set,
        ))

    @classmethod
    def embedded(
        cls,
        id: int,
        unreal_object_path: str,
        *,
        mime: str,
        data: bytes,
    ) -> TextureResource:
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("TextureResource data must be bytes-like")
        return cls._from_variant(_EmbeddedTextureResource(id, unreal_object_path, mime, bytes(data)))

    @classmethod
    def identity(
        cls,
        id: int,
        unreal_object_path: str,
        *,
        location_mode: int,
        uri: str,
        color_space: int,
        packing: int,
        uv_set: int,
        stable_id: str,
        expected_byte_length: int | None,
        expected_content_hash: bytes,
        identity_embedded: bool,
    ) -> TextureResource:
        return cls(
            id=id,
            unreal_object_path=unreal_object_path,
            location_mode=location_mode,
            uri=uri,
            color_space=color_space,
            packing=packing,
            uv_set=uv_set,
            stable_id=stable_id,
            expected_byte_length=expected_byte_length,
            expected_content_hash=expected_content_hash,
            identity_embedded=identity_embedded,
        )

    @property
    def kind(self) -> TextureResourceKind:
        if isinstance(self._variant, _EmbeddedTextureResource):
            return TextureResourceKind.LEGACY_EMBEDDED
        if isinstance(self._variant, _IdentityTextureResource):
            return TextureResourceKind.IDENTITY
        return TextureResourceKind.PARAMETER

    @property
    def variant(self) -> TextureResourceVariant:
        return self._variant

    @property
    def id(self) -> int:
        return self._variant.id

    @id.setter
    def id(self, value: int) -> None:
        self._variant.id = value

    @property
    def unreal_object_path(self) -> str:
        return self._variant.unreal_object_path

    @unreal_object_path.setter
    def unreal_object_path(self, value: str) -> None:
        self._variant.unreal_object_path = value

    @property
    def mime(self) -> str:
        return self._variant.mime if isinstance(self._variant, _EmbeddedTextureResource) else ""

    @mime.setter
    def mime(self, value: str) -> None:
        if not isinstance(self._variant, _EmbeddedTextureResource):
            raise AttributeError("mime is only valid for legacy embedded TextureResource")
        self._variant.mime = value

    @property
    def data(self) -> bytes:
        return self._variant.data if isinstance(self._variant, _EmbeddedTextureResource) else b""

    @data.setter
    def data(self, value: bytes) -> None:
        if not isinstance(self._variant, _EmbeddedTextureResource):
            raise AttributeError("data is only valid for legacy embedded TextureResource")
        self._variant.data = bytes(value)

    @property
    def image_bytes(self) -> bytes:
        """Raw bytes from an FMODEL_TEXTURES legacy embedded resource."""
        if not isinstance(self._variant, _EmbeddedTextureResource):
            raise ValueError("TextureResource does not carry a legacy embedded payload")
        return self._variant.data

    def _parameter_variant(self) -> _ParameterTextureResource | _IdentityTextureResource:
        if isinstance(self._variant, _EmbeddedTextureResource):
            raise AttributeError("Legacy embedded TextureResource has no material parameter fields")
        return self._variant

    @property
    def location_mode(self) -> int:
        return 0 if isinstance(self._variant, _EmbeddedTextureResource) else self._variant.location_mode

    @location_mode.setter
    def location_mode(self, value: int) -> None:
        self._parameter_variant().location_mode = value

    @property
    def uri(self) -> str:
        return "" if isinstance(self._variant, _EmbeddedTextureResource) else self._variant.uri

    @uri.setter
    def uri(self, value: str) -> None:
        self._parameter_variant().uri = value

    @property
    def color_space(self) -> int:
        return 0 if isinstance(self._variant, _EmbeddedTextureResource) else self._variant.color_space

    @color_space.setter
    def color_space(self, value: int) -> None:
        self._parameter_variant().color_space = value

    @property
    def packing(self) -> int:
        return 0 if isinstance(self._variant, _EmbeddedTextureResource) else self._variant.packing

    @packing.setter
    def packing(self, value: int) -> None:
        self._parameter_variant().packing = value

    @property
    def uv_set(self) -> int:
        return 0 if isinstance(self._variant, _EmbeddedTextureResource) else self._variant.uv_set

    @uv_set.setter
    def uv_set(self, value: int) -> None:
        self._parameter_variant().uv_set = value

    @property
    def stable_id(self) -> str:
        return self._variant.stable_id if isinstance(self._variant, _IdentityTextureResource) else ""

    @stable_id.setter
    def stable_id(self, value: str) -> None:
        if not isinstance(self._variant, _IdentityTextureResource):
            raise AttributeError("stable_id is only valid for identity TextureResource")
        self._variant.stable_id = value

    @property
    def expected_byte_length(self) -> int | None:
        return self._variant.expected_byte_length if isinstance(self._variant, _IdentityTextureResource) else None

    @expected_byte_length.setter
    def expected_byte_length(self, value: int | None) -> None:
        if not isinstance(self._variant, _IdentityTextureResource):
            raise AttributeError("expected_byte_length is only valid for identity TextureResource")
        self._variant.expected_byte_length = value

    @property
    def expected_content_hash(self) -> bytes:
        return self._variant.expected_content_hash if isinstance(self._variant, _IdentityTextureResource) else b""

    @expected_content_hash.setter
    def expected_content_hash(self, value: bytes) -> None:
        if not isinstance(self._variant, _IdentityTextureResource):
            raise AttributeError("expected_content_hash is only valid for identity TextureResource")
        self._variant.expected_content_hash = bytes(value)

    @property
    def identity_embedded(self) -> bool:
        return self._variant.identity_embedded if isinstance(self._variant, _IdentityTextureResource) else False

    @identity_embedded.setter
    def identity_embedded(self, value: bool) -> None:
        if not isinstance(self._variant, _IdentityTextureResource):
            raise AttributeError("identity_embedded is only valid for identity TextureResource")
        self._variant.identity_embedded = value

    def _flattened(self) -> tuple[object, ...]:
        return tuple(getattr(self, name) for name in self.__match_args__)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TextureResource):
            return NotImplemented
        return self.kind == other.kind and self._flattened() == other._flattened()

    def __repr__(self) -> str:
        values = ", ".join(f"{name}={getattr(self, name)!r}" for name in self.__match_args__)
        return f"TextureResource(kind={self.kind.value!r}, {values})"

    @classmethod
    def from_parameter_archive(cls, ar: FArchiveReader) -> TextureResource:
        location_mode = ar.read_uint8()
        uri = ar.read_fstring()
        texture_id = ar.read_int()
        unreal_object_path = ar.read_fstring()
        return cls.parameter(
            id=texture_id,
            unreal_object_path=unreal_object_path,
            location_mode=location_mode,
            uri=uri,
            color_space=ar.read_uint8(),
            packing=ar.read_uint8(),
            uv_set=ar.read_int(),
        )

    @classmethod
    def read_all(
        cls,
        ar: FArchiveReader,
        array_size: int,
    ) -> tuple[list[TextureResource], FModelMaterialTextures | None]:
        """Read the FMODEL_TEXTURES singleton envelope.

        Identity v1/v2 stores its resource and binding counts inside the chunk
        payload. The top-level ``array_size`` belongs to the envelope (and is
        always 1 for this extension), while the legacy embedded-resource format
        uses the envelope count as its resource count. Keeping the two meanings
        separate is essential for multi-texture identity exports.
        """
        from .materials import FModelMaterialTextures

        start = ar.data.tell()
        try:
            return [], FModelMaterialTextures.from_archive(ar, array_size)
        except ValueError as identity_error:
            ar.data.seek(start, 0)
            if not cls._has_explicit_legacy_texture_prefix(ar, array_size):
                raise identity_error
            ar.data.seek(start, 0)
            return cls._read_legacy_resources(ar, array_size), None

    @classmethod
    def _has_explicit_legacy_texture_prefix(cls, ar: FArchiveReader, array_size: int) -> bool:
        start = ar.data.tell()
        try:
            version = ar.read_uint8()
            if version != 1:
                return False
            count = _read_non_negative_count(ar, "texture count")
            _validate_top_level_count(count, array_size, "texture")
            if count <= 0:
                return False
            texture_id = ar.read_int()
            if texture_id < 0:
                return False
            unreal_object_path = _read_strict_legacy_fstring(
                ar,
                MAX_FMODEL_IDENTITY_PATH_BYTES,
                "FModel legacy texture unreal_object_path",
            )
            if not unreal_object_path.startswith("/"):
                return False
            mime = _read_strict_legacy_fstring(
                ar,
                MAX_FMODEL_TEXTURE_ID_BYTES,
                "FModel legacy texture mime",
            )
            if "/" not in mime:
                return False
            byte_length = _read_non_negative_count(ar, "texture byte length")
            return byte_length <= ar.remaining()
        except ValueError:
            return False
        finally:
            ar.data.seek(start, 0)

    @classmethod
    def _read_legacy_resources(cls, ar: FArchiveReader, array_size: int) -> list[TextureResource]:
        version = ar.read_uint8()
        if version != 1:
            msg = f"Unsupported FModel texture extension version: {version}"
            raise ValueError(msg)
        count = _read_non_negative_count(ar, "texture count")
        _validate_top_level_count(count, array_size, "texture")
        _validate_max_count(count, MAX_FMODEL_TEXTURES, "texture count")
        if count > ar.remaining() // 16:
            raise ValueError("Invalid FModel texture count cannot fit minimum records")
        resources = ar.read_array(count, cls.from_archive)
        ids: set[int] = set()
        for resource in resources:
            if resource.id in ids:
                raise ValueError(f"Invalid FModel duplicate embedded texture id: {resource.id}")
            ids.add(resource.id)
        return resources

    @classmethod
    def from_archive(cls, ar: FArchiveReader) -> TextureResource:
        texture_id = ar.read_int()
        unreal_object_path = ar.read_fstring()
        mime = ar.read_fstring()
        byte_length = _read_non_negative_count(ar, "texture byte length")
        if byte_length > ar.remaining():
            msg = f"Truncated FModel texture data: expected {byte_length} bytes, got {ar.remaining()}"
            raise ValueError(msg)
        data = ar.read(byte_length)
        if len(data) != byte_length:
            msg = f"Truncated FModel texture data: expected {byte_length} bytes, got {len(data)}"
            raise ValueError(msg)
        return cls.embedded(
            id=texture_id,
            unreal_object_path=unreal_object_path,
            mime=mime,
            data=data,
        )
