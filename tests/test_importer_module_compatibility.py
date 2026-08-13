from __future__ import annotations

import importlib
import sys
import types
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).parents[1] / "plugins" / "blender"
PACKAGE_ROOT = PLUGIN_ROOT / "io_scene_ueformat"


mathutils = types.ModuleType("mathutils")
mathutils.Quaternion = tuple
mathutils.Vector = tuple
sys.modules.setdefault("mathutils", mathutils)

bpy = types.ModuleType("bpy")
bpy_types = types.ModuleType("bpy.types")
bpy_types.PoseBone = object
bpy_types.bpy_prop_collection = object
bpy.types = bpy_types
sys.modules.setdefault("bpy", bpy)
sys.modules.setdefault("bpy.types", bpy_types)


def load_modules():
    package = types.ModuleType("io_scene_ueformat")
    package.__path__ = [str(PACKAGE_ROOT)]
    sys.modules.setdefault("io_scene_ueformat", package)
    protocol = importlib.import_module("io_scene_ueformat.importer.protocol")
    identity = importlib.import_module("io_scene_ueformat.importer.identity")
    resources = importlib.import_module("io_scene_ueformat.importer.resources")
    materials = importlib.import_module("io_scene_ueformat.importer.materials")
    topology = importlib.import_module("io_scene_ueformat.importer.topology")
    classes = importlib.import_module("io_scene_ueformat.importer.classes")
    reader = importlib.import_module("io_scene_ueformat.importer.reader")
    fmodel = importlib.import_module("io_scene_ueformat.importer.fmodel")
    return protocol, identity, resources, materials, topology, classes, reader, fmodel


protocol, identity, resources, materials, topology, classes, reader, fmodel = load_modules()


class TextureResourceVariantTests(unittest.TestCase):
    def test_explicit_factories_create_disjoint_variants(self):
        parameter = resources.TextureResource.parameter(
            1,
            "/Game/Textures/T_Body.T_Body",
            location_mode=1,
            uri="../Texture/T_Body.png",
            color_space=1,
            packing=0,
            uv_set=2,
        )
        embedded = resources.TextureResource.embedded(
            2,
            "/Game/Textures/T_Embedded.T_Embedded",
            mime="image/png",
            data=b"png",
        )
        identity_resource = resources.TextureResource.identity(
            0,
            "",
            location_mode=4,
            uri="Game/Textures/T_Identity.png",
            color_space=1,
            packing=0,
            uv_set=0,
            stable_id="tex.identity",
            expected_byte_length=3,
            expected_content_hash=b"\x11" * 32,
            identity_embedded=False,
        )

        self.assertIs(parameter.kind, resources.TextureResourceKind.PARAMETER)
        self.assertIs(embedded.kind, resources.TextureResourceKind.LEGACY_EMBEDDED)
        self.assertIs(identity_resource.kind, resources.TextureResourceKind.IDENTITY)
        self.assertEqual(parameter.mime, "")
        self.assertEqual(embedded.image_bytes, b"png")
        self.assertEqual(identity_resource.stable_id, "tex.identity")
        self.assertIs(classes.TextureResourceKind, resources.TextureResourceKind)

    def test_flattened_constructor_preserves_historical_parameter_positionals(self):
        resource = resources.TextureResource(
            1,
            "/Game/Textures/T_Body.T_Body",
            1,
            "../Texture/T_Body.png",
            uv_set=2,
        )

        self.assertIs(resource.kind, resources.TextureResourceKind.PARAMETER)
        self.assertEqual(resource.location_mode, 1)
        self.assertEqual(resource.uri, "../Texture/T_Body.png")
        self.assertEqual(resource.uv_set, 2)
        self.assertEqual(resource.data, b"")

    def test_mixed_or_malformed_variant_states_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "cannot contain parameter or identity"):
            resources.TextureResource(
                id=1,
                unreal_object_path="",
                mime="image/png",
                data=b"png",
                location_mode=1,
                uri="texture.png",
            )
        with self.assertRaisesRegex(ValueError, "require stable_id"):
            resources.TextureResource(
                id=0,
                unreal_object_path="",
                expected_byte_length=3,
                expected_content_hash=b"\x11" * 32,
            )
        with self.assertRaisesRegex(ValueError, "32 bytes"):
            resources.TextureResource(
                id=0,
                unreal_object_path="",
                stable_id="tex.invalid",
                expected_content_hash=b"short",
            )

    def test_variant_specific_mutation_keeps_the_discriminator_stable(self):
        parameter = resources.TextureResource(
            id=1,
            unreal_object_path="",
            location_mode=1,
            uri="texture.png",
        )
        parameter.packing = 7
        self.assertEqual(parameter.packing, 7)
        self.assertIs(parameter.kind, resources.TextureResourceKind.PARAMETER)

        embedded = resources.TextureResource(
            id=2,
            unreal_object_path="",
            mime="image/png",
            data=b"png",
        )
        with self.assertRaisesRegex(AttributeError, "material parameter fields"):
            embedded.packing = 7
        self.assertIs(embedded.kind, resources.TextureResourceKind.LEGACY_EMBEDDED)


