from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from ...archive.reader import FArchiveReader
from .envelope import parse_envelope
from .legacy import FModelLegacyExtensionChunk


FMODEL_MATERIAL_EXTENSION_VERSIONS = (1, 2)
FMODEL_PBR_INTERFACES_VERSION = 2
MAX_FMODEL_SLOTS = 65_536
MAX_FMODEL_PARAMETERS = 65_536
MAX_FMODEL_TEXTURES = 65_536
MAX_FMODEL_TEXTURE_BINDINGS = 65_536
MAX_FMODEL_PBR_INTERFACES = 65_536
MAX_FMODEL_TEXT_BYTES = 16 * 1024
MAX_FMODEL_TEXTURE_ID_BYTES = 4 * 1024

_RELATIVE_LOCATION_MODE = 1
_EMBEDDED_LOCATION_MODE = 3
_EXPORT_ROOT_LOCATION_MODE = 4


class FModelMaterialDecodeError(ValueError):
    """Raised when a versioned FModel material payload is malformed."""


class FModelTextureMapKind(IntEnum):
    BASE_COLOR = 0
    NORMAL = 1
    ORM = 2


@dataclass(frozen=True, slots=True)
class FModelTextureParameter:
    location_mode: int
    uri: str
    texture_id: int
    unreal_object_path: str
    color_space: int
    packing: int
    uv_set: int


@dataclass(frozen=True, slots=True)
class FModelMaterialParameter:
    name: str
    kind: int
    semantic: str
    value: FModelTextureParameter | float | tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class FModelMaterialSlot:
    slot_index: int
    slot_name: str
    unreal_material_path: str
    material_json_uri: str
    source_material_index: int | None
    parameters: tuple[FModelMaterialParameter, ...]


@dataclass(frozen=True, slots=True)
class FModelMaterialLinksSnapshot:
    version: int
    slots: tuple[FModelMaterialSlot, ...]

    @property
    def source_material_mapping_present(self) -> bool:
        return self.version == 2


@dataclass(frozen=True, slots=True)
class FModelTextureRecord:
    texture_id: str
    logical_resource_uri: str
    is_embedded: bool
    byte_length: int
    content_hash: bytes
    location_mode: int


@dataclass(frozen=True, slots=True)
class FModelTextureBinding:
    material_id: str
    map_kind: FModelTextureMapKind
    texture_id: str
    is_srgb: bool


@dataclass(frozen=True, slots=True)
class FModelEmbeddedTextureRecord:
    texture_id: int
    unreal_object_path: str
    mime: str
    data: bytes


@dataclass(frozen=True, slots=True)
class FModelTextureSnapshot:
    version: int
    textures: tuple[FModelTextureRecord, ...]
    bindings: tuple[FModelTextureBinding, ...]
    embedded: tuple[FModelEmbeddedTextureRecord, ...] = ()
    legacy_embedded: bool = False

    @property
    def is_legacy_embedded(self) -> bool:
        return self.legacy_embedded


@dataclass(frozen=True, slots=True)
class FModelPBRInterface:
    slot_index: int
    parameter_index: int
    role: int


def _read_count(reader: FArchiveReader, name: str, maximum: int) -> int:
    count = reader.read_int()
    if count < 0 or count > maximum:
        raise FModelMaterialDecodeError(f"Invalid FModel {name}: {count}")
    return count


def _read_text(
    reader: FArchiveReader,
    name: str,
    *,
    maximum: int = MAX_FMODEL_TEXT_BYTES,
    allow_empty: bool = True,
) -> str:
    size = reader.read_int()
    if size < 0 or size > maximum:
        raise FModelMaterialDecodeError(f"Invalid FModel {name} length: {size}")
    raw = reader.read(size)
    if raw.endswith(b"\0"):
        raw = raw[:-1]
    if not allow_empty and not raw:
        raise FModelMaterialDecodeError(f"Invalid FModel {name}")
    if b"\0" in raw:
        raise FModelMaterialDecodeError(f"Invalid FModel {name} embedded NUL")
    try:
        value = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise FModelMaterialDecodeError(f"Invalid UTF-8 in FModel {name}") from error
    if value != value.strip() or any(
        ord(character) <= 0x1F or 0x7F <= ord(character) <= 0x9F
        for character in value
    ):
        raise FModelMaterialDecodeError(f"Invalid FModel {name}")
    return value


def _validate_unreal_path(value: str, name: str) -> str:
    if not value or not value.startswith("/") or value.startswith("//"):
        raise FModelMaterialDecodeError(f"Invalid FModel {name}")
    if "\\" in value or any(segment in ("", ".", "..") for segment in value.split("/")[1:]):
        raise FModelMaterialDecodeError(f"Invalid FModel {name}")
    return value


