from __future__ import annotations

import importlib.util
import io
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock
import zipfile


WORKSPACE = Path(__file__).parents[2]
HELPER_PATH = WORKSPACE / "scripts" / "sync_release_sources.py"


def load_helper():
    specification = importlib.util.spec_from_file_location("sync_release_sources", HELPER_PATH)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"Unable to load release helper: {HELPER_PATH}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class ReleaseSourceTests(unittest.TestCase):
    def test_protocol_sources_have_matching_hashes(self):
        helper = load_helper()
        parity = helper.validate_protocol_parity(WORKSPACE)

        self.assertEqual(set(parity), {
            "CUE4Parse/CUE4Parse-Conversion/Materials/MaterialExporter2.cs",
            "CUE4Parse/CUE4Parse-Conversion/UEScene/UESceneBundleExporter.cs",
        })
        for hashes in parity.values():
            self.assertEqual(hashes["canonical"], hashes["aggregate"])

    def test_addon_archive_is_cache_free_and_supports_material_versions_one_and_two(self):
        helper = load_helper()
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "io_scene_ueformat.zip"
            entry_count = helper.build_addon_archive(WORKSPACE, archive_path)

            with zipfile.ZipFile(archive_path) as archive:
                names = archive.namelist()
                self.assertEqual(entry_count, len(names))
                self.assertIn("io_scene_ueformat/importer/classes.py", names)
                self.assertFalse(any("__pycache__" in name or name.endswith(".pyc") for name in names))
                classes = archive.read("io_scene_ueformat/importer/classes.py").decode("utf-8")
                self.assertIn("extension_version not in (1, 2)", classes)

    def test_published_archives_are_cache_free(self):
        helper = load_helper()
        helper.validate_addon_archives(WORKSPACE)

        for archive_path in (WORKSPACE / "io_scene_ueformat.zip", WORKSPACE / "dist" / "io_scene_ueformat.zip"):
            with self.subTest(archive=archive_path):
                with zipfile.ZipFile(archive_path) as archive:
                    names = archive.namelist()
                    self.assertIn("io_scene_ueformat/importer/classes.py", names)
                    self.assertFalse(any("__pycache__" in name or name.endswith(".pyc") for name in names))
                    classes = archive.read("io_scene_ueformat/importer/classes.py").decode("utf-8")
                    self.assertIn("extension_version not in (1, 2)", classes)

    def test_managed_addon_matches_canonical_source(self):
        helper = load_helper()
        helper.validate_managed_addon_parity(WORKSPACE)

    def test_missing_managed_addon_root_skips_deployment_parity_with_message(self):
        helper = load_helper()
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing-addon"
            output = io.StringIO()
            with mock.patch.dict(os.environ, {"UEFORMAT_MANAGED_ADDON_ROOT": str(missing)}):
                with mock.patch("sys.stdout", output):
                    validated = helper.validate_managed_addon_parity(WORKSPACE)

        self.assertFalse(validated)
        self.assertIn("Managed addon parity skipped", output.getvalue())
        self.assertIn(str(missing), output.getvalue())

    def test_configured_managed_addon_root_validates_parity(self):
        helper = load_helper()
        with tempfile.TemporaryDirectory() as directory:
            managed = Path(directory) / "non-default-addon"
            shutil.copytree(WORKSPACE / helper.ADDON_ROOT, managed)
            with mock.patch.dict(os.environ, {"UEFORMAT_MANAGED_ADDON_ROOT": str(managed)}):
                self.assertTrue(helper.validate_managed_addon_parity(WORKSPACE))

    def test_managed_addon_drift_is_rejected(self):
        helper = load_helper()
        with tempfile.TemporaryDirectory() as directory:
            managed = Path(directory) / "io_scene_ueformat"
            shutil.copytree(WORKSPACE / helper.ADDON_ROOT, managed)
            (managed / "importer" / "classes.py").write_text("stale", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Managed addon source mismatch: importer/classes.py"):
                helper.validate_managed_addon_parity(WORKSPACE, managed)

    def test_archives_match_source_and_each_other(self):
        helper = load_helper()
        root_archive = WORKSPACE / "io_scene_ueformat.zip"
        dist_archive = WORKSPACE / "dist" / "io_scene_ueformat.zip"
        self.assertEqual(root_archive.read_bytes(), dist_archive.read_bytes())
        helper.validate_addon_archives(WORKSPACE)

    def test_stale_archive_and_unsafe_member_names_are_rejected(self):
        helper = load_helper()
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            source = workspace / helper.ADDON_ROOT
            shutil.copytree(WORKSPACE / helper.ADDON_ROOT, source)
            for archive_relative in helper.ADDON_ARCHIVES:
                helper.build_addon_archive(workspace, workspace / archive_relative)

            stale_archive = workspace / helper.ADDON_ARCHIVES[0]
            with zipfile.ZipFile(stale_archive, "a") as archive:
                archive.writestr("io_scene_ueformat/importer/classes.py", b"stale")
            with self.assertRaisesRegex(ValueError, "duplicate member"):
                helper.validate_addon_archives(workspace)

            helper.build_addon_archive(workspace, stale_archive)
            with zipfile.ZipFile(stale_archive, "a") as archive:
                archive.writestr("io_scene_ueformat/../outside.py", b"unsafe")
            with self.assertRaisesRegex(ValueError, "unsafe member path"):
                helper.validate_addon_archives(workspace)

    def test_stale_archive_member_is_rejected(self):
        helper = load_helper()
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            source = workspace / helper.ADDON_ROOT
            shutil.copytree(WORKSPACE / helper.ADDON_ROOT, source)
            for archive_relative in helper.ADDON_ARCHIVES:
                helper.build_addon_archive(workspace, workspace / archive_relative)

            stale_source = source / "importer" / "classes.py"
            stale_source.write_text("stale", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Addon archive source mismatch"):
                helper.validate_addon_archives(workspace)


if __name__ == "__main__":
    unittest.main()