class ImporterModuleCompatibilityTests(unittest.TestCase):
    def test_classes_reexports_protocol_public_contract(self):
        names = (
            "MAGIC",
            "MODEL_IDENTIFIER",
            "ANIM_IDENTIFIER",
            "POSE_IDENTIFIER",
            "EUEFormatVersion",
            "MAX_FMODEL_IDENTITY_PATH_BYTES",
            "MAX_FMODEL_TEXTURE_ID_BYTES",
            "MAX_FMODEL_MATERIAL_TEXTURE_BINDINGS",
        )
        for name in names:
            with self.subTest(name=name):
                self.assertIs(getattr(classes, name), getattr(protocol, name))

    def test_classes_reexports_protocol_private_reader_contract(self):
        names = (
            "_read_non_negative_count",
            "_read_strict_identity_fstring",
            "_read_strict_legacy_fstring",
            "_read_strict_utf8_string",
            "_validate_fmodel_identity_chunk_count",
            "_validate_fmodel_material_id",
            "_validate_logical_resource_uri",
            "_normalize_logical_unreal_path",
            "_validate_logical_unreal_path",
            "_validate_max_count",
            "_validate_top_level_count",
            "_FModelTextureLegacyFormatMismatchError",
        )
        for name in names:
            with self.subTest(name=name):
                self.assertIs(getattr(classes, name), getattr(protocol, name))

    def test_classes_reexports_identity_contract(self):
        names = (
            "FModelSkeletonIdentity",
            "FModelTargetSkeletonIdentity",
            "FModelTargetPoseIdentity",
            "compute_skeleton_layout_hash",
        )
        for name in names:
            with self.subTest(name=name):
                self.assertIs(getattr(classes, name), getattr(identity, name))

    def test_identity_module_has_no_blender_runtime_dependency(self):
        self.assertNotIn("bpy", identity.__dict__)

    def test_classes_reexports_material_contract(self):
        names = (
            "FModelTextureMapKind",
            "FModelMaterialTextureRecord",
            "FModelMaterialTextureBinding",
            "FModelMaterialTextures",
            "FModelMaterialLinks",
            "FModelPBRInterface",
            "FModelMaterialSlot",
            "FModelMaterialParameter",
        )
        for name in names:
            with self.subTest(name=name):
                self.assertIs(getattr(classes, name), getattr(materials, name))

    def test_classes_reexports_resource_contract(self):
        self.assertIs(classes.TextureResource, resources.TextureResource)

    def test_material_resource_modules_have_no_blender_runtime_dependency(self):
        self.assertNotIn("bpy", materials.__dict__)
        self.assertNotIn("bpy", resources.__dict__)

    def test_classes_reexports_topology_contract(self):
        names = (
            "UEMODELLodTopologySummary",
            "UEMODELSkeletonTopologySummary",
            "UEMODELTopologySummary",
            "_validate_uemodel_lods",
            "_validate_uemodel_skeleton",
            "validate_uemodel_topology",
        )
        for name in names:
            with self.subTest(name=name):
                self.assertIs(getattr(classes, name), getattr(topology, name))

    def test_topology_module_has_no_blender_runtime_dependency(self):
        self.assertNotIn("bpy", topology.__dict__)

    def test_existing_consumers_observe_the_same_protocol_types(self):
        self.assertIs(reader.EUEFormatVersion, protocol.EUEFormatVersion)
        self.assertIs(fmodel._validate_logical_unreal_path, protocol._validate_logical_unreal_path)

    def test_read_uint8_is_integer_api_and_read_byte_remains_compatible(self):
        archive = reader.FArchiveReader(b"\x7f\x80")
        self.assertEqual(archive.read_uint8(), 0x7F)
        self.assertEqual(archive.read_byte(), b"\x80")

    def test_integer_byte_consumers_use_read_uint8(self):
        importer_root = PACKAGE_ROOT / "importer"
        for path in importer_root.glob("*.py"):
            with self.subTest(path=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertNotIn("int.from_bytes(ar.read_byte()", source)

    def test_existing_consumers_observe_extracted_contract_types(self):
        self.assertIs(fmodel.FModelMaterialLinks, materials.FModelMaterialLinks)
        self.assertIs(fmodel.FModelMaterialTextures, materials.FModelMaterialTextures)
        self.assertIs(fmodel.FModelSkeletonIdentity, identity.FModelSkeletonIdentity)
        self.assertIs(fmodel.TextureResource, resources.TextureResource)

    def test_consumer_source_boundaries_do_not_regress_to_classes_facade(self):
        importer_root = PACKAGE_ROOT / "importer"
        fmodel_source = (importer_root / "fmodel.py").read_text(encoding="utf-8")
        builder_source = (importer_root / "material_builder.py").read_text(encoding="utf-8")
        images_source = (importer_root / "material_images.py").read_text(encoding="utf-8")
        reader_source = (importer_root / "reader.py").read_text(encoding="utf-8")
        logic_source = (importer_root / "logic.py").read_text(encoding="utf-8")
        self.assertIn("from .materials import", fmodel_source)
        self.assertIn("from .identity import", fmodel_source)
        self.assertIn("from .resources import TextureResource", fmodel_source)
        normalized_builder = "\n".join(line.strip() for line in builder_source.splitlines())
        normalized_images = "\n".join(line.strip() for line in images_source.splitlines())
        self.assertIn("from .materials import FModelMaterialLinks", normalized_builder)
        self.assertIn("from .materials import FModelMaterialLinks, FModelMaterialParameter", normalized_builder)
        self.assertIn("from .materials import FModelMaterialParameter", normalized_images)
        self.assertIn("from .resources import TextureResource", images_source)
        self.assertIn("from .protocol import EUEFormatVersion", reader_source)
        self.assertIn("from .protocol import ANIM_IDENTIFIER", logic_source)
        self.assertNotIn("from .classes import EUEFormatVersion", reader_source)
        self.assertNotIn("from ..importer.classes import EUEFormatVersion", logic_source)

    def test_protocol_module_has_no_blender_runtime_dependency(self):
        self.assertNotIn("bpy", protocol.__dict__)
        self.assertEqual(protocol.MAGIC, "UEFORMAT")


if __name__ == "__main__":
    unittest.main()
