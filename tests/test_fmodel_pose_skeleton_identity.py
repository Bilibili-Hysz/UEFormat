from __future__ import annotations

import unittest

from tests.test_fmodel_skeleton_identity import (
    animation_archive,
    chunk,
    classes,
    fstring,
    model_identity_payload,
    reader,
)
from io_scene_ueformat.importer import fmodel
from pathlib import Path
import hashlib


def pose_archive(data: bytes):
    return animation_archive(data)


def target_pose_identity_payload(*, source_path: str = "/Game/Hero/P_Pose.P_Pose", **kwargs) -> bytes:
    identity = model_identity_payload(**kwargs)
    return identity[:1] + fstring(source_path) + identity[1:]


class FModelPoseSkeletonIdentityReaderTests(unittest.TestCase):
    def test_reads_target_pose_identity_record(self):
        pose = classes.UEPose.from_archive(pose_archive(chunk(
            "FMODEL_TARGET_SKELETON", 1, target_pose_identity_payload(),
        )))

        self.assertEqual(pose.target_skeleton_identity.version, 1)
        self.assertEqual(
            pose.target_skeleton_identity.source_pose_path,
            "/Game/Hero/P_Pose.P_Pose",
        )
        self.assertEqual(
            pose.target_skeleton_identity.skeleton_path,
            "/Game/Hero/SK_Hero_Skeleton.SK_Hero_Skeleton",
        )

    def test_legacy_pose_without_identity_remains_valid(self):
        pose = classes.UEPose.from_archive(pose_archive(b""))

        self.assertIsNone(pose.target_skeleton_identity)

    def test_rejects_duplicate_and_non_singleton_target_chunks(self):
        target = chunk("FMODEL_TARGET_SKELETON", 1, target_pose_identity_payload())

        with self.assertRaisesRegex(ValueError, "duplicate.*FMODEL_TARGET_SKELETON"):
            classes.UEPose.from_archive(pose_archive(target + target))
        with self.assertRaisesRegex(ValueError, "FMODEL_TARGET_SKELETON chunk array_size"):
            classes.UEPose.from_archive(pose_archive(
                chunk("FMODEL_TARGET_SKELETON", 2, target_pose_identity_payload())
            ))

    def test_rejects_invalid_target_identity_payload(self):
        cases = (
            (target_pose_identity_payload(source_path=""), "source_pose_path"),
            (target_pose_identity_payload(version=2), "version"),
            (target_pose_identity_payload(has_guid=2), "has_skeleton_guid"),
            (target_pose_identity_payload() + b"junk", "Trailing"),
            (target_pose_identity_payload()[:-1], "available data"),
        )

        for payload, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                classes.UEPose.from_archive(pose_archive(
                    chunk("FMODEL_TARGET_SKELETON", 1, payload)
                ))

    def test_deterministic_fixture_contains_target_pose_identity(self):
        fixture = Path(__file__).with_name("fixtures") / "fmodel_target_skeleton.uepose"
        self.assertEqual(
            hashlib.sha256(fixture.read_bytes()).hexdigest(),
            "b1a77dc3fb6b88682a35c5549b1cc50946c942a2e062a2b9c860bed125e732cc",
        )
        raw = fixture.read_bytes()
        header = reader.FArchiveReader(raw)
        self.assertEqual(header.read_string(len(classes.MAGIC)), classes.MAGIC)
        self.assertEqual(header.read_fstring(), classes.POSE_IDENTIFIER)
        header.read_byte()
        header.read_fstring()
        header.read_bool()
        body = reader.FArchiveReader(raw[header.data.tell():])
        body.file_version = classes.EUEFormatVersion.AddPoseExport
        body.metadata["scale"] = 0.01
        pose = classes.UEPose.from_archive(body)
        self.assertEqual(len(pose.poses), 1)
        self.assertEqual(pose.target_skeleton_identity.source_pose_path, "/Game/Hero/P_Pose.P_Pose")


class FModelPoseSkeletonIdentityValidationTests(unittest.TestCase):
    def setUp(self):
        self.target = {
            "version": 1,
            "source_pose_path": "/Game/Hero/P_Pose.P_Pose",
            "skeleton_path": "/Game/Hero/SK_Hero_Skeleton.SK_Hero_Skeleton",
            "has_skeleton_guid": True,
            "skeleton_guid": "00" * 16,
            "bone_layout_hash": "11" * 32,
        }
        self.armature = dict(self.target)
        self.armature.pop("source_pose_path")

    def test_accepts_matching_pose_and_armature_identity(self):
        fmodel.verify_fmodel_target_pose(self.target, self.armature)

    def test_rejects_pose_skeleton_mismatch(self):
        with self.assertRaisesRegex(ValueError, "pose skeleton path mismatch"):
            fmodel.verify_fmodel_target_pose(
                self.target,
                dict(self.armature, skeleton_path="/Game/Other/SK_Other.SK_Other"),
            )

    def test_reports_missing_pose_identity_without_mutation(self):
        issues = fmodel.pose_identity_issues(self.target, None)
        self.assertEqual(issues, ["FModel armature skeleton identity is missing"])


if __name__ == "__main__":
    unittest.main()
