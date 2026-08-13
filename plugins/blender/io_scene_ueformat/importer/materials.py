from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING

from .protocol import (
    FMODEL_PBR_INTERFACES_VERSION,
    MAX_FMODEL_IDENTITY_PATH_BYTES,
    MAX_FMODEL_MATERIAL_TEXTURE_BINDINGS,
    MAX_FMODEL_PARAMETERS,
    MAX_FMODEL_PBR_INTERFACES,
    MAX_FMODEL_SLOTS,
    MAX_FMODEL_TEXTURE_ID_BYTES,
    MAX_FMODEL_TEXTURES,
    _FModelTextureLegacyFormatMismatchError,
    _read_non_negative_count,
    _read_strict_utf8_string,
    _validate_fmodel_material_id,
    _validate_fmodel_identity_chunk_count,
    _validate_logical_resource_uri,
    _validate_max_count,
    _validate_top_level_count,
)
from .resources import TextureResource

_RELATIVE_LOCATION_MODE = 1
_EMBEDDED_LOCATION_MODE = 3
_EXPORT_ROOT_LOCATION_MODE = 4

if TYPE_CHECKING:
    from io_scene_ueformat.importer.reader import FArchiveReader


class FModelTextureMapKind(IntEnum):
    BaseColor = 0
    Normal = 1
    Orm = 2


@dataclass(frozen=True, slots=True)
class FModelMaterialTextureRecord:
    texture_id: str
    logical_resource_uri: str
    is_embedded: bool
    byte_length: int
    content_hash: bytes
    location_mode: int = _EXPORT_ROOT_LOCATION_MODE


@dataclass(frozen=True, slots=True)
class FModelMaterialTextureBinding:
    # Historical field name retained for source compatibility. The wire value is
    # MaterialId and cross-links to FMODEL_MATERIALS MaterialName/slot_name.
    material_path: str
    map_kind: FModelTextureMapKind
    texture_id: str
    is_srgb: bool

    @property
    def material_id(self) -> str:
        return self.material_path


@dataclass(frozen=True, slots=True)
class FModelMaterialTextures:
    version: int
    textures: tuple[FModelMaterialTextureRecord, ...]
    bindings: tuple[FModelMaterialTextureBinding, ...]

    @classmethod
    def from_archive(cls, ar: FArchiveReader, array_size: int) -> FModelMaterialTextures:
        # FMODEL_TEXTURES is a singleton top-level extension chunk. Its envelope
        # count is therefore always 1; the payload has its own texture_count and
        # binding_count fields. Do not compare the payload texture_count with the
        # envelope count: a model may legitimately contain multiple textures.
        _validate_fmodel_identity_chunk_count(array_size, "FMODEL_TEXTURES")
        version = ar.read_uint8()
        if version not in (1, 2):
            raise _FModelTextureLegacyFormatMismatchError(
                f"Unsupported FModel texture extension version: {version}"
            )
        texture_count = _read_non_negative_count(ar, "texture count")
        _validate_max_count(texture_count, MAX_FMODEL_TEXTURES, "texture count")
        if texture_count > ar.remaining() // (47 if version == 2 else 46):
            raise _FModelTextureLegacyFormatMismatchError(
                "Invalid FModel texture count cannot fit minimum records"
            )

        textures = tuple(
            FModelMaterialTextureRecord.from_archive(ar, version)
            for _ in range(texture_count)
        )
        texture_ids: set[str] = set()
        previous_texture_id: str | None = None
        for texture in textures:
            if texture.texture_id in texture_ids:
                raise ValueError(f"Invalid FModel duplicate texture id: {texture.texture_id}")
            if previous_texture_id is not None and texture.texture_id <= previous_texture_id:
                raise ValueError("Invalid FModel textures must be in canonical ascending StableId order")
            texture_ids.add(texture.texture_id)
            previous_texture_id = texture.texture_id

        binding_count = _read_non_negative_count(ar, "texture binding count")
        _validate_max_count(binding_count, MAX_FMODEL_MATERIAL_TEXTURE_BINDINGS, "texture binding count")
        if binding_count > ar.remaining() // 10:
            raise ValueError("Invalid FModel texture binding count cannot fit minimum records")

        bindings = tuple(FModelMaterialTextureBinding.from_archive(ar) for _ in range(binding_count))
        seen_pairs: set[tuple[str, FModelTextureMapKind]] = set()
        previous_sort_key: tuple[str, int] | None = None
        for binding in bindings:
            if binding.texture_id not in texture_ids:
                raise ValueError(f"Invalid FModel texture binding missing texture id: {binding.texture_id}")
            pair = (binding.material_path, binding.map_kind)
            if pair in seen_pairs:
                raise ValueError(
                    "Invalid FModel duplicate material/map binding: "
                    f"{binding.material_path} {binding.map_kind.name}"
                )
            seen_pairs.add(pair)
            sort_key = (binding.material_path, int(binding.map_kind))
            if previous_sort_key is not None and sort_key < previous_sort_key:
                raise ValueError("Invalid FModel texture bindings are not in canonical order")
            previous_sort_key = sort_key

        if not ar.eof():
            raise ValueError("Trailing FModel texture identity data")
        return cls(version, textures, bindings)


