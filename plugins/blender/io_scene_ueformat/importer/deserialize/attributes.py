from __future__ import annotations

from collections.abc import Iterator

from ..archive.reader import FArchiveReader


MAX_ATTRIBUTE_COUNT = 4096


def iter_attributes(ar: FArchiveReader) -> Iterator[tuple[str, FArchiveReader]]:
    count = ar.read_int()
    if count < 0 or count > MAX_ATTRIBUTE_COUNT:
        raise ValueError(f"Invalid V10 attribute count: {count}")
    for _ in range(count):
        name = ar.read_fstring()
        byte_size = ar.read_int()
        yield name, ar.chunk(byte_size)
    if not ar.eof():
        raise ValueError("Trailing bytes after V10 attribute envelope")


def require_attribute_eof(ar: FArchiveReader, name: str) -> None:
    if not ar.eof():
        raise ValueError(f"Trailing bytes in V10 {name} attribute")
