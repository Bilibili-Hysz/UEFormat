from __future__ import annotations

from collections.abc import Iterator

from ..archive.reader import FArchiveReader
from ..resource_limits import enforce_limit




def iter_attributes(ar: FArchiveReader) -> Iterator[tuple[str, FArchiveReader]]:
    count = ar.read_int()
    if count < 0 or count > ar.remaining() // 8:
        raise ValueError(f"Invalid V10 attribute count: {count}")
    enforce_limit(count, "MAX_ATTRIBUTES", "V10 attributes")
    for _ in range(count):
        name = ar.read_fstring()
        byte_size = ar.read_int()
        yield name, ar.chunk(byte_size)
    if not ar.eof():
        raise ValueError("Trailing bytes after V10 attribute envelope")


def require_attribute_eof(ar: FArchiveReader, name: str) -> None:
    if not ar.eof():
        raise ValueError(f"Trailing bytes in V10 {name} attribute")