def _validate_resource_uri(value: str, name: str, *, allow_leading_parents: bool) -> str:
    if not value or value.startswith("/") or "\\" in value or ":" in value:
        raise FModelMaterialDecodeError(f"Invalid FModel {name}")
    seen_non_parent = False
    for segment in value.split("/"):
        if segment in ("", "."):
            raise FModelMaterialDecodeError(f"Invalid FModel {name}")
        if segment == "..":
            if not allow_leading_parents or seen_non_parent:
                raise FModelMaterialDecodeError(f"Invalid FModel {name}")
        else:
            seen_non_parent = True
    if value.split("/")[-1] == "..":
        raise FModelMaterialDecodeError(f"Invalid FModel {name}")
    return value


def _validate_material_id(value: str, name: str) -> str:
    if not value or value.isspace() or value != value.strip() or "\0" in value:
        raise FModelMaterialDecodeError(f"Invalid FModel {name}")
    if value.startswith("/"):
        return _validate_unreal_path(value, name)
    if "/" in value or "\\" in value or ":" in value:
        raise FModelMaterialDecodeError(f"Invalid FModel {name}")
    return value


def _read_parameter_texture(reader: FArchiveReader) -> FModelTextureParameter:
    location_mode = int.from_bytes(reader.read_byte(), byteorder="little")
    if location_mode not in (1, 2, 3, 4):
        raise FModelMaterialDecodeError(f"Invalid FModel texture location_mode: {location_mode}")
    uri = _read_text(reader, "parameter texture uri")
    if uri:
        _validate_resource_uri(
            uri,
            "parameter texture uri",
            allow_leading_parents=location_mode == _RELATIVE_LOCATION_MODE,
        )
    texture_id = reader.read_int()
    unreal_object_path = _read_text(reader, "parameter texture Unreal object path")
    color_space = int.from_bytes(reader.read_byte(), byteorder="little")
    packing = int.from_bytes(reader.read_byte(), byteorder="little")
    uv_set = reader.read_int()
    return FModelTextureParameter(
        location_mode,
        uri,
        texture_id,
        unreal_object_path,
        color_space,
        packing,
        uv_set,
    )


def _read_material_parameter(reader: FArchiveReader) -> FModelMaterialParameter:
    name = _read_text(reader, "material parameter name")
    kind = int.from_bytes(reader.read_byte(), byteorder="little")
    semantic = _read_text(reader, "material parameter semantic")
    if kind == 1:
        value: FModelTextureParameter | float | tuple[float, float, float, float] = _read_parameter_texture(reader)
    elif kind == 2:
        value = reader.read_float()
    elif kind == 3:
        value = tuple(float(component) for component in reader.read_float_vector(4))
    else:
        raise FModelMaterialDecodeError(f"Invalid FModel material parameter kind: {kind}")
    return FModelMaterialParameter(name, kind, semantic, value)


def _read_material_slot(reader: FArchiveReader, version: int) -> FModelMaterialSlot:
    slot_index = reader.read_int()
    if slot_index < 0:
        raise FModelMaterialDecodeError(f"Invalid FModel material slot_index: {slot_index}")
    source_material_index = reader.read_int() if version == 2 else None
    if source_material_index is not None and source_material_index < -1:
        raise FModelMaterialDecodeError(
            f"Invalid FModel material source_material_index: {source_material_index}",
        )
    if source_material_index == -1:
        source_material_index = None
    slot_name = _read_text(reader, "material slot name")
    material_path = _read_text(reader, "material Unreal path")
    material_json_uri = _read_text(reader, "material JSON URI")
    if material_json_uri:
        _validate_resource_uri(
            material_json_uri,
            "material JSON URI",
            allow_leading_parents=True,
        )
    parameter_count = _read_count(reader, "parameter count", MAX_FMODEL_PARAMETERS)
    parameters = tuple(_read_material_parameter(reader) for _ in range(parameter_count))
    return FModelMaterialSlot(
        slot_index,
        slot_name,
        material_path,
        material_json_uri,
        source_material_index,
        parameters,
    )


def decode_material_links_payload(
    payload: bytes,
    *,
    array_size: int,
) -> FModelMaterialLinksSnapshot:
    reader = FArchiveReader(payload)
    version = int.from_bytes(reader.read_byte(), byteorder="little")
    if version not in FMODEL_MATERIAL_EXTENSION_VERSIONS:
        raise FModelMaterialDecodeError(f"Unsupported FModel material extension version: {version}")
    count = _read_count(reader, "slot count", MAX_FMODEL_SLOTS)
    if count != array_size:
        raise FModelMaterialDecodeError(
            f"FModel material slot count {count} does not match V9 ArraySize {array_size}",
        )
    slots = tuple(_read_material_slot(reader, version) for _ in range(count))
    if not reader.eof():
        raise FModelMaterialDecodeError("Trailing FModel material link data")
    return FModelMaterialLinksSnapshot(version, slots)


