from __future__ import annotations

import struct
import gzip

import pytest

from io_scene_ueformat.importer.extensions.fmodel import (
    FModelV10Attribute,
    FModelV10FileKind,
    collect_fmodel_extensions,
    parse_envelope,
)
from io_scene_ueformat.importer.extensions.fmodel.attributes import (
    FModelV10AttributeError,
    scan_attribute_spans,
)
from io_scene_ueformat.importer.archive.reader import FArchiveReader
from io_scene_ueformat.importer.constants import MAGIC
from io_scene_ueformat.importer.dto.anim import AnimDto
from io_scene_ueformat.importer.dto.model import ModelDto
from io_scene_ueformat.importer.dto.pose import PoseDto
from io_scene_ueformat.importer.version import EUEFormatVersion
from io_scene_ueformat.importer.wire import parse_body, parse_header


def fstring(value: str) -> bytes:
    raw = value.encode("utf-8")
    return struct.pack("<i", len(raw)) + raw


def v10_file(identifier: str, body_bytes: bytes, *, compressed: bool = False) -> bytes:
    payload = gzip.compress(body_bytes) if compressed else body_bytes
    header = (
        MAGIC.encode("ascii")
        + fstring(identifier)
        + bytes([EUEFormatVersion.AttributeFormatRestructure])
        + fstring("SyntheticObject")
        + fstring("/Game/Synthetic/SyntheticObject")
        + struct.pack("<?", compressed)
    )
    if compressed:
        header += fstring("GZIP") + struct.pack("<ii", len(body_bytes), len(payload))
    return header + payload


def body(*attributes: tuple[str, bytes]) -> bytes:
    return struct.pack("<i", len(attributes)) + b"".join(
        fstring(name) + struct.pack("<i", len(payload)) + payload
        for name, payload in attributes
    )


def test_standard_attributes_are_not_copied_into_extension_snapshot() -> None:
    data = body(
        ("LODS", b"large-standard-payload"),
        (FModelV10Attribute.MATERIALS.value, b"enveloped-materials"),
        ("FMODEL_FUTURE_ATTRIBUTE", b"future"),
    )

    snapshot = collect_fmodel_extensions(
        FModelV10FileKind.MODEL,
        data,
        known_standard_attributes=("LODS", "SKELETON", "COLLISION"),
    )

    assert snapshot.present
    assert snapshot.get(FModelV10Attribute.MATERIALS) == b"enveloped-materials"
    assert "LODS" not in snapshot.attributes
    assert snapshot.unknown_fmodel_attributes == ("FMODEL_FUTURE_ATTRIBUTE",)


def test_standard_only_file_has_no_fmodel_side_effect_payload() -> None:
    snapshot = collect_fmodel_extensions(
        FModelV10FileKind.MODEL,
        body(("LODS", b"standard")),
        known_standard_attributes=("LODS", "SKELETON", "COLLISION"),
    )

    assert not snapshot.present
    assert snapshot.attributes == {}


def test_duplicate_recognized_extension_fails_closed() -> None:
    data = body(
        (FModelV10Attribute.TEXTURES.value, b"one"),
        (FModelV10Attribute.TEXTURES.value, b"two"),
    )

    with pytest.raises(ValueError, match="duplicate FModel V10 extension"):
        collect_fmodel_extensions(FModelV10FileKind.MODEL, data)


def test_extension_for_another_file_kind_is_skipped_and_reported() -> None:
    data = body((FModelV10Attribute.MATERIALS.value, b"model-only"))

    snapshot = collect_fmodel_extensions(FModelV10FileKind.ANIM, data)

    assert not snapshot.present
    assert snapshot.unknown_fmodel_attributes == (FModelV10Attribute.MATERIALS.value,)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (struct.pack("<i", -1), "attribute count"),
        (struct.pack("<i", 1) + struct.pack("<i", 100), "truncated"),
        (struct.pack("<i", 1) + fstring("LODS") + struct.pack("<i", -1), "negative"),
        (struct.pack("<i", 0) + b"trailing", "trailing"),
    ],
)
def test_attribute_envelope_rejects_malformed_lengths(data: bytes, message: str) -> None:
    with pytest.raises(FModelV10AttributeError, match=message):
        scan_attribute_spans(data)


