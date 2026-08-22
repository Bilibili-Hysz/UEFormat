from __future__ import annotations

from pathlib import Path

import pytest

from io_scene_ueformat.importer.archive.reader import FArchiveReader
from io_scene_ueformat.importer.extensions.fmodel import (
    FModelV10Attribute,
    FModelV10FileKind,
    collect_fmodel_extensions,
    decode_v10_identity_chunk,
    decode_v10_material_links,
    decode_v10_textures,
)
from io_scene_ueformat.importer.fmodel_v10_adapter import to_runtime_material_links
from io_scene_ueformat.importer.wire import parse_body, parse_header


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "fmodel_v10"


def _read_fixture(path: Path):
    header, body = parse_header(FArchiveReader(path.read_bytes()))
    body_bytes = body.read_to_end()
    dto = parse_body(header, FArchiveReader(body_bytes))
    snapshot = collect_fmodel_extensions(
        FModelV10FileKind.MODEL,
        body_bytes,
        known_standard_attributes=("LODS", "SKELETON", "COLLISION"),
    )
    return header, dto, snapshot


def _read_generated_fixture(path: Path):
    header, body = parse_header(FArchiveReader(path.read_bytes()))
    body_bytes = body.read_to_end()
    dto = parse_body(header, FArchiveReader(body_bytes))
    snapshot = collect_fmodel_extensions(
        FModelV10FileKind.MODEL,
        body_bytes,
        known_standard_attributes=("LODS", "SKELETON", "COLLISION"),
    )
    return header, dto, snapshot


@pytest.mark.parametrize(
    "filename",
    ["fmodel_v10_none.uemodel", "fmodel_v10_gzip.uemodel"],
)
def test_csharp_v10_producer_fixtures_keep_standard_and_extension_layers_separate(
    filename: str,
) -> None:
    header, dto, snapshot = _read_fixture(FIXTURE_ROOT / filename)

    assert header.object_path.endswith("SM_FModelV10Contract.SM_FModelV10Contract")
    assert len(dto.lods) == 0
    assert set(snapshot.attributes) == {
        FModelV10Attribute.MATERIALS.value,
        FModelV10Attribute.TEXTURES.value,
        FModelV10Attribute.SKELETON_IDENTITY.value,
    }
    assert not snapshot.unknown_fmodel_attributes


def test_csharp_v10_producer_fixture_decodes_uri_and_pbr_semantics() -> None:
    _, _, snapshot = _read_fixture(FIXTURE_ROOT / "fmodel_v10_none.uemodel")
    materials = decode_v10_material_links(snapshot.get(FModelV10Attribute.MATERIALS))
    textures = decode_v10_textures(snapshot.get(FModelV10Attribute.TEXTURES))
    identity = decode_v10_identity_chunk(
        snapshot.get(FModelV10Attribute.SKELETON_IDENTITY),
    )

    assert materials.version == 2
    assert materials.slots[0].material_json_uri == "../Materials/M_FModelV10_Body.json"
    assert [record.logical_resource_uri for record in textures.textures] == [
        "../Textures/T_FModelV10_BaseColor.png",
        "../Textures/T_FModelV10_Normal.png",
        "../Textures/T_FModelV10_Orm.png",
    ]
    assert [int(binding.map_kind) for binding in textures.bindings] == [0, 1, 2]
    assert identity.skeleton_path.endswith("SKEL_FM_Base.SKEL_FM_Base")

    runtime_links, embedded = to_runtime_material_links(materials, textures)
    assert not embedded
    assert [parameter.semantic for parameter in runtime_links.slots[0].parameters] == [
        "base_color",
        "normal",
        "orm",
    ]
    assert [parameter.value.kind.value for parameter in runtime_links.slots[0].parameters] == [
        "identity",
        "identity",
        "identity",
    ]


def test_csharp_v10_zstd_fixture_is_a_real_compression_gate() -> None:
    try:
        import zstandard  # noqa: F401
    except ModuleNotFoundError:
        pytest.skip("Blender bundled zstandard wheel is required for the ZSTD gate")

    header, dto, snapshot = _read_fixture(FIXTURE_ROOT / "fmodel_v10_zstd.uemodel")
    assert header.object_path.endswith("SM_FModelV10Contract.SM_FModelV10Contract")
    assert len(dto.lods) == 0
    assert snapshot.present


def test_generated_csharp_v10_fixture_contains_real_mesh_geometry() -> None:
    generated = Path(__file__).parent / "generated" / "fmodel-v10" / "models" / "fmodel_v10_none.uemodel"
    if not generated.is_file():
        pytest.skip("run the C# V10 producer harness first")
    _, dto, snapshot = _read_generated_fixture(generated)
    assert len(dto.lods) == 1
    assert dto.lods[0].vertices.shape == (3, 3)
    assert dto.lods[0].indices.tolist() == [[0, 1, 2]]
    assert [material.material_name for material in dto.lods[0].materials] == ["Body"]
    assert snapshot.present
