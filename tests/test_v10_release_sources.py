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
    specification = importlib.util.spec_from_file_location("sync_release_sources_v10", HELPER_PATH)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"Unable to load release helper: {HELPER_PATH}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class V10ReleaseSourceTests(unittest.TestCase):
    def make_workspace(self, helper, workspace: Path) -> None:
        for relative_path in helper.PROTOCOL_FILES:
            canonical = workspace / helper.CANONICAL_ROOT / relative_path
            aggregate = workspace / helper.AGGREGATE_ROOT / relative_path
            canonical.parent.mkdir(parents=True, exist_ok=True)
            aggregate.parent.mkdir(parents=True, exist_ok=True)
            canonical.write_text("canonical protocol source", encoding="utf-8")
            aggregate.write_text("canonical protocol source", encoding="utf-8")

        shutil.copytree(WORKSPACE / helper.ADDON_ROOT, workspace / helper.ADDON_ROOT)
        for archive_relative in helper.ADDON_ARCHIVES:
            helper.build_addon_archive(workspace, workspace / archive_relative)

    def test_v10_metadata_matches_manifest_and_bl_info(self):
        helper = load_helper()
        metadata = helper.validate_addon_metadata(WORKSPACE)

        self.assertEqual(metadata["version"], (0, 10, 0))
        self.assertEqual(metadata["blender_minimum"], (4, 2, 0))
        self.assertEqual(metadata["name"], "UEFormat (.uemodel / .ueanim / .uepose)")
        self.assertEqual(len(metadata["wheels"]), 3)

    def test_v10_archive_contains_runtime_boundaries_and_no_retired_modules(self):
        helper = load_helper()
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "io_scene_ueformat.zip"
            helper.build_addon_archive(WORKSPACE, archive_path)

            with zipfile.ZipFile(archive_path) as archive:
                names = set(archive.namelist())

            self.assertTrue(set(helper.V10_REQUIRED_ADDON_MEMBERS) <= names)
            self.assertFalse(set(helper.V9_RETIRED_ADDON_MEMBERS) & names)
            self.assertTrue(
                any(name.startswith("io_scene_ueformat/importer/extensions/fmodel/") for name in names)
            )

    def test_v10_archive_is_exactly_reproducible_and_matches_source(self):
        helper = load_helper()
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            shutil.copytree(WORKSPACE / helper.ADDON_ROOT, workspace / helper.ADDON_ROOT)
            first = workspace / "io_scene_ueformat.zip"
            second = workspace / "dist" / "io_scene_ueformat.zip"
            helper.build_addon_archive(workspace, first)
            helper.build_addon_archive(workspace, second)

            self.assertEqual(first.read_bytes(), second.read_bytes())
            helper.validate_addon_archives(workspace)

    def test_retired_v9_member_fails_archive_preflight(self):
        helper = load_helper()
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            source = workspace / helper.ADDON_ROOT
            shutil.copytree(WORKSPACE / helper.ADDON_ROOT, source)
            retired = source / "importer" / "classes.py"
            retired.write_text("retired V9 module", encoding="utf-8")
            for archive_relative in helper.ADDON_ARCHIVES:
                helper.build_addon_archive(workspace, workspace / archive_relative)

            with self.assertRaisesRegex(ValueError, "retired V9 importer members"):
                helper.validate_addon_archives(workspace)

    def test_missing_v10_member_fails_archive_preflight(self):
        helper = load_helper()
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            source = workspace / helper.ADDON_ROOT
            shutil.copytree(WORKSPACE / helper.ADDON_ROOT, source)
            (source / "importer" / "wire.py").unlink()
            for archive_relative in helper.ADDON_ARCHIVES:
                helper.build_addon_archive(workspace, workspace / archive_relative)

            with self.assertRaisesRegex(ValueError, "missing V10 members"):
                helper.validate_addon_archives(workspace)

    def test_v10_preflight_is_independent_of_user_installation(self):
        helper = load_helper()
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self.make_workspace(helper, workspace)
            output = io.StringIO()
            with mock.patch.dict(
                os.environ,
                {
                    "UEFORMAT_MANAGED_ADDON_ROOT": str(workspace / "missing-managed-addon"),
                    "FMODEL_TOOLS_V2_ROOT": str(workspace / "missing-v2"),
                },
            ), mock.patch("sys.stdout", output):
                result = helper.run_preflight(workspace)

        self.assertEqual(result, 0)
        self.assertIn("Managed addon parity skipped", output.getvalue())


if __name__ == "__main__":
    unittest.main()
