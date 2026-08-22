from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from io_scene_ueformat.importer.fmodel_payload import FModelImportPayload
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
