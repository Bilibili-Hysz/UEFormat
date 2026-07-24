from __future__ import annotations

import contextlib
import hashlib
import importlib
import io
import struct
import sys
import types
import unittest
from pathlib import Path

import numpy as np


REPOSITORY = Path(__file__).parents[1]
FIXTURE = Path(__file__).with_name("fixtures") / "fmodel_golden.uemodel"
PLUGIN_ROOT = REPOSITORY / "plugins" / "blender"
UPSTREAM_PLUGIN_ROOT = REPOSITORY.parents[0] / "UEFormat" / "plugins" / "blender"


def load_modules(root: Path, package_name: str):
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

    for key in tuple(sys.modules):
        if key == package_name or key.startswith(f"{package_name}."):
            del sys.modules[key]
    package = types.ModuleType(package_name)
    package.__path__ = [str(root / "io_scene_ueformat")]
    sys.modules[package_name] = package
    return (
        importlib.import_module(f"{package_name}.importer.classes"),
        importlib.import_module(f"{package_name}.importer.reader"),
    )


class FModelGoldenFixtureTests(unittest.TestCase):
    def test_fmodel_reader_decodes_standard_and_extension_data(self):
        classes, reader = load_modules(PLUGIN_ROOT, "fmodel_golden_reader")
        archive = reader.FArchiveReader(FIXTURE.read_bytes())
        archive.read_string(len(classes.MAGIC))
        self.assertEqual(archive.read_fstring(), classes.MODEL_IDENTIFIER)
        archive.file_version = classes.EUEFormatVersion(int.from_bytes(archive.read_byte(), byteorder="big"))
        archive.read_fstring()
        self.assertFalse(archive.read_bool())
        archive.metadata["scale"] = 0.01
        model = classes.UEModel.from_archive(archive)

        self.assertEqual(len(model.lods), 1)
        self.assertEqual(model.lods[0].vertices.shape, (3, 3))
        lod = model.lods[0]
        self.assertEqual(lod.indices.tolist(), [[0, 1, 2]])
        self.assertEqual([material.material_name for material in model.lods[0].materials], ["M_GoldenBody", "M_GoldenOverlay"])
        self.assertEqual(len(model.material_links.slots), 2)
        self.assertEqual([parameter.name for parameter in model.material_links.slots[0].parameters], ["BaseColor", "Normal", "WetnessMask", "Roughness", "Tint"])
        self.assertEqual(model.material_links.slots[1].parameters[0].texture_id, 1)
        self.assertEqual(len(model.textures), 3)
        self.assertEqual([resource.id for resource in model.textures], [0, 1, 2])

    def test_v9_lod_preserves_unreal_clockwise_indices_after_basis_reflection(self):
        classes, reader = load_modules(PLUGIN_ROOT, "fmodel_v9_winding_reader")

        def fstring(value: str) -> bytes:
            encoded = value.encode()
            return struct.pack("i", len(encoded)) + encoded

        def section(name: str, count: int, payload: bytes) -> bytes:
            return fstring(name) + struct.pack("ii", count, len(payload)) + payload

        vertices = struct.pack("9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)
        # The serializer emits this clockwise order in Unreal coordinates.
        indices = struct.pack("3I", 0, 2, 1)
        normals = struct.pack("12f", 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1)
        lod_payload = (
            section("VERTICES", 3, vertices)
            + section("INDICES", 3, indices)
            + section("NORMALS", 3, normals)
        )
        archive = reader.FArchiveReader(fstring("LOD0") + struct.pack("i", len(lod_payload)) + lod_payload)
        archive.file_version = classes.EUEFormatVersion.AddPoseExport
        archive.metadata["scale"] = 1.0

        lod = classes.UEModelLOD.from_archive(archive)
        self.assertEqual(lod.indices.tolist(), [[0, 2, 1]])
        face = lod.vertices[lod.indices[0]]
        face_normal = np.cross(face[1] - face[0], face[2] - face[0])
        self.assertGreater(float(np.dot(face_normal, lod.normals[0])), 0)

    def test_unmodified_upstream_reader_warns_and_skips_extensions(self):
        classes, reader = load_modules(UPSTREAM_PLUGIN_ROOT, "upstream_golden_reader")
        archive = reader.FArchiveReader(FIXTURE.read_bytes())
        archive.read_string(len(classes.MAGIC))
        self.assertEqual(archive.read_fstring(), classes.MODEL_IDENTIFIER)
        archive.file_version = classes.EUEFormatVersion(int.from_bytes(archive.read_byte(), byteorder="big"))
        archive.read_fstring()
        self.assertFalse(archive.read_bool())
        archive.metadata["scale"] = 0.01
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            model = classes.UEModel.from_archive(archive)

        self.assertEqual(len(model.lods), 1)
        self.assertEqual(model.lods[0].vertices.shape, (3, 3))
        self.assertEqual([material.material_name for material in model.lods[0].materials], ["M_GoldenBody", "M_GoldenOverlay"])
        warnings = output.getvalue()
        self.assertIn("Unknown Model Data: FMODEL_MATERIALS", warnings)
        self.assertIn("Unknown Model Data: FMODEL_TEXTURES", warnings)
        self.assertIn("Unknown Model Data: FUTURE_GOLDEN", warnings)

    def test_fixture_hash_is_documented_golden_value(self):
        self.assertEqual(
            hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
            "d5f0724e6d45824148f9d572122aedc2eb2ef05312fc8da58816fd38b6346722",
        )


if __name__ == "__main__":
    unittest.main()
