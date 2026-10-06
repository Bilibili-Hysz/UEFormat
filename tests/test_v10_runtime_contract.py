from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from io_scene_ueformat.importer.fmodel_payload import FModelImportPayload
from io_scene_ueformat.importer.fmodel_v10_adapter import to_runtime_material_links
from io_scene_ueformat.importer.extensions.fmodel.materials import (
    FModelMaterialLinksSnapshot,
    FModelMaterialSlot,
    FModelTextureBinding,
    FModelTextureMapKind,
    FModelTextureRecord,
    FModelTextureSnapshot,
)
from io_scene_ueformat.importer.runtime_fmodel import FModelResourceResolver
from io_scene_ueformat.importer.runtime_resources import TextureResource


def _identity_texture(uri: str, payload: bytes, *, byte_length: int | None = None) -> TextureResource:
    return TextureResource.identity(
        id=0,
        unreal_object_path="/Game/Textures/T_Test.T_Test",
        location_mode=1,
        uri=uri,
        color_space=1,
        packing=0,
        uv_set=0,
        stable_id="texture.test",
        expected_byte_length=len(payload) if byte_length is None else byte_length,
        expected_content_hash=sha256(payload).digest(),
        identity_embedded=False,
    )


def test_owner_relative_identity_texture_verifies_size_and_hash(tmp_path: Path) -> None:
    content = tmp_path / "CodeWorld" / "Content"
    model = content / "Models" / "SK_Test.uemodel"
    texture_path = content / "Textures" / "T_Test.png"
    model.parent.mkdir(parents=True)
    texture_path.parent.mkdir(parents=True)
    texture_path.write_bytes(b"texture payload")

    resolver = FModelResourceResolver(
        model_path=model,
        fmodel_textures={},
        ue_export_root=content,
        bundle_root=None,
    )
    texture = _identity_texture("../Textures/T_Test.png", b"texture payload")

    assert resolver.resolve_texture(texture) == texture_path.resolve()


def test_owner_relative_identity_texture_rejects_integrity_mismatch(tmp_path: Path) -> None:
    content = tmp_path / "CodeWorld" / "Content"
    model = content / "Models" / "SK_Test.uemodel"
    texture_path = content / "Textures" / "T_Test.png"
    model.parent.mkdir(parents=True)
    texture_path.parent.mkdir(parents=True)
    texture_path.write_bytes(b"wrong payload")
    resolver = FModelResourceResolver(
        model_path=model,
        fmodel_textures={},
        ue_export_root=content,
        bundle_root=None,
    )

    with pytest.raises(ValueError, match="byte length mismatch|SHA-256 mismatch"):
        resolver.resolve_texture(_identity_texture("../Textures/T_Test.png", b"expected payload"))


def test_identity_provenance_source_is_explicit() -> None:
    assert FModelImportPayload().skeleton_identity_source is None


def test_v10_material_binding_rejects_ambiguous_slot_name() -> None:
    materials = FModelMaterialLinksSnapshot(
        version=2,
        slots=(
            FModelMaterialSlot(
                slot_index=0,
                slot_name="Body",
                unreal_material_path="/Game/Materials/M_Body_A.M_Body_A",
                material_json_uri="",
                source_material_index=0,
                parameters=(),
            ),
            FModelMaterialSlot(
                slot_index=1,
                slot_name="Body",
                unreal_material_path="/Game/Materials/M_Body_B.M_Body_B",
                material_json_uri="",
                source_material_index=1,
                parameters=(),
            ),
        ),
    )
    textures = FModelTextureSnapshot(
        version=2,
        textures=(
            FModelTextureRecord(
                texture_id="texture.base",
                logical_resource_uri="textures/body.png",
                is_embedded=False,
                byte_length=3,
                content_hash=sha256(b"PNG").digest(),
                location_mode=1,
            ),
        ),
        bindings=(
            FModelTextureBinding(
                material_id="Body",
                map_kind=FModelTextureMapKind.BASE_COLOR,
                texture_id="texture.base",
                is_srgb=True,
            ),
        ),
    )

    with pytest.raises(ValueError, match="ambiguous across Unreal material identities"):
        to_runtime_material_links(materials, textures)


def test_v10_embedded_identity_record_fails_before_resource_resolution() -> None:
    materials = FModelMaterialLinksSnapshot(
        version=2,
        slots=(
            FModelMaterialSlot(
                slot_index=0,
                slot_name="Body",
                unreal_material_path="/Game/Materials/M_Body.M_Body",
                material_json_uri="materials/body.json",
                source_material_index=0,
                parameters=(),
            ),
        ),
    )
    textures = FModelTextureSnapshot(
        version=2,
        textures=(
            FModelTextureRecord(
                texture_id="texture.embedded",
                logical_resource_uri="textures/body.png",
                is_embedded=True,
                byte_length=3,
                content_hash=sha256(b"PNG").digest(),
                location_mode=3,
            ),
        ),
        bindings=(
            FModelTextureBinding(
                material_id="Body",
                map_kind=FModelTextureMapKind.BASE_COLOR,
                texture_id="texture.embedded",
                is_srgb=True,
            ),
        ),
    )

    with pytest.raises(ValueError, match="identity records marked embedded"):
        to_runtime_material_links(materials, textures)


def test_inferred_export_tree_allows_sibling_mount_but_rejects_escape(tmp_path):
    model = tmp_path / "Exports/Game/Content/Models/Model.uemodel"
    model.parent.mkdir(parents=True)
    payload = b"verified texture"
    sibling = tmp_path / "Exports/Engine/Content/Textures/T.png"
    sibling.parent.mkdir(parents=True)
    sibling.write_bytes(payload)
    resolver = FModelResourceResolver(model_path=model, fmodel_textures={}, ue_export_root=None, bundle_root=None)
    assert resolver.resolve_texture(_identity_texture("../../../Engine/Content/Textures/T.png", payload)) == sibling.resolve()
    outside = tmp_path / "Outside.png"
    outside.write_bytes(payload)
    with pytest.raises(ValueError, match="escapes"):
        resolver.resolve_texture(_identity_texture("../../../../Outside.png", payload))


def test_inferred_export_root_identity_allows_sibling_mount(tmp_path):
    model = tmp_path / "Exports/Game/Content/Models/Model.uemodel"
    model.parent.mkdir(parents=True)
    payload = b"verified texture"
    sibling = tmp_path / "Exports/Engine/Content/Textures/T.png"
    sibling.parent.mkdir(parents=True)
    sibling.write_bytes(payload)
    resolver = FModelResourceResolver(model_path=model, fmodel_textures={}, ue_export_root=None, bundle_root=None)
    texture = _identity_texture("Engine/Content/Textures/T.png", payload)
    texture.location_mode = 4
    assert resolver.resolve_texture(texture) == sibling.resolve()
