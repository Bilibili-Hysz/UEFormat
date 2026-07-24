from __future__ import annotations

import hashlib
from pathlib import Path

from tests.test_fmodel_skeleton_identity import animation_archive, classes, reader


FIXTURES = Path(__file__).with_name("fixtures")
MODEL = FIXTURES / "fmodel_skeleton_identity.uemodel"
ANIMATION = FIXTURES / "fmodel_target_skeleton.ueanim"
DOCUMENTED_MODEL_HASH = "25d70f86a61c7053d83ffe43240c13e5f7081b055bb05db8a7526b68d1badb6d"
DOCUMENTED_ANIMATION_HASH = "4af1eaca0bf69aae85f788bd33e2d55d351f70d94527148c02e6f8352a244cf2"


def test_identity_fixtures_match_documented_hashes():
    assert hashlib.sha256(MODEL.read_bytes()).hexdigest() == DOCUMENTED_MODEL_HASH
    assert hashlib.sha256(ANIMATION.read_bytes()).hexdigest() == DOCUMENTED_ANIMATION_HASH


def test_identity_fixtures_are_valid_standard_archives_with_extensions():
    model_data = MODEL.read_bytes()
    anim_data = ANIMATION.read_bytes()
    model_reader = reader.FArchiveReader(model_data)
    model_reader.read_string(len(classes.MAGIC))
    assert model_reader.read_fstring() == classes.MODEL_IDENTIFIER
    model_reader.read_byte()
    model_reader.read_fstring()
    model_reader.read_bool()
    model_reader.file_version = classes.EUEFormatVersion.LatestVersion
    model_reader = reader.FArchiveReader(model_data[model_reader.data.tell():])
    model_reader.file_version = classes.EUEFormatVersion.LatestVersion
    model_reader.metadata["scale"] = 0.01
    model = classes.UEModel.from_archive(model_reader)
    assert model.skeleton is not None
    assert model.skeleton_identity is not None
    assert [lod.name for lod in model.lods] == ["LOD0"]

    anim_reader = animation_archive(anim_data)
    anim_reader.read_string(len(classes.MAGIC))
    assert anim_reader.read_fstring() == classes.ANIM_IDENTIFIER
    anim_reader.read_byte()
    anim_reader.read_fstring()
    anim_reader.read_bool()
    animation_body = reader.FArchiveReader(anim_data[anim_reader.data.tell():])
    animation_body.file_version = classes.EUEFormatVersion.SerializeAssetMetadata
    animation = classes.UEAnim.from_archive(animation_body)
    assert animation.target_skeleton_identity is not None
    assert animation.target_skeleton_identity.source_animation_path.endswith("A_Run.A_Run")
