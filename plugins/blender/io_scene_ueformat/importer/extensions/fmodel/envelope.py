from __future__ import annotations

from dataclasses import dataclass
import struct


ENVELOPE_VERSION = 1
ENVELOPE_HEADER_SIZE = 5
MAX_ENVELOPE_PAYLOAD_BYTES = 64 * 1024 * 1024
MAX_ENVELOPE_ARRAY_SIZE = 1_000_000


@dataclass(frozen=True, slots=True)
class FModelV10Envelope:
    """Internal adapter envelope around a versioned FModel payload.

    V9 chunks carried ``array_size`` outside their payload. V10 attributes do
    not. The adapter therefore keeps that value explicit and leaves existing
    payload decoder bytes untouched. This is **not** a producer wire contract
    yet; a future V10 writer must be specified and fixture-tested before this
    adapter is used for export.
    """

    envelope_version: int
    array_size: int
    payload: bytes


def parse_envelope(data: bytes) -> FModelV10Envelope:
    if len(data) < ENVELOPE_HEADER_SIZE:
        raise ValueError("truncated FModel V10 extension envelope")
    envelope_version, array_size = struct.unpack_from("<Bi", data, 0)
    if envelope_version != ENVELOPE_VERSION:
        raise ValueError(f"unsupported FModel V10 envelope version: {envelope_version}")
    if array_size < 0 or array_size > MAX_ENVELOPE_ARRAY_SIZE:
        raise ValueError(f"invalid FModel V10 array size: {array_size}")
    payload = data[ENVELOPE_HEADER_SIZE:]
    if len(payload) > MAX_ENVELOPE_PAYLOAD_BYTES:
        raise ValueError("FModel V10 extension envelope payload is too large")
    return FModelV10Envelope(envelope_version, array_size, payload)