@classmethod
def _fmodel_material_texture_record_from_archive(
    cls,
    ar: FArchiveReader,
    extension_version: int = 1,
) -> FModelMaterialTextureRecord:
    texture_id = _read_strict_utf8_string(
        ar,
        MAX_FMODEL_TEXTURE_ID_BYTES,
        "FModel texture texture_id",
    )
    # The canonical C# writer/inspector order is:
    # StableId, LogicalResourceUri, [LocationMode in v2], IsEmbedded,
    # ByteLength, SHA-256. Keep this reader byte-for-byte aligned with
    # FModelTextureChunkWriter and FModelTextureChunkInspector; reading the
    # location byte before the URI silently corrupts all v2 records.
    logical_resource_uri = _read_strict_utf8_string(
        ar,
        MAX_FMODEL_IDENTITY_PATH_BYTES,
        "FModel texture logical_resource_uri",
    )
    location_mode = (
        ar.read_uint8()
        if extension_version >= 2
        else _EXPORT_ROOT_LOCATION_MODE
    )
    if location_mode not in (1, 2, 3, 4):
        raise ValueError(f"Invalid FModel texture location_mode: {location_mode}")
    logical_resource_uri = _validate_logical_resource_uri(
        logical_resource_uri,
        "FModel texture logical_resource_uri",
        allow_leading_parents=location_mode == _RELATIVE_LOCATION_MODE,
    )
    is_embedded = ar.read_uint8()
    if is_embedded not in (0, 1):
        raise ValueError(f"Invalid FModel texture is_embedded: {is_embedded}")
    if extension_version >= 2 and is_embedded and location_mode != _EMBEDDED_LOCATION_MODE:
        raise ValueError("Embedded FModel texture must use Embedded location mode")
    byte_length = _read_non_negative_count(ar, "texture byte length")
    content_hash = ar.read(32)
    return FModelMaterialTextureRecord(
        texture_id=texture_id,
        logical_resource_uri=logical_resource_uri,
        is_embedded=bool(is_embedded),
        byte_length=byte_length,
        content_hash=content_hash,
        location_mode=location_mode,
    )


FModelMaterialTextureRecord.from_archive = _fmodel_material_texture_record_from_archive  # type: ignore[attr-defined]


@classmethod
def _fmodel_material_texture_binding_from_archive(cls, ar: FArchiveReader) -> FModelMaterialTextureBinding:
    material_path = _validate_fmodel_material_id(
        _read_strict_utf8_string(
            ar,
            MAX_FMODEL_IDENTITY_PATH_BYTES,
            "FModel texture binding material_path/material_id",
        ),
        "FModel texture binding material_path/material_id",
    )
    map_kind_raw = ar.read_uint8()
    try:
        map_kind = FModelTextureMapKind(map_kind_raw)
    except ValueError as error:
        raise ValueError(f"Invalid FModel texture map kind: {map_kind_raw}") from error
    texture_id = _read_strict_utf8_string(
        ar,
        MAX_FMODEL_TEXTURE_ID_BYTES,
        "FModel texture binding texture_id",
    )
    is_srgb = ar.read_uint8()
    if is_srgb not in (0, 1):
        raise ValueError(f"Invalid FModel texture binding is_srgb: {is_srgb}")
    return FModelMaterialTextureBinding(
        material_path=material_path,
        map_kind=map_kind,
        texture_id=texture_id,
        is_srgb=bool(is_srgb),
    )


FModelMaterialTextureBinding.from_archive = _fmodel_material_texture_binding_from_archive  # type: ignore[attr-defined]


@dataclass(slots=True)
class FModelMaterialLinks:
    slots: list[FModelMaterialSlot] = field(default_factory=list)
    source_material_mapping_present: bool = False
    pbr_interfaces: list[FModelPBRInterface] = field(default_factory=list)
    pbr_interfaces_present: bool = False

    @property
    def source_slot_to_blender_index(self) -> dict[int, int]:
        """Map serialized source slots to the dense MATERIALS record order."""
        return {slot.slot_index: index for index, slot in enumerate(self.slots)}

    @classmethod
    def from_archive(cls, ar: FArchiveReader, array_size: int) -> FModelMaterialLinks:
        extension_version = ar.read_uint8()
        if extension_version not in (1, 2):
            msg = f"Unsupported FModel material extension version: {extension_version}"
            raise ValueError(msg)
        count = _read_non_negative_count(ar, "slot count")
        _validate_top_level_count(count, array_size, "slot")
        _validate_max_count(count, MAX_FMODEL_SLOTS, "slot count")
        if count > ar.remaining() // (24 if extension_version == 2 else 20):
            raise ValueError("Invalid FModel slot count cannot fit minimum records")
        return cls(
            slots=ar.read_array(count, lambda record: FModelMaterialSlot.from_archive(record, extension_version)),
            source_material_mapping_present=extension_version == 2,
        )


