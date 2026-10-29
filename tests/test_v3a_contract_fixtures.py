from __future__ import annotations

import hashlib
import importlib
import json
import re
import sys
import types
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = WORKSPACE / "docs" / "archive" / "2026-08-05-retired-v3a-boundary" / "contract-fixtures"
MANIFEST = FIXTURE_ROOT / "manifest.json"
REQUIRED_KINDS = {"uemodel", "ueanim", "uepose"}
PROTOCOL = {
    "ueformatBase": 9,
    "fmodelMaterials": 2,
    "fmodelTextures": 1,
    "skeletonIdentity": 1,
    "uescene": 1,
}
SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
SECRET_LIKE = re.compile(r"aes|secret|password|token|apikey|appsettings", re.IGNORECASE)


def _fixtures() -> list[dict]:
    document = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert set(document) == {"fixtures"}
    assert isinstance(document["fixtures"], list)
    return document["fixtures"]


def _is_safe_relative_path(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    normalized = value.replace("\\", "/")
    return not Path(value).is_absolute() and not normalized.startswith("/") and not re.match(r"^[A-Za-z]:", normalized) and ".." not in normalized.split("/")


def _validate_fixture_manifest(fixture: dict, raw_json: str) -> list[str]:
    diagnostics: list[str] = []
    for key in ("fixtureId", "kind", "producer", "protocol", "files", "expected", "expectedByFile", "realReleaseEvidence"):
        if key not in fixture:
            diagnostics.append(f"missing {key}")
    producer = fixture.get("producer")
    if not isinstance(producer, dict) or any(not isinstance(producer.get(key), str) or not producer[key].strip() for key in ("kind", "repository", "commit")):
        diagnostics.append("invalid provenance")
    if fixture.get("protocol") != PROTOCOL:
        diagnostics.append("invalid protocol")
    files = fixture.get("files")
    expected_by_file = fixture.get("expectedByFile")
    if not isinstance(files, list) or not files:
        diagnostics.append("missing files")
        files = []
    if not isinstance(expected_by_file, dict):
        diagnostics.append("missing expectedByFile")
        expected_by_file = {}
    for entry in files:
        if not isinstance(entry, dict):
            diagnostics.append("invalid file entry")
            continue
        path = entry.get("path")
        if not _is_safe_relative_path(path):
            diagnostics.append("unsafe file path")
        if not isinstance(entry.get("sha256"), str) or not SHA256.fullmatch(entry["sha256"]):
            diagnostics.append("invalid sha256")
        if not isinstance(entry.get("bytes"), int) or entry["bytes"] < 0:
            diagnostics.append("invalid bytes")
        expected = expected_by_file.get(path)
        if not isinstance(expected, dict) or not isinstance(expected.get("chunks"), list) or not isinstance(expected.get("resources"), int) or not isinstance(expected.get("legacyReadable"), bool):
            diagnostics.append(f"missing per-file expectations: {path}")
    if set(expected_by_file) != {entry.get("path") for entry in files if isinstance(entry, dict)}:
        diagnostics.append("per-file expectations do not match files")
    evidence = fixture.get("realReleaseEvidence")
    if not isinstance(evidence, bool):
        diagnostics.append("invalid release-evidence flag")
    if evidence and (not isinstance(fixture.get("releaseVersion"), str) or not fixture["releaseVersion"].strip() or not _is_safe_relative_path(fixture.get("releaseSourceLog"))):
        diagnostics.append("invalid release evidence")
    if SECRET_LIKE.search(raw_json):
        diagnostics.append("secret-like content")
    return diagnostics


def _load_bpy_free_readers():
    plugin_root = Path(__file__).parents[1] / "plugins" / "blender"
    package_root = plugin_root / "io_scene_ueformat"
    mathutils = types.ModuleType("mathutils")
    mathutils.Quaternion = tuple
    mathutils.Vector = tuple
    sys.modules["mathutils"] = mathutils
    bpy = types.ModuleType("bpy")
    bpy_types = types.ModuleType("bpy.types")
    bpy_types.PoseBone = object
    bpy_types.bpy_prop_collection = object
    bpy.types = bpy_types
    sys.modules["bpy"] = bpy
    sys.modules["bpy.types"] = bpy_types
    package = types.ModuleType("io_scene_ueformat")
    package.__path__ = [str(package_root)]
    sys.modules.setdefault("io_scene_ueformat", package)
    return (
        importlib.import_module("io_scene_ueformat.importer.classes"),
        importlib.import_module("io_scene_ueformat.importer.reader"),
    )


classes, reader = _load_bpy_free_readers()


def _read_binary_fixture(path: Path):
    archive = reader.FArchiveReader(path.read_bytes())
    assert archive.read_string(len(classes.MAGIC)) == classes.MAGIC
    identifier = archive.read_fstring()
    archive.file_version = classes.EUEFormatVersion(int.from_bytes(archive.read_byte(), byteorder="big"))
    archive.read_fstring()  # object name
    assert archive.read_bool() is False
    archive.metadata["scale"] = 1.0
    parser = {
        classes.MODEL_IDENTIFIER: classes.UEModel,
        classes.ANIM_IDENTIFIER: classes.UEAnim,
        classes.POSE_IDENTIFIER: classes.UEPose,
    }[identifier]
    return identifier, parser.from_archive(archive)


def _binary_chunk_names(path: Path) -> list[str]:
    archive = reader.FArchiveReader(path.read_bytes())
    assert archive.read_string(len(classes.MAGIC)) == classes.MAGIC
    archive.read_fstring()
    archive.read_byte()
    archive.read_fstring()
    assert archive.read_bool() is False
    chunks: list[str] = []
    while not archive.eof():
        chunks.append(archive.read_fstring())
        archive.read_int()
        archive.skip(archive.read_int())
    return chunks


def test_python_manifest_validator_rejects_required_field_hash_path_secret_and_release_evidence_failures():
    valid = {
        "fixtureId": "fixture", "kind": "uemodel",
        "producer": {"kind": "synthetic", "repository": "fixtures", "commit": "abc"},
        "protocol": PROTOCOL,
        "files": [{"path": "model.uemodel", "sha256": "0" * 64, "bytes": 1}],
        "expected": {"chunks": [], "resources": 0, "legacyReadable": True},
        "expectedByFile": {"model.uemodel": {"chunks": [], "resources": 0, "legacyReadable": True}},
        "realReleaseEvidence": False,
    }
    assert _validate_fixture_manifest(valid, json.dumps(valid)) == []
    missing = dict(valid)
    missing.pop("producer")
    assert "missing producer" in _validate_fixture_manifest(missing, json.dumps(missing))
    bad_hash = dict(valid, files=[dict(valid["files"][0], sha256="not-a-hash")])
    assert "invalid sha256" in _validate_fixture_manifest(bad_hash, json.dumps(bad_hash))
    unsafe = dict(valid, files=[dict(valid["files"][0], path="../secret.bin")])
    assert "unsafe file path" in _validate_fixture_manifest(unsafe, json.dumps(unsafe))
    secret = dict(valid, producer={"kind": "synthetic", "repository": "tokenized-fixtures", "commit": "abc"})
    assert "secret-like content" in _validate_fixture_manifest(secret, json.dumps(secret))
    release = dict(valid, realReleaseEvidence=True, releaseVersion="", releaseSourceLog="/release.log")
    assert "invalid release evidence" in _validate_fixture_manifest(release, json.dumps(release))


def test_shared_manifest_validates_provenance_security_and_per_file_metadata():
    raw = MANIFEST.read_text(encoding="utf-8")
    fixtures = _fixtures()
    assert {fixture["kind"] for fixture in fixtures} >= REQUIRED_KINDS
    assert all(_validate_fixture_manifest(fixture, raw) == [] for fixture in fixtures)
    assert all(fixture["producer"]["kind"] == "synthetic" for fixture in fixtures)
    assert not any(fixture["realReleaseEvidence"] for fixture in fixtures)
    for fixture in fixtures:
        for entry in fixture["files"]:
            relative = Path(entry["path"])
            payload = (FIXTURE_ROOT / relative).read_bytes()
            assert len(payload) == entry["bytes"]
            assert hashlib.sha256(payload).hexdigest() == entry["sha256"]
            expected = fixture["expectedByFile"][entry["path"]]
            assert expected["chunks"]
            assert isinstance(expected["resources"], int)
            assert isinstance(expected["legacyReadable"], bool)


def test_binary_fixtures_parse_with_bpy_free_readers_and_match_per_file_contracts():
    fixtures = {fixture["fixtureId"]: fixture for fixture in _fixtures()}
    model_fixture = fixtures["uemodel/material-links-skeleton-identity"]
    model_path = FIXTURE_ROOT / "uemodel/material-links.uemodel"
    identifier, model = _read_binary_fixture(model_path)
    assert identifier == "UEMODEL"
    assert _binary_chunk_names(model_path) == model_fixture["expectedByFile"]["uemodel/material-links.uemodel"]["chunks"]
    assert model_fixture["expectedByFile"]["uemodel/material-links.uemodel"]["resources"] == len(model.textures) == 3
    assert len(model.lods) == 1 and len(model.material_links.slots) == 2
    assert [slot.unreal_material_path for slot in model.material_links.slots] == ["/Game/Materials/M_GoldenBody.M_GoldenBody", "/Game/Materials/M_GoldenOverlay.M_GoldenOverlay"]
    assert {texture.unreal_object_path for texture in model.textures} == {"/Game/Textures/T_Golden_BC.T_Golden_BC", "/Game/Textures/T_Golden_N.T_Golden_N", "/Game/Textures/T_Golden_Wetness.T_Golden_Wetness"}
    identity_path = FIXTURE_ROOT / "uemodel/skeleton-identity.uemodel"
    identifier, identity_model = _read_binary_fixture(identity_path)
    assert identifier == "UEMODEL" and identity_model.skeleton_identity is not None
    assert _binary_chunk_names(identity_path) == model_fixture["expectedByFile"]["uemodel/skeleton-identity.uemodel"]["chunks"]
    assert model_fixture["expectedByFile"]["uemodel/skeleton-identity.uemodel"]["resources"] == len(identity_model.textures) == 0
    assert identity_model.skeleton_identity.skeleton_path == "/Game/Characters/SK_Hero_Skeleton.SK_Hero_Skeleton"
    assert identity_model.skeleton_identity.skeleton_guid == b"\x01\0\0\0\x02\0\0\0\x03\0\0\0\x04\0\0\0"

    anim_fixture = fixtures["ueanim/target-skeleton-identity"]
    identifier, anim = _read_binary_fixture(FIXTURE_ROOT / "ueanim/target-skeleton.ueanim")
    assert identifier == "UEANIM" and anim.target_skeleton_identity is not None
    assert _binary_chunk_names(FIXTURE_ROOT / "ueanim/target-skeleton.ueanim") == anim_fixture["expectedByFile"]["ueanim/target-skeleton.ueanim"]["chunks"]
    assert anim_fixture["expectedByFile"]["ueanim/target-skeleton.ueanim"]["resources"] == len(anim.tracks) == 0
    assert anim.target_skeleton_identity.source_animation_path == "/Game/Hero/A_Run.A_Run"
    assert anim.target_skeleton_identity.skeleton_path == "/Game/Characters/SK_Hero_Skeleton.SK_Hero_Skeleton"

    pose_fixture = fixtures["uepose/target-skeleton-identity"]
    identifier, pose = _read_binary_fixture(FIXTURE_ROOT / "uepose/target-skeleton.uepose")
    assert identifier == "UEPOSE" and pose.target_skeleton_identity is not None
    assert _binary_chunk_names(FIXTURE_ROOT / "uepose/target-skeleton.uepose") == pose_fixture["expectedByFile"]["uepose/target-skeleton.uepose"]["chunks"]
    assert pose_fixture["expectedByFile"]["uepose/target-skeleton.uepose"]["resources"] == len(pose.curve_names) == 0
    assert [entry.name for entry in pose.poses] == ["SmilePose"]
    assert pose.target_skeleton_identity.source_pose_path == "/Game/Hero/P_Pose.P_Pose"
