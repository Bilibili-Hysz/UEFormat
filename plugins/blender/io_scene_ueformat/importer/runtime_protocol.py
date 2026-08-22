from __future__ import annotations

from enum import IntEnum, auto
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from io_scene_ueformat.importer.reader import FArchiveReader


MAGIC = "UEFORMAT"
MODEL_IDENTIFIER = "UEMODEL"
ANIM_IDENTIFIER = "UEANIM"
POSE_IDENTIFIER = "UEPOSE"


class EUEFormatVersion(IntEnum):
    BeforeCustomVersionWasAdded = 0
    SerializeBinormalSign = 1
    AddMultipleVertexColors = 2
    AddConvexCollisionGeom = 3
    LevelOfDetailFormatRestructure = 4
    SerializeVirtualBones = 5
    SerializeMaterialPath = 6
    SerializeAssetMetadata = 7
    PreserveOriginalTransforms = 8
    AddPoseExport = 9

    VersionPlusOne = auto()
    LatestVersion = VersionPlusOne - 1


def _read_non_negative_count(ar: FArchiveReader, name: str) -> int:
    count = ar.read_int()
    if count < 0:
        msg = f"Invalid FModel {name}: {count}"
        raise ValueError(msg)
    return count


FMODEL_PBR_INTERFACES_VERSION = 2


# Stable per-list safety ceilings: 65,536 remains far above normal exports
# while preventing attacker-controlled extension counts from driving allocations.
MAX_FMODEL_SLOTS = 65_536
MAX_FMODEL_PARAMETERS = 65_536
MAX_FMODEL_TEXTURES = 65_536
MAX_FMODEL_PBR_INTERFACES = 65_536
MAX_FMODEL_IDENTITY_PATH_BYTES = 16 * 1024
MAX_FMODEL_TEXTURE_ID_BYTES = 4 * 1024
MAX_FMODEL_MATERIAL_TEXTURE_BINDINGS = 65_536


class _FModelTextureLegacyFormatMismatchError(ValueError):
    """Raised only when an FMODEL_TEXTURES payload is clearly not identity-v1."""


def _validate_max_count(count: int, maximum: int, name: str) -> None:
    if count > maximum:
        raise ValueError(f"Invalid FModel {name} exceeds maximum {maximum}: {count}")


def _validate_top_level_count(count: int, array_size: int, name: str) -> None:
    if count != array_size:
        raise ValueError(
            f"Invalid FModel {name} count {count} does not match top-level array_size {array_size}"
        )


def _validate_fmodel_identity_chunk_count(array_size: int, section_name: str) -> None:
    if array_size != 1:
        raise ValueError(f"Invalid {section_name} chunk array_size: {array_size}")


def _read_strict_identity_fstring(ar: FArchiveReader, maximum_size: int, description: str) -> str:
    """Read UEFormat's length-prefixed UTF-8 FString safely.

    CUE4Parse's canonical writer stores the exact UTF-8 byte count without a
    terminator. A trailing NUL from older hand-built fixtures is accepted, but
    embedded NULs, empty/whitespace values and malformed UTF-8 remain invalid.
    """
    size = int.from_bytes(ar.read(4), byteorder="little", signed=True)
    if size <= 0:
        raise ValueError(f"Invalid {description} length: {size}")
    if size > maximum_size:
        raise ValueError(f"Invalid {description} length exceeds maximum {maximum_size}: {size}")
    raw = ar.read(size)
    if raw.endswith(b"\0"):
        raw = raw[:-1]
    if not raw:
        raise ValueError(f"Invalid {description}")
    if b"\0" in raw:
        raise ValueError(f"{description} contains embedded NUL")
    try:
        value = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError(f"{description} contains invalid UTF-8") from error
    if value.isspace() or value != value.strip():
        raise ValueError(f"Invalid {description}")
    return value


def _read_strict_utf8_string(ar: FArchiveReader, maximum_size: int, description: str) -> str:
    size = int.from_bytes(ar.read(4), byteorder="little", signed=True)
    if size < 0:
        raise ValueError(f"Invalid {description} length: {size}")
    if size > maximum_size:
        raise ValueError(f"Invalid {description} length exceeds maximum {maximum_size}: {size}")
    raw = ar.read(size)
    if b"\0" in raw:
        raise ValueError(f"{description} contains embedded NUL")
    try:
        value = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError(f"{description} contains invalid UTF-8") from error
    if not value or value.isspace() or value != value.strip():
        raise ValueError(f"Invalid {description}")
    return value


