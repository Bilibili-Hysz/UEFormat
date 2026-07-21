from __future__ import annotations

import importlib
import struct
import sys
import types
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).parents[1] / "plugins" / "blender"
PACKAGE_ROOT = PLUGIN_ROOT / "io_scene_ueformat"


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


def load_modules():
    package = types.ModuleType("io_scene_ueformat")
    package.__path__ = [str(PACKAGE_ROOT)]
    sys.modules.setdefault("io_scene_ueformat", package)
    return (
        importlib.import_module("io_scene_ueformat.importer.classes"),
        importlib.import_module("io_scene_ueformat.importer.reader"),
        importlib.import_module("io_scene_ueformat.importer.fmodel"),
    )


classes, reader, fmodel = load_modules()


class FModelSkeletonIdentityComparisonTests(unittest.TestCase):
    def setUp(self):
        self.identity = {
            "skeleton_path": "\\Game\\Hero\\SK_Hero_Skeleton.SK_Hero_Skeleton",
            "has_skeleton_guid": True,
            "skeleton_guid": "00" * 16,
            "bone_layout_hash": "11" * 32,
        }

    def test_normalized_exact_match(self):
        other = dict(self.identity, skeleton_path=" /Game/Hero/SK_Hero_Skeleton.SK_Hero_Skeleton ")
        fmodel.verify_fmodel_target_skeleton(self.identity, other)

    def test_reports_each_mismatch_field(self):
        for field, message in (
            ("skeleton_path", "path"),
            ("skeleton_guid", "GUID"),
            ("bone_layout_hash", "layout hash"),
        ):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, message):
                fmodel.verify_fmodel_target_skeleton(self.identity, dict(self.identity, **{field: "mismatch"}))

    def test_compares_guid_only_when_both_present(self):
        fmodel.verify_fmodel_target_skeleton(
            dict(self.identity, has_skeleton_guid=False, skeleton_guid=None),
            dict(self.identity, skeleton_guid="mismatch"),
        )


class FModelAnimationBindingPlanTests(unittest.TestCase):
    DEFAULT = object()

    def setUp(self):
        self.target_identity = {
            "version": 1,
            "source_animation_path": "/Game/Hero/A_Run.A_Run",
            "skeleton_path": "/Game/Hero/SK_Hero_Skeleton.SK_Hero_Skeleton",
            "has_skeleton_guid": True,
            "skeleton_guid": "00" * 16,
            "bone_layout_hash": "11" * 32,
        }
        self.armature_identity = dict(self.target_identity)
        self.armature_identity.pop("source_animation_path")

    def plan(self, *, target_identity=DEFAULT, armature_identity=DEFAULT, tracks=("root",), bones=("root",), strict=True):
        return fmodel.plan_fmodel_animation_binding(
            self.target_identity if target_identity is self.DEFAULT else target_identity,
            self.armature_identity if armature_identity is self.DEFAULT else armature_identity,
            tracks,
            bones,
            strict=strict,
        )

    def test_strict_rejects_missing_or_malformed_expected_identity(self):
        cases = (
            (None, self.armature_identity, "target.*missing"),
            (self.target_identity, None, "armature.*missing"),
            (self.target_identity, {"version": 1}, "armature.*malformed"),
            (dict(self.target_identity, bone_layout_hash="bad"), self.armature_identity, "target.*malformed"),
        )
        for target, armature, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.plan(target_identity=target, armature_identity=armature)

    def test_strict_rejects_layout_mismatch_and_ambiguous_casefold_match(self):
        with self.assertRaisesRegex(ValueError, "layout hash mismatch"):
            self.plan(armature_identity=dict(self.armature_identity, bone_layout_hash="22" * 32))
        with self.assertRaisesRegex(ValueError, "ambiguous.*root"):
            self.plan(bones=("Root", "ROOT"))

    def test_strict_rejects_more_than_ten_percent_missing_tracks(self):
        tracks = tuple(f"bone_{index}" for index in range(10))
        with self.assertRaisesRegex(ValueError, "2 of 10.*missing"):
            self.plan(tracks=tracks, bones=tracks[:8])

    def test_permissive_reports_exact_binding_counts_and_source_identity(self):
        plan = self.plan(
            tracks=("root", "missing", "arm"),
            bones=("Root", "Arm", "ARM"),
            strict=False,
        )
        self.assertEqual(plan.bound_track_count, 1)
        self.assertEqual(plan.skipped_track_count, 2)
        self.assertEqual(plan.total_track_count, 3)
        self.assertEqual(plan.bound_bone_names, ("Root", None, None))
        self.assertEqual(plan.source_identity, self.target_identity)

    def test_legacy_import_without_either_identity_is_not_provenance_strict(self):
        plan = fmodel.plan_fmodel_animation_binding(None, None, ("root",), ("Root", "ROOT"))
        self.assertEqual(plan.bound_track_count, 1)
        self.assertEqual(plan.bound_bone_names, ("Root",))
        self.assertFalse(plan.provenance_expected)