def _read_texture_record(reader: FArchiveReader, version: int) -> FModelTextureRecord:
    texture_id = _read_text(
        reader,
        "texture stable id",
        maximum=MAX_FMODEL_TEXTURE_ID_BYTES,
        allow_empty=False,
    )
    uri = _read_text(reader, "texture resource URI", maximum=MAX_FMODEL_TEXT_BYTES, allow_empty=False)
    location_mode = (
        int.from_bytes(reader.read_byte(), byteorder="little")
        if version >= 2
        else _EXPORT_ROOT_LOCATION_MODE
    )
    if location_mode not in (1, 2, 3, 4):
        raise FModelMaterialDecodeError(f"Invalid FModel texture location_mode: {location_mode}")
    _validate_resource_uri(
        uri,
        "texture resource URI",
        allow_leading_parents=location_mode == _RELATIVE_LOCATION_MODE,
    )
    is_embedded = int.from_bytes(reader.read_byte(), byteorder="little")
    if is_embedded not in (0, 1):
        raise FModelMaterialDecodeError(f"Invalid FModel texture is_embedded: {is_embedded}")
    if version >= 2 and is_embedded and location_mode != _EMBEDDED_LOCATION_MODE:
        raise FModelMaterialDecodeError("Embedded FModel texture must use Embedded location mode")
    byte_length = reader.read_int()
    if byte_length < 0:
        raise FModelMaterialDecodeError(f"Invalid FModel texture byte length: {byte_length}")
    return FModelTextureRecord(
        texture_id,
        uri,
        bool(is_embedded),
        byte_length,
        reader.read(32),
        location_mode,
    )


def decode_texture_payload(
    payload: bytes,
    *,
    array_size: int,
) -> FModelTextureSnapshot:
    reader = FArchiveReader(payload)
    version = int.from_bytes(reader.read_byte(), byteorder="little")
    if version not in FMODEL_MATERIAL_EXTENSION_VERSIONS:
        raise FModelMaterialDecodeError(f"Unsupported FModel texture extension version: {version}")
    if array_size != 1:
        return _decode_legacy_embedded_texture_payload(reader, version=version, array_size=array_size)
    start = reader.data.tell()
    try:
        return _decode_identity_texture_payload(reader, version=version)
    except FModelMaterialDecodeError as identity_error:
        if version != 1:
            raise identity_error
        reader.data.seek(start, 0)
        try:
            return _decode_legacy_embedded_texture_payload(reader, version=version, array_size=array_size)
        except FModelMaterialDecodeError:
            raise identity_error


def _decode_identity_texture_payload(
    reader: FArchiveReader,
    *,
    version: int,
) -> FModelTextureSnapshot:
    texture_count = _read_count(reader, "texture count", MAX_FMODEL_TEXTURES)
    textures = tuple(_read_texture_record(reader, version) for _ in range(texture_count))
    texture_ids = [texture.texture_id for texture in textures]
    if len(set(texture_ids)) != len(texture_ids) or texture_ids != sorted(texture_ids):
        raise FModelMaterialDecodeError("FModel textures must have unique ascending StableIds")

    binding_count = _read_count(reader, "texture binding count", MAX_FMODEL_TEXTURE_BINDINGS)
    bindings: list[FModelTextureBinding] = []
    known_ids = set(texture_ids)
    for _ in range(binding_count):
        material_id = _validate_material_id(_read_text(reader, "texture binding material id", allow_empty=False), "texture binding material id")
        map_kind_raw = int.from_bytes(reader.read_byte(), byteorder="little")
        try:
            map_kind = FModelTextureMapKind(map_kind_raw)
        except ValueError as error:
            raise FModelMaterialDecodeError(f"Invalid FModel texture map kind: {map_kind_raw}") from error
        texture_id = _read_text(reader, "texture binding texture id", maximum=MAX_FMODEL_TEXTURE_ID_BYTES, allow_empty=False)
        if texture_id not in known_ids:
            raise FModelMaterialDecodeError(f"FModel texture binding references missing texture id: {texture_id}")
        is_srgb = int.from_bytes(reader.read_byte(), byteorder="little")
        if is_srgb not in (0, 1):
            raise FModelMaterialDecodeError(f"Invalid FModel texture binding is_srgb: {is_srgb}")
        bindings.append(FModelTextureBinding(material_id, map_kind, texture_id, bool(is_srgb)))
    binding_keys = [(binding.material_id, int(binding.map_kind)) for binding in bindings]
    if len(set(binding_keys)) != len(binding_keys) or binding_keys != sorted(binding_keys):
        raise FModelMaterialDecodeError("FModel texture bindings must be unique and sorted")
    if not reader.eof():
        raise FModelMaterialDecodeError("Trailing FModel texture identity data")
    return FModelTextureSnapshot(version, textures, tuple(bindings))


