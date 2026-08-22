from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ...archive.reader import FArchiveReader
from .envelope import parse_envelope
from .legacy import FModelLegacyExtensionChunk


MAX_IDENTITY_PATH_BYTES = 16 * 1024
IdentitySourceKind = Literal["animation", "pose"]


@dataclass(frozen=True, slots=True)
class FModelIdentityRecord:
    version: int
    skeleton_path: str
    skeleton_guid: bytes | None
    bone_layout_hash: bytes
    source_path: str | None = None


def _read_identity_path(reader: FArchiveReader, field_name: str) -> str:
    size = reader.read_int()
    if size <= 0 or size > MAX_IDENTITY_PATH_BYTES:
        raise ValueError(f"Invalid {field_name} length: {size}")
    raw = reader.read(size)
    if raw.endswith(b"\0"):
        raw = raw[:-1]
    if not raw or b"\0" in raw:
        raise ValueError(f"Invalid {field_name}")
    try:
        value = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError(f"Invalid UTF-8 in {field_name}") from error
    if (
        not value
        or value.isspace()
        or value != value.strip()
        or any(ord(character) <= 0x1F or 0x7F <= ord(character) <= 0x9F for character in value)
        or not value.startswith("/")
        or value.startswith("//")
        or "\\" in value
        or (len(value) >= 3 and value[1] == ":" and value[2] == "/")
        or any(segment in ("", ".", "..") for segment in value.split("/")[1:])
    ):
        raise ValueError(f"Invalid {field_name}")
    return value


def decode_identity_payload(
    payload: bytes,
    *,
    source_kind: IdentitySourceKind | None = None,
) -> FModelIdentityRecord:
    """Decode the shared V1 identity payload used by the V9 adapter.

    A future V10 producer may reuse this semantic payload, but doing so is not
    implied by this decoder; the V10 attribute name, version and envelope must
    still be specified independently.
    """

    reader = FArchiveReader(payload)
    version = int.from_bytes(reader.read_byte(), byteorder="little")
    if version != 1:
        raise ValueError(f"Unsupported FModel skeleton identity version: {version}")
    source_path = None
    if source_kind is not None:
        source_path = _read_identity_path(reader, f"FModel target {source_kind} path")
    skeleton_path = _read_identity_path(reader, "FModel skeleton identity skeleton path")
    has_guid = int.from_bytes(reader.read_byte(), byteorder="little")
    if has_guid not in (0, 1):
        raise ValueError(f"Invalid FModel skeleton identity has_skeleton_guid: {has_guid}")
    skeleton_guid = reader.read(16) if has_guid else None
    bone_layout_hash = reader.read(32)
    if not reader.eof():
        raise ValueError("Trailing FModel skeleton identity data")
    return FModelIdentityRecord(version, skeleton_path, skeleton_guid, bone_layout_hash, source_path)


def decode_legacy_identity_chunk(
    chunk: FModelLegacyExtensionChunk,
    *,
    source_kind: IdentitySourceKind | None = None,
) -> FModelIdentityRecord:
    if chunk.array_size != 1:
        raise ValueError(f"FModel identity chunk array_size must be 1: {chunk.array_size}")
    return decode_identity_payload(chunk.payload, source_kind=source_kind)


def decode_v10_identity_chunk(
    payload: bytes,
    *,
    source_kind: IdentitySourceKind | None = None,
) -> FModelIdentityRecord:
    """Decode the frozen V10 envelope around the shared identity payload."""

    envelope = parse_envelope(payload)
    if envelope.array_size != 1:
        raise ValueError(f"FModel V10 identity array_size must be 1: {envelope.array_size}")
    return decode_identity_payload(envelope.payload, source_kind=source_kind)