def fstring(value: str) -> bytes:
    encoded = value.encode("utf-8") + b"\0"
    return struct.pack("<i", len(encoded)) + encoded


def chunk(name: str, count: int, payload: bytes, *, byte_size: int | None = None) -> bytes:
    return fstring(name) + struct.pack("<ii", count, len(payload) if byte_size is None else byte_size) + payload


def animation_archive(data: bytes):
    archive = reader.FArchiveReader(data)
    archive.file_version = classes.EUEFormatVersion.SerializeAssetMetadata
    return archive


def model_identity_payload(*, version: int = 1, path: str = "/Game/Hero/SK_Hero_Skeleton.SK_Hero_Skeleton", has_guid: int = 1, guid: bytes = bytes(range(16)), layout_hash: bytes = bytes(range(32))) -> bytes:
    return bytes([version]) + fstring(path) + bytes([has_guid]) + (guid if has_guid == 1 else b"") + layout_hash


def target_identity_payload(*, source_path: str = "/Game/Hero/A_Run.A_Run", **kwargs) -> bytes:
    return b"\x01" + fstring(source_path) + model_identity_payload(**kwargs)[1:]


class FModelSkeletonIdentityReaderTests(unittest.TestCase):
    def test_reads_model_and_target_identity_records(self):
        model = classes.UEModel.from_archive(reader.FArchiveReader(chunk(
            "FMODEL_SKELETON_IDENTITY", 1, model_identity_payload(),
        )))
        anim = classes.UEAnim.from_archive(animation_archive(chunk(
            "FMODEL_TARGET_SKELETON", 1, target_identity_payload(),
        )))

        self.assertEqual(model.skeleton_identity.version, 1)
        self.assertEqual(model.skeleton_identity.skeleton_guid, bytes(range(16)))
        self.assertEqual(model.skeleton_identity.bone_layout_hash, bytes(range(32)))
        self.assertEqual(anim.target_skeleton_identity.source_animation_path, "/Game/Hero/A_Run.A_Run")
        self.assertEqual(anim.target_skeleton_identity.skeleton_path, "/Game/Hero/SK_Hero_Skeleton.SK_Hero_Skeleton")

    def test_preserves_absent_and_present_identity_distinction(self):
        self.assertIsNone(classes.UEModel.from_archive(reader.FArchiveReader(b"")).skeleton_identity)
        self.assertIsNone(classes.UEAnim.from_archive(animation_archive(b"")).target_skeleton_identity)
        identity = classes.UEAnim.from_archive(animation_archive(chunk(
            "FMODEL_TARGET_SKELETON", 1, target_identity_payload(has_guid=0),
        ))).target_skeleton_identity
        self.assertIsNotNone(identity)
        self.assertIsNone(identity.skeleton_guid)

    def test_rejects_duplicate_identity_chunks(self):
        model_chunk = chunk("FMODEL_SKELETON_IDENTITY", 1, model_identity_payload())
        anim_chunk = chunk("FMODEL_TARGET_SKELETON", 1, target_identity_payload())
        with self.assertRaisesRegex(ValueError, "duplicate.*FMODEL_SKELETON_IDENTITY"):
            classes.UEModel.from_archive(reader.FArchiveReader(model_chunk + model_chunk))
        with self.assertRaisesRegex(ValueError, "duplicate.*FMODEL_TARGET_SKELETON"):
            classes.UEAnim.from_archive(animation_archive(anim_chunk + anim_chunk))

    def test_rejects_non_singleton_identity_chunk_counts(self):
        with self.assertRaisesRegex(ValueError, "FMODEL_SKELETON_IDENTITY chunk array_size"):
            classes.UEModel.from_archive(reader.FArchiveReader(chunk(
                "FMODEL_SKELETON_IDENTITY", 0, model_identity_payload(),
            )))
        with self.assertRaisesRegex(ValueError, "FMODEL_TARGET_SKELETON chunk array_size"):
            classes.UEAnim.from_archive(animation_archive(chunk(
                "FMODEL_TARGET_SKELETON", 2, target_identity_payload(),
            )))

    def test_rejects_invalid_versions_booleans_paths_and_trailing_data(self):
        cases = (
            ("FMODEL_SKELETON_IDENTITY", model_identity_payload(version=2), "version"),
            ("FMODEL_SKELETON_IDENTITY", model_identity_payload(path=""), "skeleton_path"),
            ("FMODEL_SKELETON_IDENTITY", model_identity_payload(has_guid=2), "has_skeleton_guid"),
            ("FMODEL_SKELETON_IDENTITY", model_identity_payload() + b"junk", "Trailing"),
        )
        for name, payload, message in cases:
            with self.subTest(name=name, message=message), self.assertRaisesRegex(ValueError, message):
                classes.UEModel.from_archive(reader.FArchiveReader(chunk(name, 1, payload)))

        for payload, message in (
            (target_identity_payload(source_path=""), "source_animation_path"),
            (target_identity_payload(has_guid=2), "has_skeleton_guid"),
            (target_identity_payload() + b"junk", "Trailing"),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                classes.UEAnim.from_archive(animation_archive(chunk("FMODEL_TARGET_SKELETON", 1, payload)))

    def test_rejects_oversized_identity_paths_before_reading_padded_chunks(self):
        oversized_path = b"x" * (classes.MAX_FMODEL_IDENTITY_PATH_BYTES + 1)
        oversized_fstring = struct.pack("<i", len(oversized_path)) + oversized_path
        model_payload = b"\x01" + oversized_fstring + b"\x01" + bytes(range(16)) + bytes(range(32))
        target_payload = oversized_fstring + model_identity_payload()
        target_skeleton_payload = b"\x01" + fstring("/Game/Hero/A_Run.A_Run") + model_payload

        with self.assertRaisesRegex(ValueError, "skeleton_path.*maximum"):
            classes.UEModel.from_archive(reader.FArchiveReader(chunk(
                "FMODEL_SKELETON_IDENTITY", 1, model_payload,
            )))
        with self.assertRaisesRegex(ValueError, "source_animation_path.*maximum"):
            classes.UEAnim.from_archive(animation_archive(chunk(
                "FMODEL_TARGET_SKELETON", 1, target_payload,
            )))
        with self.assertRaisesRegex(ValueError, "skeleton_path.*maximum"):
            classes.UEAnim.from_archive(animation_archive(chunk(
                "FMODEL_TARGET_SKELETON", 1, target_skeleton_payload,
            )))

    def test_rejects_truncated_guid_hash_and_invalid_chunk_framing(self):
        model_payload = model_identity_payload()
        anim_payload = target_identity_payload()
        cases = (
            (classes.UEModel, "FMODEL_SKELETON_IDENTITY", model_payload[:-1], "available data"),
            (classes.UEModel, "FMODEL_SKELETON_IDENTITY", model_payload[:-33], "available data"),
            (classes.UEAnim, "FMODEL_TARGET_SKELETON", anim_payload[:-1], "available data"),
            (classes.UEAnim, "FMODEL_TARGET_SKELETON", anim_payload[:-33], "available data"),
        )
        for parser, name, payload, message in cases:
            with self.subTest(name=name, payload_length=len(payload)), self.assertRaisesRegex(ValueError, message):
                parser.from_archive(
                    animation_archive(chunk(name, 1, payload)) if parser is classes.UEAnim else reader.FArchiveReader(chunk(name, 1, payload))
                )

        with self.assertRaisesRegex(ValueError, "negative byte_size"):
            classes.UEAnim.from_archive(animation_archive(chunk(
                "FMODEL_TARGET_SKELETON", 1, target_identity_payload(), byte_size=-1,
            )))
        with self.assertRaisesRegex(ValueError, "exceeds available data"):
            classes.UEAnim.from_archive(animation_archive(chunk(
                "FMODEL_TARGET_SKELETON", 1, target_identity_payload(), byte_size=len(anim_payload) + 1,
            )))


if __name__ == "__main__":
    unittest.main()
