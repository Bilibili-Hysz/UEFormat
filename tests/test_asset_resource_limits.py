"""Format bounds remain enforced while optional budgets are user controlled."""

import struct

import pytest

from io_scene_ueformat.importer.archive.reader import FArchiveReader
from io_scene_ueformat.importer.deserialize.attributes import iter_attributes
from io_scene_ueformat.importer.resource_limits import ResourceLimitError


def test_complete_array_above_previous_cap_is_readable():
    count = 1_000_001
    reader = FArchiveReader(struct.pack('<i', count) + struct.pack('<i', 17) * count)
    result = reader.read_serialized_array(lambda archive: archive.read_int())
    assert len(result) == count and result[-1] == 17 and reader.eof()


def test_explicit_array_budget_remains_available(monkeypatch):
    monkeypatch.setenv('FMODEL_UEFORMAT_MAX_ARRAY_ITEMS', '2')
    reader = FArchiveReader(struct.pack('<i', 3) + b'abc')
    with pytest.raises(ResourceLimitError, match='configured'):
        reader.read_serialized_array(lambda archive: archive.read_byte())


def test_truncated_array_and_vector_remain_invalid():
    with pytest.raises(ValueError, match='available data'):
        FArchiveReader(struct.pack('<i', 4) + b'abc').read_serialized_array(lambda archive: archive.read_byte())
    with pytest.raises(ValueError, match='available data'):
        FArchiveReader(b'abc').read_float_vector(1)


def test_more_than_4096_skippable_attributes_are_supported():
    payload = struct.pack('<i', 4097) + (struct.pack('<i', 6) + b'FUTURE' + struct.pack('<i', 0)) * 4097
    assert len(list(iter_attributes(FArchiveReader(payload)))) == 4097


def test_attribute_budget_is_distinct_from_malformed_lengths(monkeypatch):
    monkeypatch.setenv('FMODEL_UEFORMAT_MAX_ATTRIBUTES', '1')
    payload = struct.pack('<i', 2) + (struct.pack('<i', 1) + b'X' + struct.pack('<i', 0)) * 2
    with pytest.raises(ResourceLimitError):
        list(iter_attributes(FArchiveReader(payload)))