def test_attribute_names_require_strict_utf8() -> None:
    data = struct.pack("<i", 1) + struct.pack("<i", 2) + b"\xff\xff" + struct.pack("<i", 0)

    with pytest.raises(FModelV10AttributeError, match="UTF-8"):
        scan_attribute_spans(data)


def test_v10_extension_envelope_preserves_legacy_payload_bytes() -> None:
    payload = b"v9-derived-payload"
    data = struct.pack("<Bi", 1, 1) + payload

    envelope = parse_envelope(data)

    assert envelope.envelope_version == 1
    assert envelope.array_size == 1
    assert envelope.payload == payload


def test_v10_extension_envelope_rejects_unknown_version_and_negative_count() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        parse_envelope(struct.pack("<Bi", 2, 1) + b"payload")

    with pytest.raises(ValueError, match="array size"):
        parse_envelope(struct.pack("<Bi", 1, -1) + b"payload")

    with pytest.raises(ValueError, match="array size"):
        parse_envelope(struct.pack("<Bi", 1, 1_000_001) + b"payload")


def test_archive_reader_rejects_truncated_reads_and_negative_vectors() -> None:
    reader = FArchiveReader(b"\x01")
    with pytest.raises(ValueError, match="Integer exceeds"):
        reader.read_int()

    reader = FArchiveReader(b"")
    with pytest.raises(ValueError, match="negative integer vector"):
        reader.read_int_vector(-1)


def test_archive_reader_rejects_negative_or_unbounded_array_counts() -> None:
    with pytest.raises(ValueError, match="negative array count"):
        FArchiveReader(struct.pack("<i", -1)).read_serialized_array(lambda ar: None)

    with pytest.raises(ValueError, match="exceeds maximum"):
        FArchiveReader(struct.pack("<i", 1_000_001)).read_serialized_array(lambda ar: None)


@pytest.mark.parametrize(
    ("identifier", "expected_type"),
    [
        ("UEMODEL", ModelDto),
        ("UEANIM", AnimDto),
        ("UEPOSE", PoseDto),
    ],
)
def test_v10_wire_router_reads_synthetic_standard_only_files(identifier: str, expected_type: type) -> None:
    header, reader = parse_header(FArchiveReader(v10_file(identifier, struct.pack("<i", 0))))

    assert header.file_version is EUEFormatVersion.AttributeFormatRestructure
    assert header.object_path == "/Game/Synthetic/SyntheticObject"
    assert isinstance(parse_body(header, reader), expected_type)
    assert reader.eof()


def test_v10_wire_router_reads_gzip_body_with_declared_size() -> None:
    header, reader = parse_header(FArchiveReader(v10_file("UEMODEL", struct.pack("<i", 0), compressed=True)))

    assert header.identifier == "UEMODEL"
    assert isinstance(parse_body(header, reader), ModelDto)
    assert reader.eof()


def test_v10_gzip_reader_rejects_under_declared_body() -> None:
    data = bytearray(v10_file("UEMODEL", struct.pack("<i", 0), compressed=True))
    # Locate the declared uncompressed size after the fixed V10 header fields.
    reader = FArchiveReader(bytes(data))
    reader.read_string(len(MAGIC))
    reader.read_fstring()
    reader.read_byte()
    reader.read_fstring()
    reader.read_fstring()
    reader.read_bool()
    reader.read_fstring()
    size_offset = reader.data.tell()
    data[size_offset:size_offset + 4] = struct.pack("<i", 3)

    with pytest.raises(ValueError, match="GZIP body exceeds"):
        parse_header(FArchiveReader(bytes(data)))


def test_v10_gzip_reader_rejects_over_declared_body() -> None:
    data = bytearray(v10_file("UEMODEL", struct.pack("<i", 0), compressed=True))
    reader = FArchiveReader(bytes(data))
    reader.read_string(len(MAGIC))
    reader.read_fstring()
    reader.read_byte()
    reader.read_fstring()
    reader.read_fstring()
    reader.read_bool()
    reader.read_fstring()
    size_offset = reader.data.tell()
    data[size_offset:size_offset + 4] = struct.pack("<i", 5)

    with pytest.raises(ValueError, match="Decompressed body size"):
        parse_header(FArchiveReader(bytes(data)))


def test_v10_wire_router_rejects_unknown_identifier() -> None:
    header, reader = parse_header(FArchiveReader(v10_file("UEUNKNOWN", struct.pack("<i", 0))))

    with pytest.raises(ValueError, match="Unknown identifier"):
        parse_body(header, reader)