@dataclass(slots=True)
class FModelPBRInterface:
    slot_index: int
    parameter_index: int
    role: int

    @classmethod
    def read_all(cls, ar: FArchiveReader, array_size: int) -> list[FModelPBRInterface]:
        extension_version = ar.read_uint8()
        if extension_version != FMODEL_PBR_INTERFACES_VERSION:
            msg = f"Unsupported FModel PBR interface extension version: {extension_version}"
            raise ValueError(msg)
        count = _read_non_negative_count(ar, "PBR interface count")
        _validate_top_level_count(count, array_size, "PBR interface")
        _validate_max_count(count, MAX_FMODEL_PBR_INTERFACES, "PBR interface count")
        if count > ar.remaining() // 9:
            raise ValueError("Invalid FModel PBR interface count cannot fit minimum records")
        return ar.read_array(count, cls.from_archive)

    @classmethod
    def from_archive(cls, ar: FArchiveReader) -> FModelPBRInterface:
        slot_index = ar.read_int()
        parameter_index = ar.read_int()
        role = ar.read_uint8()
        if slot_index < 0:
            raise ValueError(f"Invalid FModel PBR interface slot_index: {slot_index}")
        if parameter_index < 0:
            raise ValueError(f"Invalid FModel PBR interface parameter_index: {parameter_index}")
        if role < 1 or role > 12:
            raise ValueError(f"Invalid FModel PBR role: {role}")
        return cls(slot_index, parameter_index, role)


@dataclass(slots=True)
class FModelMaterialSlot:
    slot_index: int
    slot_name: str
    unreal_material_path: str
    material_json_uri: str
    source_material_index: int | None = None
    parameters: list[FModelMaterialParameter] = field(default_factory=list)

    @classmethod
    def from_archive(cls, ar: FArchiveReader, extension_version: int = 1) -> FModelMaterialSlot:
        slot_index = ar.read_int()
        if slot_index < 0:
            raise ValueError(f"Invalid FModel material slot_index: {slot_index}")
        source_material_index = ar.read_int() if extension_version == 2 else None
        if source_material_index is not None and source_material_index < -1:
            raise ValueError(f"Invalid FModel material source_material_index: {source_material_index}")
        if source_material_index == -1:
            source_material_index = None
        slot_name = ar.read_fstring()
        unreal_material_path = ar.read_fstring()
        material_json_uri = ar.read_fstring()
        if material_json_uri:
            _validate_logical_resource_uri(
                material_json_uri,
                "FModel material material_json_uri",
                allow_leading_parents=True,
            )
        parameter_count = _read_non_negative_count(ar, "parameter count")
        _validate_max_count(parameter_count, MAX_FMODEL_PARAMETERS, "parameter count")
        if parameter_count > ar.remaining() // 13:
            raise ValueError("Invalid FModel parameter count cannot fit minimum records")
        return cls(
            slot_index=slot_index,
            source_material_index=source_material_index,
            slot_name=slot_name,
            unreal_material_path=unreal_material_path,
            material_json_uri=material_json_uri,
            parameters=ar.read_array(
                parameter_count,
                FModelMaterialParameter.from_archive,
            ),
        )


@dataclass(slots=True)
class FModelMaterialParameter:
    name: str
    kind: int
    semantic: str
    value: TextureResource | float | tuple[float, float, float, float]

    @classmethod
    def from_archive(cls, ar: FArchiveReader) -> FModelMaterialParameter:
        name = ar.read_fstring()
        kind = ar.read_uint8()
        semantic = ar.read_fstring()
        if kind == 1:
            value: TextureResource | float | tuple[float, float, float, float] = TextureResource.from_parameter_archive(ar)
        elif kind == 2:
            value = ar.read_float()
        elif kind == 3:
            value = tuple(ar.read_float_vector(4))
        else:
            msg = f"Invalid FModel material parameter kind: {kind}"
            raise ValueError(msg)
        return cls(name=name, kind=kind, semantic=semantic, value=value)

    @property
    def texture(self) -> TextureResource:
        assert isinstance(self.value, TextureResource)  # noqa: S101
        return self.value

    @property
    def location_mode(self) -> int:
        return self.texture.location_mode

    @property
    def uri(self) -> str:
        return self.texture.uri

    @property
    def texture_id(self) -> int:
        return self.texture.id

    @property
    def unreal_object_path(self) -> str:
        return self.texture.unreal_object_path

    @property
    def color_space(self) -> int:
        return self.texture.color_space

    @property
    def packing(self) -> int:
        return self.texture.packing

    @property
    def uv_set(self) -> int:
        return self.texture.uv_set
