from __future__ import annotations

import hashlib
import importlib
import sys
import types
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).parents[1]
FIXTURE = Path(__file__).with_name("fixtures") / "fmodel_v2_deployment.uemodel"
PLUGIN_ROOT = REPOSITORY / "plugins" / "blender"


def load_reader():
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
    package_name = "fmodel_v2_deployment_reader"
    package = types.ModuleType(package_name)
    package.__path__ = [str(PLUGIN_ROOT / "io_scene_ueformat")]
    sys.modules[package_name] = package
    return (
        importlib.import_module(f"{package_name}.importer.classes"),
        importlib.import_module(f"{package_name}.importer.reader"),
    )


class FModelV2DeploymentFixtureTests(unittest.TestCase):
    def test_fixture_serializes_v2_interfaces_and_packed_custom_texture(self):
        classes, reader = load_reader()
        archive = reader.FArchiveReader(FIXTURE.read_bytes())
        archive.read_string(len(classes.MAGIC))
        self.assertEqual(archive.read_fstring(), classes.MODEL_IDENTIFIER)
        archive.file_version = classes.EUEFormatVersion(int.from_bytes(archive.read_byte(), byteorder="big"))
        archive.read_fstring()
        self.assertFalse(archive.read_bool())
        archive.metadata["scale"] = 0.01
        model = classes.UEModel.from_archive(archive)

        self.assertEqual([interface.role for interface in model.material_links.pbr_interfaces], [1, 3, 9])
        self.assertEqual([interface.parameter_index for interface in model.material_links.pbr_interfaces], [0, 1, 2])
        self.assertTrue(model.material_links.pbr_interfaces_present)
        self.assertEqual(model.material_links.slots[0].parameters[2].texture.packing, 7)
        self.assertEqual([texture.id for texture in model.textures], [0, 1, 2])

    def test_fixture_hash_is_documented_deterministic_value(self):
        self.assertEqual(
            hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
            "baed6227e36c33d8f177d7540ff30bba0f122753ce3dcbb2c2558d0c56d95d0a",
        )


if __name__ == "__main__":
    unittest.main()
