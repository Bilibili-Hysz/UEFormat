from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from ...archive.reader import FArchiveReader
from .attributes import MAX_EXTENSION_ATTRIBUTE_BYTES
from .contract import (
    ANIMATION_ATTRIBUTES,
    MODEL_ATTRIBUTES,
    POSE_ATTRIBUTES,
    FModelV10Attribute,
    FModelV10ExtensionSnapshot,
    FModelV10FileKind,
)


MAX_LEGACY_CHUNK_COUNT = 4096


class FModelLegacyChunkError(ValueError):
    """Raised when a V9 chunk stream cannot be scanned safely."""


@dataclass(frozen=True, slots=True)
class FModelLegacyExtensionChunk:
    """A validated V9 chunk payload retained before semantic decoding."""

    array_size: int
    payload: bytes


@dataclass(frozen=True, slots=True)
class FModelLegacyExtensionSnapshot:
    file_kind: FModelV10FileKind
    attributes: Mapping[str, FModelLegacyExtensionChunk]
    unknown_fmodel_attributes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "attributes", MappingProxyType(dict(self.attributes)))

    @property
    def present(self) -> bool:
        return bool(self.attributes)

    def get(self, attribute: FModelV10Attribute) -> FModelLegacyExtensionChunk | None:
        return self.attributes.get(attribute.value)


def _allowed_attributes(file_kind: FModelV10FileKind) -> frozenset[str]:
    if file_kind is FModelV10FileKind.MODEL:
        return frozenset(attribute.value for attribute in MODEL_ATTRIBUTES)
    if file_kind is FModelV10FileKind.ANIM:
        return frozenset(attribute.value for attribute in ANIMATION_ATTRIBUTES)
    if file_kind is FModelV10FileKind.POSE:
        return frozenset(attribute.value for attribute in POSE_ATTRIBUTES)
    raise ValueError(f"unsupported FModel legacy file kind: {file_kind}")


def collect_fmodel_legacy_extensions(
    file_kind: FModelV10FileKind,
    body: bytes,
) -> FModelLegacyExtensionSnapshot:
    """Scan V9 ``Name + ArraySize + ByteSize + Payload`` chunks.

    This adapter intentionally retains only recognized FModel payloads. The
    V9 standard parser remains responsible for standard DTOs, and unknown
    chunks are skipped without constructing material or filesystem objects.
    The returned records are not yet a V10 producer contract.
    """

    allowed = _allowed_attributes(file_kind)
    values: dict[str, FModelLegacyExtensionChunk] = {}
    unknown: list[str] = []
    reader = FArchiveReader(body)
    chunk_count = 0

    while not reader.eof():
        chunk_count += 1
        if chunk_count > MAX_LEGACY_CHUNK_COUNT:
            raise FModelLegacyChunkError(
                f"V9 chunk count exceeds maximum {MAX_LEGACY_CHUNK_COUNT}",
            )

        name = reader.read_fstring()
        array_size = reader.read_int()
        byte_size = reader.read_int()
        if array_size < 0:
            raise FModelLegacyChunkError(f"negative V9 array size for {name}: {array_size}")
        if byte_size < 0:
            raise FModelLegacyChunkError(f"negative V9 byte size for {name}: {byte_size}")

        if name not in allowed:
            if name.startswith("FMODEL_") and name not in allowed:
                unknown.append(name)
            reader.skip(byte_size)
            continue

        if name in values:
            raise FModelLegacyChunkError(f"duplicate V9 FModel extension chunk: {name}")
        if byte_size > MAX_EXTENSION_ATTRIBUTE_BYTES:
            raise FModelLegacyChunkError(
                f"V9 FModel extension chunk {name} exceeds {MAX_EXTENSION_ATTRIBUTE_BYTES} bytes",
            )
        values[name] = FModelLegacyExtensionChunk(array_size, reader.read(byte_size))

    return FModelLegacyExtensionSnapshot(file_kind, values, tuple(sorted(set(unknown))))


def promote_legacy_snapshot_to_v10_shape(
    snapshot: FModelLegacyExtensionSnapshot,
) -> FModelV10ExtensionSnapshot:
    """Expose only payload bytes for adapters that do not use V9 ArraySize.

    This helper is deliberately not used by the importer. Most V9 FModel
    chunks require their singleton/count semantics to be validated before a
    V10 attribute can be emitted, so promotion remains an explicit adapter
    operation rather than an implicit compatibility claim.
    """

    return FModelV10ExtensionSnapshot(
        snapshot.file_kind,
        {name: chunk.payload for name, chunk in snapshot.attributes.items()},
        snapshot.unknown_fmodel_attributes,
    )