def _decode_legacy_embedded_texture_payload(
    reader: FArchiveReader,
    *,
    version: int,
    array_size: int,
) -> FModelTextureSnapshot:
    if version != 1:
        raise FModelMaterialDecodeError(f"Unsupported FModel legacy texture extension version: {version}")
    count = _read_count(reader, "legacy texture count", MAX_FMODEL_TEXTURES)
    if count != array_size:
        raise FModelMaterialDecodeError(
            f"FModel legacy texture count {count} does not match V9 ArraySize {array_size}",
        )
    records: list[FModelEmbeddedTextureRecord] = []
    ids: set[int] = set()
    for _ in range(count):
        texture_id = reader.read_int()
        if texture_id < 0 or texture_id in ids:
            raise FModelMaterialDecodeError(f"Invalid FModel legacy embedded texture id: {texture_id}")
        ids.add(texture_id)
        object_path = _read_text(reader, "legacy texture Unreal object path", allow_empty=False)
        _validate_unreal_path(object_path, "legacy texture Unreal object path")
        mime = _read_text(reader, "legacy texture MIME", allow_empty=False)
        if "/" not in mime:
            raise FModelMaterialDecodeError("Invalid FModel legacy texture MIME")
        byte_length = reader.read_int()
        if byte_length < 0 or byte_length > reader.remaining():
            raise FModelMaterialDecodeError("Truncated FModel legacy texture data")
        records.append(FModelEmbeddedTextureRecord(texture_id, object_path, mime, reader.read(byte_length)))
    if not reader.eof():
        raise FModelMaterialDecodeError("Trailing FModel legacy texture data")
    return FModelTextureSnapshot(version, (), (), tuple(records), True)


def decode_pbr_payload(payload: bytes, *, array_size: int) -> tuple[FModelPBRInterface, ...]:
    reader = FArchiveReader(payload)
    version = int.from_bytes(reader.read_byte(), byteorder="little")
    if version != FMODEL_PBR_INTERFACES_VERSION:
        raise FModelMaterialDecodeError(f"Unsupported FModel PBR interface extension version: {version}")
    count = _read_count(reader, "PBR interface count", MAX_FMODEL_PBR_INTERFACES)
    if count != array_size:
        raise FModelMaterialDecodeError(
            f"FModel PBR interface count {count} does not match V9 ArraySize {array_size}",
        )
    interfaces: list[FModelPBRInterface] = []
    for _ in range(count):
        slot_index = reader.read_int()
        parameter_index = reader.read_int()
        role = int.from_bytes(reader.read_byte(), byteorder="little")
        if slot_index < 0 or parameter_index < 0 or not 1 <= role <= 12:
            raise FModelMaterialDecodeError("Invalid FModel PBR interface record")
        interfaces.append(FModelPBRInterface(slot_index, parameter_index, role))
    if not reader.eof():
        raise FModelMaterialDecodeError("Trailing FModel PBR interface data")
    return tuple(interfaces)


def decode_legacy_material_links(chunk: FModelLegacyExtensionChunk) -> FModelMaterialLinksSnapshot:
    return decode_material_links_payload(chunk.payload, array_size=chunk.array_size)


def decode_legacy_textures(chunk: FModelLegacyExtensionChunk) -> FModelTextureSnapshot:
    return decode_texture_payload(chunk.payload, array_size=chunk.array_size)


def decode_legacy_pbr(chunk: FModelLegacyExtensionChunk) -> tuple[FModelPBRInterface, ...]:
    return decode_pbr_payload(chunk.payload, array_size=chunk.array_size)


def decode_v10_material_links(payload: bytes) -> FModelMaterialLinksSnapshot:
    envelope = parse_envelope(payload)
    return decode_material_links_payload(envelope.payload, array_size=envelope.array_size)


def decode_v10_textures(payload: bytes) -> FModelTextureSnapshot:
    envelope = parse_envelope(payload)
    return decode_texture_payload(envelope.payload, array_size=envelope.array_size)


def decode_v10_pbr(payload: bytes) -> tuple[FModelPBRInterface, ...]:
    envelope = parse_envelope(payload)
    return decode_pbr_payload(envelope.payload, array_size=envelope.array_size)
