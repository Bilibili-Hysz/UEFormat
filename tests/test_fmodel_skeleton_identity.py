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
            "skeleton_path": "/Game/Hero/SK_Hero_Skeleton.SK_Hero_Skeleton",
            "has_skeleton_guid": True,
            "skeleton_guid": "00" * 16,
            "bone_layout_hash": "11" * 32,
        }

    def test_normalized_exact_match(self):
        fmodel.verify_fmodel_target_skeleton(self.identity, dict(self.identity))

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

    def test_rejects_invalid_skeleton_paths_without_normalization(self):
        invalid_paths = (
            " /Game/Hero/SK_Hero_Skeleton.SK_Hero_Skeleton",
            "/Game/Hero/SK_Hero_Skeleton.SK_Hero_Skeleton ",
            "\\Game\\Hero\\SK_Hero_Skeleton.SK_Hero_Skeleton",
            "C:/Game/Hero/SK_Hero_Skeleton.SK_Hero_Skeleton",
            "file:/Game/Hero/SK_Hero_Skeleton.SK_Hero_Skeleton",
            "FILE:/Game/Hero/SK_Hero_Skeleton.SK_Hero_Skeleton",
            "File:/Game/Hero/SK_Hero_Skeleton.SK_Hero_Skeleton",
            "fIlE:/Game/Hero/SK_Hero_Skeleton.SK_Hero_Skeleton",
            "/Game//Hero/SK_Hero_Skeleton.SK_Hero_Skeleton",
            "/Game/./Hero/SK_Hero_Skeleton.SK_Hero_Skeleton",
            "/Game/../Hero/SK_Hero_Skeleton.SK_Hero_Skeleton",
        )

        for path in invalid_paths:
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "malformed|invalid"):
                fmodel.verify_fmodel_target_skeleton(dict(self.identity, skeleton_path=path), dict(self.identity))

    def test_pose_verifier_rejects_invalid_skeleton_paths_without_normalization(self):
        invalid_paths = (
            " /Game/Hero/SK_Hero_Skeleton.SK_Hero_Skeleton",
            "/Game/Hero/SK_Hero_Skeleton.SK_Hero_Skeleton ",
            "\\Game\\Hero\\SK_Hero_Skeleton.SK_Hero_Skeleton",
            "C:/Game/Hero/SK_Hero_Skeleton.SK_Hero_Skeleton",
            "file:/Game/Hero/SK_Hero_Skeleton.SK_Hero_Skeleton",
            "FILE:/Game/Hero/SK_Hero_Skeleton.SK_Hero_Skeleton",
            "File:/Game/Hero/SK_Hero_Skeleton.SK_Hero_Skeleton",
            "fIlE:/Game/Hero/SK_Hero_Skeleton.SK_Hero_Skeleton",
            "/Game//Hero/SK_Hero_Skeleton.SK_Hero_Skeleton",
            "/Game/./Hero/SK_Hero_Skeleton.SK_Hero_Skeleton",
            "/Game/../Hero/SK_Hero_Skeleton.SK_Hero_Skeleton",
        )

        for path in invalid_paths:
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "malformed|invalid"):
                fmodel.verify_fmodel_target_pose(
                    {
                        "version": 1,
                        "source_pose_path": "/Game/Hero/P_Hero.P_Hero",
                        "skeleton_path": path,
                        "has_skeleton_guid": True,
                        "skeleton_guid": "00" * 16,
                        "bone_layout_hash": "11" * 32,
                    },
                    dict(self.identity),
                )

    def test_pose_verifier_rejects_unc_like_paths(self):
        invalid_paths = (
            "//server/share/SK_Hero_Skeleton.SK_Hero_Skeleton",
            "//Game/Hero/SK_Hero_Skeleton.SK_Hero_Skeleton",
        )
        for path in invalid_paths:
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "malformed|invalid"):
                fmodel.verify_fmodel_target_skeleton(dict(self.identity, skeleton_path=path), dict(self.identity))
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "malformed|invalid"):
                fmodel.verify_fmodel_target_pose(
                    {
                        "version": 1,
                        "source_pose_path": "/Game/Hero/P_Hero.P_Hero",
                        "skeleton_path": path,
                        "has_skeleton_guid": True,
                        "skeleton_guid": "00" * 16,
                        "bone_layout_hash": "11" * 32,
                    },
                    dict(self.identity),
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

    def test_strict_rejects_invalid_provenance_paths_without_normalization(self):
        invalid_paths = (
            " /Game/Hero/A_Run.A_Run",
            "/Game/Hero/A_Run.A_Run ",
            "\\Game\\Hero\\A_Run.A_Run",
            "C:/Game/Hero/A_Run.A_Run",
            "file:/Game/Hero/A_Run.A_Run",
            "FILE:/Game/Hero/A_Run.A_Run",
            "File:/Game/Hero/A_Run.A_Run",
            "fIlE:/Game/Hero/A_Run.A_Run",
            "/Game//Hero/A_Run.A_Run",
            "/Game/./Hero/A_Run.A_Run",
            "/Game/../Hero/A_Run.A_Run",
        )
        for path in invalid_paths:
            with self.subTest(field="source_animation_path", path=path), self.assertRaisesRegex(ValueError, "malformed|invalid"):
                self.plan(target_identity=dict(self.target_identity, source_animation_path=path))

        for path in ("//server/share/A_Run.A_Run", "//Game/Hero/A_Run.A_Run"):
            with self.subTest(field="source_animation_path", path=path), self.assertRaisesRegex(ValueError, "malformed|invalid"):
                self.plan(target_identity=dict(self.target_identity, source_animation_path=path))

        skeleton_invalid_paths = (
            " /Game/Hero/SK_Hero_Skeleton.SK_Hero_Skeleton",
            "/Game/Hero/SK_Hero_Skeleton.SK_Hero_Skeleton ",
            "\\Game\\Hero\\SK_Hero_Skeleton.SK_Hero_Skeleton",
            "C:/Game/Hero/SK_Hero_Skeleton.SK_Hero_Skeleton",
            "file:/Game/Hero/SK_Hero_Skeleton.SK_Hero_Skeleton",
            "FILE:/Game/Hero/SK_Hero_Skeleton.SK_Hero_Skeleton",
            "File:/Game/Hero/SK_Hero_Skeleton.SK_Hero_Skeleton",
            "fIlE:/Game/Hero/SK_Hero_Skeleton.SK_Hero_Skeleton",
            "/Game//Hero/SK_Hero_Skeleton.SK_Hero_Skeleton",
            "/Game/./Hero/SK_Hero_Skeleton.SK_Hero_Skeleton",
            "/Game/../Hero/SK_Hero_Skeleton.SK_Hero_Skeleton",
        )
        for path in skeleton_invalid_paths:
            with self.subTest(field="skeleton_path", path=path), self.assertRaisesRegex(ValueError, "malformed|invalid"):
                self.plan(target_identity=dict(self.target_identity, skeleton_path=path))
            with self.subTest(field="armature_skeleton_path", path=path), self.assertRaisesRegex(ValueError, "malformed|invalid"):
                self.plan(armature_identity=dict(self.armature_identity, skeleton_path=path))

        for path in ("//server/share/SK_Hero_Skeleton.SK_Hero_Skeleton", "//Game/Hero/SK_Hero_Skeleton.SK_Hero_Skeleton"):
            with self.subTest(field="skeleton_path", path=path), self.assertRaisesRegex(ValueError, "malformed|invalid"):
                self.plan(target_identity=dict(self.target_identity, skeleton_path=path))
            with self.subTest(field="armature_skeleton_path", path=path), self.assertRaisesRegex(ValueError, "malformed|invalid"):
                self.plan(armature_identity=dict(self.armature_identity, skeleton_path=path))

    def test_strict_rejects_invalid_pose_identity_metadata(self):
        invalid_paths = (
            " /Game/Hero/P_Hero.P_Hero",
            "/Game/Hero/P_Hero.P_Hero ",
            "\\Game\\Hero\\P_Hero.P_Hero",
            "C:/Game/Hero/P_Hero.P_Hero",
            "file:/Game/Hero/P_Hero.P_Hero",
            "/Game//Hero/P_Hero.P_Hero",
            "/Game/./Hero/P_Hero.P_Hero",
            "/Game/../Hero/P_Hero.P_Hero",
            "//server/share/P_Hero.P_Hero",
        )
        armature = dict(self.armature_identity)
        for path in invalid_paths:
            target = {
                "version": 1,
                "source_pose_path": path,
                "skeleton_path": self.target_identity["skeleton_path"],
                "has_skeleton_guid": True,
                "skeleton_guid": "00" * 16,
                "bone_layout_hash": "11" * 32,
            }
            with self.subTest(path=path):
                self.assertIn("malformed", fmodel.pose_identity_issues(target, armature)[0])

    def test_legal_identity_still_binds(self):
        plan = self.plan(tracks=("root",), bones=("root",), strict=True)
        self.assertEqual(plan.bound_bone_names, ("root",))
        self.assertEqual(plan.bound_track_count, 1)

    def test_legacy_import_without_either_identity_is_not_provenance_strict(self):
        plan = fmodel.plan_fmodel_animation_binding(None, None, ("root",), ("Root", "ROOT"))
        self.assertEqual(plan.bound_track_count, 1)
        self.assertEqual(plan.bound_bone_names, ("Root",))
        self.assertFalse(plan.provenance_expected)


def fstring(value: str) -> bytes:
    encoded = value.encode("utf-8") + b"\0"
    return struct.pack("<i", len(encoded)) + encoded


def raw_fstring(payload: bytes) -> bytes:
    return struct.pack("<i", len(payload)) + payload


def chunk(name: str, count: int, payload: bytes, *, byte_size: int | None = None) -> bytes:
    return fstring(name) + struct.pack("<ii", count, len(payload) if byte_size is None else byte_size) + payload


def animation_archive(data: bytes):
    archive = reader.FArchiveReader(data)
    archive.file_version = classes.EUEFormatVersion.SerializeAssetMetadata
    return archive



def animation_metadata_payload() -> bytes:
    return (
        struct.pack("<if", 1, 30.0)
        + fstring("")
        + bytes((0, 0))
        + struct.pack("<i", 0)
    )


def model_identity_payload(*, version: int = 1, path: str = "/Game/Hero/SK_Hero_Skeleton.SK_Hero_Skeleton", has_guid: int = 1, guid: bytes = bytes(range(16)), layout_hash: bytes = bytes(range(32))) -> bytes:
    return bytes([version]) + fstring(path) + bytes([has_guid]) + (guid if has_guid == 1 else b"") + layout_hash


def target_identity_payload(*, source_path: str = "/Game/Hero/A_Run.A_Run", **kwargs) -> bytes:
    return b"\x01" + fstring(source_path) + model_identity_payload(**kwargs)[1:]


class FModelSkeletonIdentityReaderTests(unittest.TestCase):
    def test_contract_uses_explicit_unc_rejection(self):
        source = Path(importlib.import_module("io_scene_ueformat.importer.protocol").__file__).read_text(encoding="utf-8")
        self.assertIn('path.startswith("//")', source)

    def test_logical_unreal_path_rejects_mixed_case_file_uri_prefix(self):
        cases = (
            ("skeleton_path", "FILE:/Game/Hero"),
            ("skeleton_path", "File:/Game/Hero"),
            ("skeleton_path", "fIlE:/Game/Hero"),
            ("source_animation_path", "FILE:/Game/Hero/A_Run.A_Run"),
            ("source_animation_path", "File:/Game/Hero/A_Run.A_Run"),
            ("source_animation_path", "fIlE:/Game/Hero/A_Run.A_Run"),
        )

        for field_name, path in cases:
            with self.subTest(field_name=field_name, path=path), self.assertRaisesRegex(ValueError, field_name):
                classes._validate_logical_unreal_path(path, field_name)

    def test_rejects_invalid_logical_unreal_paths_for_model_and_target_identity(self):
        invalid_paths = (
            " ",
            "/Game/He\0ro",
            "C:/Game/Hero",
            "\\\\server\\share",
            "//server/share",
            "//Game/Hero",
            "file:/Game/Hero",
            "FILE:/Game/Hero",
            "File:/Game/Hero",
            "fIlE:/Game/Hero",
            "Game/Hero",
            "/Game//Hero",
            "/Game/./Hero",
            "/Game/../Hero",
            "/Game\\Hero",
        )

        for path in invalid_paths:
            with self.subTest(kind="model", path=repr(path)), self.assertRaisesRegex(ValueError, "skeleton_path"):
                classes.UEModel.from_archive(reader.FArchiveReader(chunk(
                    "FMODEL_SKELETON_IDENTITY", 1, model_identity_payload(path=path),
                )))

            with self.subTest(kind="target", path=repr(path)), self.assertRaisesRegex(ValueError, "source_animation_path"):
                classes.UEAnim.from_archive(animation_archive(chunk(
                    "FMODEL_TARGET_SKELETON", 1, target_identity_payload(source_path=path),
                )))

    def test_rejects_malformed_identity_fstrings(self):
        malformed_cases = (
            ("model_empty", b"\x01" + raw_fstring(b"") + b"\x01" + bytes(range(16)) + bytes(range(32)), "length|Invalid"),
            ("model_embedded_nul", b"\x01" + raw_fstring(b"/Game/Hero\0/SK_Hero_Skeleton.SK_Hero_Skeleton") + b"\x01" + bytes(range(16)) + bytes(range(32)), "embedded NUL"),
            ("model_invalid_utf8", b"\x01" + raw_fstring(b"/Game/Hero/\xff\xfe") + b"\x01" + bytes(range(16)) + bytes(range(32)), "invalid UTF-8"),
            ("target_empty", b"\x01" + raw_fstring(b"") + fstring("/Game/Hero/SK_Hero_Skeleton.SK_Hero_Skeleton") + b"\x01" + bytes(range(16)) + bytes(range(32)), "length|Invalid"),
            ("target_embedded_nul", b"\x01" + raw_fstring(b"/Game/Hero/A_Run\0.A_Run") + fstring("/Game/Hero/SK_Hero_Skeleton.SK_Hero_Skeleton") + b"\x01" + bytes(range(16)) + bytes(range(32)), "embedded NUL"),
            ("target_invalid_utf8", b"\x01" + raw_fstring(b"/Game/Hero/\xff\xfe") + fstring("/Game/Hero/SK_Hero_Skeleton.SK_Hero_Skeleton") + b"\x01" + bytes(range(16)) + bytes(range(32)), "invalid UTF-8"),
            ("pose_empty", b"\x01" + raw_fstring(b"") + fstring("/Game/Hero/SK_Hero_Skeleton.SK_Hero_Skeleton") + b"\x01" + bytes(range(16)) + bytes(range(32)), "length|Invalid"),
            ("pose_embedded_nul", b"\x01" + raw_fstring(b"/Game/Hero\0/P_Hero.P_Hero") + fstring("/Game/Hero/SK_Hero_Skeleton.SK_Hero_Skeleton") + b"\x01" + bytes(range(16)) + bytes(range(32)), "embedded NUL"),
            ("pose_invalid_utf8", b"\x01" + raw_fstring(b"/Game/Hero/\xff\xfe") + fstring("/Game/Hero/SK_Hero_Skeleton.SK_Hero_Skeleton") + b"\x01" + bytes(range(16)) + bytes(range(32)), "invalid UTF-8"),
        )

        for name, payload, message in malformed_cases:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, message):
                if name.startswith("pose_"):
                    classes.UEAnim.from_archive(animation_archive(chunk("FMODEL_TARGET_SKELETON", 1, payload)))
                elif name.startswith("target_"):
                    classes.UEAnim.from_archive(animation_archive(chunk("FMODEL_TARGET_SKELETON", 1, payload)))
                else:
                    classes.UEModel.from_archive(reader.FArchiveReader(chunk("FMODEL_SKELETON_IDENTITY", 1, payload)))

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

    def test_strict_fmodel_animation_profile_accepts_canonical_chunks(self):
        payload = (
            chunk("METADATA", 1, animation_metadata_payload())
            + chunk("TRACKS", 0, b"")
            + chunk("FMODEL_TARGET_SKELETON", 1, target_identity_payload())
        )
        animation = classes.UEAnim.from_archive(
            animation_archive(payload),
            strict_fmodel_profile=True,
        )
        self.assertIsNotNone(animation.target_skeleton_identity)

    def test_strict_fmodel_animation_profile_rejects_missing_duplicate_and_order_drift(self):
        metadata = chunk("METADATA", 1, animation_metadata_payload())
        tracks = chunk("TRACKS", 0, b"")
        target = chunk("FMODEL_TARGET_SKELETON", 1, target_identity_payload())
        cases = (
            (tracks + target, "missing required chunk: METADATA"),
            (metadata + tracks + tracks + target, "duplicate known chunk: TRACKS"),
            (tracks + metadata + target, "chunk order"),
        )
        for payload, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                classes.UEAnim.from_archive(
                    animation_archive(payload),
                    strict_fmodel_profile=True,
                )

    def test_strict_animation_option_requires_fmodel_target_identity(self):
        payload = (
            chunk("METADATA", 1, animation_metadata_payload())
            + chunk("TRACKS", 0, b"")
        )
        with self.assertRaisesRegex(ValueError, "missing required chunk: FMODEL_TARGET_SKELETON"):
            classes.UEAnim.from_archive(
                animation_archive(payload),
                strict_fmodel_profile=True,
            )

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
