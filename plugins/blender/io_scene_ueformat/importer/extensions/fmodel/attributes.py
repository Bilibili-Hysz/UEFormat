from __future__ import annotations

from dataclasses import dataclass
import struct


MAX_ATTRIBUTE_COUNT = 4096
MAX_ATTRIBUTE_NAME_BYTES = 1024
MAX_EXTENSION_ATTRIBUTE_BYTES = 64 * 1024 * 1024


class FModelV10AttributeError(ValueError):
    """Raised when the V10 attribute envelope is malformed."""


@dataclass(frozen=True, slots=True)
class AttributeSpan:
    name: str
    payload_offset: int
    payload_size: int


def _read_i32(data: bytes, offset: int, field: str) -> tuple[int, int]:
    end = offset + 4
    if end > len(data):
        raise FModelV10AttributeError(f"truncated V10 attribute {field}")
    return struct.unpack_from("<i", data, offset)[0], end


def _read_fstring(data: bytes, offset: int, field: str) -> tuple[str, int]:
    size, offset = _read_i32(data, offset, f"{field} length")
    if size <= 0:
        raise FModelV10AttributeError(f"invalid V10 attribute {field} length: {size}")
    if size > MAX_ATTRIBUTE_NAME_BYTES:
        raise FModelV10AttributeError(f"V10 attribute {field} exceeds {MAX_ATTRIBUTE_NAME_BYTES} bytes")
    end = offset + size
    if end > len(data):
        raise FModelV10AttributeError(f"truncated V10 attribute {field}")
    raw = data[offset:end]
    if raw.endswith(b"\0"):
        raw = raw[:-1]
    if not raw or b"\0" in raw:
        raise FModelV10AttributeError(f"invalid V10 attribute {field}")
    try:
        value = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise FModelV10AttributeError(f"invalid UTF-8 in V10 attribute {field}") from error
    if not value or value.isspace() or value != value.strip():
        raise FModelV10AttributeError(f"invalid V10 attribute {field}")
    return value, end


def scan_attribute_spans(data: bytes) -> tuple[AttributeSpan, ...]:
    """Scan V10 attributes without copying standard LOD payloads.

    The returned offsets point into the caller-owned immutable byte buffer.
    Unknown attributes are intentionally represented as spans and can be
    skipped by callers. Duplicate handling is deferred to the extension
    router because standard and additive namespaces have different policies.
    """

    count, offset = _read_i32(data, 0, "count")
    if count < 0 or count > MAX_ATTRIBUTE_COUNT:
        raise FModelV10AttributeError(f"invalid V10 attribute count: {count}")

    spans: list[AttributeSpan] = []
    for index in range(count):
        name, offset = _read_fstring(data, offset, f"name[{index}]")
        byte_size, offset = _read_i32(data, offset, f"byte_size[{index}]")
        if byte_size < 0:
            raise FModelV10AttributeError(f"negative V10 attribute byte size for {name}")
        end = offset + byte_size
        if end > len(data):
            raise FModelV10AttributeError(f"V10 attribute {name} exceeds body size")
        spans.append(AttributeSpan(name, offset, byte_size))
        offset = end

    if offset != len(data):
        raise FModelV10AttributeError("trailing bytes after V10 attribute envelope")
    return tuple(spans)


def payload_for_span(data: bytes, span: AttributeSpan) -> bytes:
    if span.payload_size > MAX_EXTENSION_ATTRIBUTE_BYTES:
        raise FModelV10AttributeError(
            f"V10 extension attribute {span.name} exceeds {MAX_EXTENSION_ATTRIBUTE_BYTES} bytes",
        )
    return data[span.payload_offset:span.payload_offset + span.payload_size]

