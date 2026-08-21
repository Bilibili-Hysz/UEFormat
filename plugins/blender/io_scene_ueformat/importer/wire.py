from __future__ import annotations

import gzip
import io
from dataclasses import dataclass

from ..logging import Log
from .archive.reader import FArchiveReader
from .constants import ANIM_IDENTIFIER, MAGIC, MODEL_IDENTIFIER, POSE_IDENTIFIER
from .dto.anim import AnimDto
from .dto.model import ModelDto
from .dto.pose import PoseDto
from .version import EUEFormatVersion


MAX_COMPRESSED_BODY_BYTES = 2 * 1024 * 1024 * 1024
DECOMPRESSION_CHUNK_BYTES = 1024 * 1024


@dataclass(slots=True)
class UEFormatHeader:
    identifier: str
    file_version: EUEFormatVersion
    object_name: str
    object_path: str


def _decompress_gzip(payload: bytes, expected_size: int) -> bytes:
    """Decompress GZIP without allowing an undeclared expansion to run away."""

    output = bytearray()
    with gzip.GzipFile(fileobj=io.BytesIO(payload), mode="rb") as stream:
        while True:
            remaining = expected_size - len(output)
            chunk = stream.read(min(DECOMPRESSION_CHUNK_BYTES, remaining + 1))
            output.extend(chunk)
            if len(output) > expected_size:
                raise ValueError("GZIP body exceeds declared uncompressed size")
            if not chunk:
                break
    return bytes(output)


def _decompress_zstd(payload: bytes, expected_size: int) -> bytes:
    from .. import _require_zstd

    # The extra byte lets us detect an under-declared body while still
    # bounding the decompressor's output allocation.
    return _require_zstd().decompress(payload, max_output_size=expected_size + 1)


def parse_header(ar: FArchiveReader) -> tuple[UEFormatHeader, FArchiveReader]:
    magic = ar.read_string(len(MAGIC))
    if magic != MAGIC:
        raise ValueError("Invalid magic")

    identifier = ar.read_fstring()
    file_version = EUEFormatVersion(int.from_bytes(ar.read_byte(), byteorder="big"))
    if file_version > EUEFormatVersion.LatestVersion:
        msg = f"File Version {file_version} is not supported for this version of the importer."
        Log.error(msg)
        raise ValueError(msg)

    object_name = ar.read_fstring()
    object_path = ""
    if file_version >= EUEFormatVersion.AttributeFormatRestructure:
        object_path = ar.read_fstring()

    Log.info(f"Importing {object_name}")

    body = ar
    is_compressed = ar.read_bool()
    if is_compressed:
        compression_type = ar.read_fstring()
        uncompressed_size = ar.read_int()
        compressed_size = ar.read_int()
        if uncompressed_size < 0 or uncompressed_size > MAX_COMPRESSED_BODY_BYTES:
            raise ValueError(f"Invalid uncompressed body size: {uncompressed_size}")
        if compressed_size < 0 or compressed_size != ar.remaining():
            raise ValueError(
                f"Compressed body size does not match header: {compressed_size} != {ar.remaining()}"
            )
        compressed_payload = ar.read(compressed_size)

        if compression_type == "GZIP":
            body_bytes = _decompress_gzip(compressed_payload, uncompressed_size)
        elif compression_type == "ZSTD":
            body_bytes = _decompress_zstd(compressed_payload, uncompressed_size)
        else:
            msg = f"Unknown Compression Type: {compression_type}"
            Log.error(msg)
            raise ValueError(msg)

        if len(body_bytes) != uncompressed_size:
            raise ValueError(
                f"Decompressed body size does not match header: {len(body_bytes)} != {uncompressed_size}"
            )
        body = FArchiveReader(body_bytes)

    body.file_version = file_version
    return UEFormatHeader(identifier, file_version, object_name, object_path), body


def parse_body(header: UEFormatHeader, ar: FArchiveReader) -> ModelDto | AnimDto | PoseDto:
    if header.file_version >= EUEFormatVersion.AttributeFormatRestructure:
        from . import deserialize as parser
    else:
        from . import legacy as parser

    if header.identifier == MODEL_IDENTIFIER:
        return parser.read_model(ar)
    if header.identifier == ANIM_IDENTIFIER:
        return parser.read_anim(ar)
    if header.identifier == POSE_IDENTIFIER:
        return parser.read_pose(ar)

    msg = f"Unknown identifier: {header.identifier}"
    Log.error(msg)
    raise ValueError(msg)