def _read_strict_legacy_fstring(ar: FArchiveReader, maximum_size: int, description: str) -> str:
    """Read the historical FArchiveWriter FString used by embedded texture v1.

    Older fixtures encode the byte count without a trailing NUL, while some
    producers include one. Both forms are valid for this legacy-only probe.
    """
    size = int.from_bytes(ar.read(4), byteorder="little", signed=True)
    if size <= 0:
        raise ValueError(f"Invalid {description} length: {size}")
    if size > maximum_size:
        raise ValueError(f"Invalid {description} length exceeds maximum {maximum_size}: {size}")
    raw = ar.read(size)
    if raw.endswith(b"\0"):
        raw = raw[:-1]
    if not raw or b"\0" in raw:
        raise ValueError(f"Invalid {description}")
    try:
        value = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError(f"{description} contains invalid UTF-8") from error
    if value.isspace() or value != value.strip():
        raise ValueError(f"Invalid {description}")
    return value


def _validate_logical_unreal_path(path: str, field_name: str) -> str:
    if not path or path.isspace() or path != path.strip():
        raise ValueError(f"Invalid {field_name}")
    if "\0" in path:
        raise ValueError(f"Invalid {field_name}")
    if path.startswith("//"):
        raise ValueError(f"Invalid {field_name}")
    if "\\" in path:
        raise ValueError(f"Invalid {field_name}")
    if len(path) >= 3 and path[1] == ":" and path[2] == "/" and path[0].isalpha():
        raise ValueError(f"Invalid {field_name}")
    if path.casefold().startswith("file:"):
        raise ValueError(f"Invalid {field_name}")
    if not path.startswith("/"):
        raise ValueError(f"Invalid {field_name}")

    for segment in path.split("/")[1:]:
        if segment in ("", ".", ".."):
            raise ValueError(f"Invalid {field_name}")

    return path


def _normalize_logical_unreal_path(path: str, field_name: str) -> str:
    """Canonicalize an upstream logical Unreal path for identity comparison.

    The standard UEFormat ``SKELETON/METADATA`` field is written by upstream
    serializers and may be project-relative (for example
    ``CodeWorld/Content/...``), while the FModel identity extension is always
    rooted (``/CodeWorld/Content/...``).  This is a representation difference,
    not a filesystem fallback: validate the same path rules after adding the
    single logical root marker.
    """
    if not path.startswith("/"):
        path = "/" + path
    return _validate_logical_unreal_path(path, field_name)


def _validate_fmodel_material_id(value: str, field_name: str) -> str:
    if not value or value.isspace() or value != value.strip() or "\0" in value:
        raise ValueError(f"Invalid {field_name}")
    if value.startswith("/"):
        return _validate_logical_unreal_path(value, field_name)
    if "/" in value or "\\" in value or ":" in value:
        raise ValueError(f"Invalid {field_name}")
    return value


def _validate_logical_resource_uri(
    uri: str,
    field_name: str,
    *,
    allow_leading_parents: bool = False,
) -> str:
    if not uri or uri.isspace() or uri != uri.strip():
        raise ValueError(f"Invalid {field_name}")
    # Match .NET char.IsControl: reject C0 and C1 control characters
    # anywhere in the logical URI, not only NUL or boundary whitespace.
    if any(ord(character) <= 0x1F or 0x7F <= ord(character) <= 0x9F for character in uri):
        raise ValueError(f"Invalid {field_name}")
    if uri.startswith("/"):
        raise ValueError(f"Invalid {field_name}")
    if "\\" in uri:
        raise ValueError(f"Invalid {field_name}")
    if ":" in uri:
        raise ValueError(f"Invalid {field_name}")

    seen_non_parent = False
    segments = uri.split("/")
    for segment in segments:
        if segment in ("", "."):
            raise ValueError(f"Invalid {field_name}")
        if segment == "..":
            if not allow_leading_parents or seen_non_parent:
                raise ValueError(f"Invalid {field_name}")
            continue
        seen_non_parent = True

    if segments[-1] == "..":
        raise ValueError(f"Invalid {field_name}")
    return uri
