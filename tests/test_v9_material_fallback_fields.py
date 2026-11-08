"""The v9 material path must not read fields MaterialDto does not have.

A legacy file carries no source-slot map, so the builder falls back to deriving one from the
dense MATERIALS array. That fallback read material.source_slot_index - a field MaterialDto
declares nowhere - so every v9 import raised AttributeError. The V10 gate path always supplies
an explicit map, which is why the regression stayed invisible until the fixtures were imported
directly.

These tests pin the fallback against the real DTO.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "plugins" / "blender"))

from io_scene_ueformat.importer.dto.model import MaterialDto  # noqa: E402


class MaterialDtoFieldTest(unittest.TestCase):
    def test_dto_declares_exactly_the_fields_the_reader_fills(self):
        fields = set(MaterialDto.__dataclass_fields__)
        self.assertEqual(
            fields,
            {"material_name", "material_path", "first_index", "num_faces"},
            "MaterialDto changed shape; the material builder must be updated with it",
        )

    def test_source_slot_index_is_not_a_field(self):
        self.assertNotIn(
            "source_slot_index",
            MaterialDto.__dataclass_fields__,
            "the builder used to read this; if it is added, update runtime_material_builder",
        )


class MaterialBuilderSourceTest(unittest.TestCase):
    """Guard the builder's source text, since the bug was a wrong attribute name."""

    def setUp(self) -> None:
        self.source = (
            REPOSITORY
            / "plugins"
            / "blender"
            / "io_scene_ueformat"
            / "importer"
            / "runtime_material_builder.py"
        ).read_text(encoding="utf-8")

    def test_builder_never_reads_a_phantom_field(self):
        # Only executable lines matter: the comment that explains the old bug names the field,
        # and a source-text scan that cannot tell code from prose would forbid documenting it.
        code_lines = [
            line for line in self.source.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        offenders = [line for line in code_lines if "source_slot_index" in line]
        self.assertEqual(
            offenders,
            [],
            f"runtime_material_builder reads a field MaterialDto does not declare: {offenders}",
        )

    def test_fallback_derives_the_map_from_position(self):
        # The dense array is ordered, so position is the index.
        self.assertIn("for blender_index, _material in enumerate(lod_materials)", self.source)


if __name__ == "__main__":
    unittest.main()
