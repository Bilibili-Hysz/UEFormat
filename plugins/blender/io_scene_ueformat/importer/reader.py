from __future__ import annotations

import io
import struct
import numpy as np
import numpy.typing as npt
from typing import TYPE_CHECKING, BinaryIO, Literal, TypeVar, overload

from ..importer.utils import bytes_to_str
from .protocol import EUEFormatVersion

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import TracebackType

R = TypeVar("R")


class FArchiveReader:
    def __init__(self, data: bytes) -> None:
        self.data: BinaryIO = io.BytesIO(data)
        self.size = len(data)
        self.data.seek(0)
        self.file_version = EUEFormatVersion.BeforeCustomVersionWasAdded
        self.metadata = {}

    def __enter__(self) -> FArchiveReader:
        self.data.seek(0)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.data.close()

    def eof(self) -> bool:
        return self.data.tell() >= self.size

    def remaining(self) -> int:
        return self.size - self.data.tell()

    def _read_exact(self, size: int, description: str) -> bytes:
        if size < 0 or size > self.remaining():
            raise ValueError(f"{description} exceeds available data")
        data = self.data.read(size)
        if len(data) != size:
            raise ValueError(f"{description} exceeds available data")
        return data

    def read(self, size: int) -> bytes:
        return self._read_exact(size, "Read size")

    def read_to_end(self) -> bytes:
        return self.data.read(self.size - self.data.tell())

    def read_bool(self) -> bool:
        return struct.unpack("?", self._read_exact(1, "Boolean"))[0]

    def read_string(self, size: int) -> str:
        string = self._read_exact(size, "String length")
        return bytes_to_str(string)

    def read_fstring(self) -> str:
        (size,) = struct.unpack("i", self._read_exact(4, "FString length"))
        if size < 0:
            raise ValueError(f"Invalid negative FString length: {size}")
        string = self._read_exact(size, "FString length")
        ret = bytes_to_str(string)
        return ret

    def read_bounded_fstring(self, maximum_size: int, description: str) -> str:
        (size,) = struct.unpack("i", self._read_exact(4, f"{description} length"))
        if size < 0:
            raise ValueError(f"Invalid negative {description} length: {size}")
        if size > maximum_size:
            raise ValueError(f"Invalid {description} length exceeds maximum {maximum_size}: {size}")
        return bytes_to_str(self._read_exact(size, f"{description} length"))

    def read_int(self) -> int:
        return struct.unpack("i", self._read_exact(4, "Integer"))[0]

    def read_int_vector(self, size: int) -> tuple[int, ...]:
        if size <= 0:
            return ()
        return struct.unpack(str(size) + "I", self._read_exact(size * 4, "Integer vector"))

    def read_short(self) -> int:
        return struct.unpack("h", self._read_exact(2, "Short"))[0]

    def read_ushort(self) -> int:
        return struct.unpack("<H", self._read_exact(2, "Unsigned short"))[0]

    def read_uint8(self) -> int:
        return struct.unpack("B", self._read_exact(1, "Unsigned byte"))[0]

    def read_byte(self) -> bytes:
        """Return one byte for compatibility with the upstream reader API."""
        return struct.unpack("c", self._read_exact(1, "Byte"))[0]

    def read_float(self) -> float:
        return struct.unpack("f", self._read_exact(4, "Float"))[0]

    @overload
    def read_float_vector(self, size: Literal[1]) -> tuple[float]: ...
    @overload
    def read_float_vector(self, size: Literal[2]) -> tuple[float, float]: ...
    @overload
    def read_float_vector(self, size: Literal[3]) -> tuple[float, float, float]: ...
    @overload
    def read_float_vector(self, size: Literal[4]) -> tuple[float, float, float, float]: ...
    @overload
    def read_float_vector(self, size: int) -> tuple[float, ...]: ...

    def read_float_vector(self, size: int) -> npt.NDArray[float]:
        return np.array(struct.unpack(str(size) + "f", self._read_exact(size * 4, "Float vector")))

    def read_byte_vector(self, size: int) -> tuple[int, ...]:
        return struct.unpack(str(size) + "B", self._read_exact(size, "Byte vector"))

    def skip(self, size: int) -> None:
        self._read_exact(size, "Skip size")

    def read_serialized_array(self, predicate: Callable[[FArchiveReader], R]) -> list[R]:
        count = self.read_int()
        return self.read_array(count, predicate)

    def read_array(
        self,
        count: int,
        predicate: Callable[[FArchiveReader], R],
    ) -> list[R]:
        if count < 0:
            raise ValueError(f"Invalid negative array count: {count}")
        return [predicate(self) for _ in range(count)]

    def chunk(self, size: int) -> FArchiveReader:
        if size < 0:
            raise ValueError(f"Invalid negative chunk byte_size: {size}")
        new_reader = FArchiveReader(self._read_exact(size, "Chunk byte_size"))
        new_reader.file_version = self.file_version
        new_reader.metadata = self.metadata
        
        return new_reader
