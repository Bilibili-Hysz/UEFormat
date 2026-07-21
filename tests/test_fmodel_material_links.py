from __future__ import annotations

import importlib
import json
from pathlib import Path
import struct
import sys
import types
import unittest
from unittest import mock


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


def _load_importer_modules():
    package = types.ModuleType("io_scene_ueformat")
    package.__path__ = [str(PACKAGE_ROOT)]
    sys.modules.setdefault("io_scene_ueformat", package)
    return (
        importlib.import_module("io_scene_ueformat.importer.classes"),
        importlib.import_module("io_scene_ueformat.importer.reader"),
        importlib.import_module("io_scene_ueformat.importer.fmodel"),
    )


classes, reader, fmodel = _load_importer_modules()
material_builder = importlib.import_module("io_scene_ueformat.importer.material_builder")
material_images = importlib.import_module("io_scene_ueformat.importer.material_images")


def fstring(value: str) -> bytes:
    encoded = value.encode("utf-8") + b"\0"
    return struct.pack("<i", len(encoded)) + encoded


def chunk(name: str, count: int, payload: bytes, *, byte_size: int | None = None) -> bytes:
    declared_size = len(payload) if byte_size is None else byte_size
    return fstring(name) + struct.pack("<ii", count, declared_size) + payload


def material_links_payload(
    *,
    extension_version: int = 1,
    slot_count: int = 1,
    parameter_count: int = 3,
    parameter_kind: int = 1,
) -> bytes:
    payload = bytearray(struct.pack("<Bi", extension_version, slot_count))
    if slot_count <= 0:
        return bytes(payload)

    payload.extend(struct.pack("<i", 4))
    payload.extend(fstring("Body"))
    payload.extend(fstring("/Game/Materials/M_Body.M_Body"))
    payload.extend(fstring("materials/body.json"))
    payload.extend(struct.pack("<i", parameter_count))
    if parameter_count <= 0:
        return bytes(payload)

    payload.extend(fstring("BaseColor"))
    payload.extend(struct.pack("<B", parameter_kind))
    payload.extend(fstring("base_color"))
    if parameter_kind == 1:
        payload.extend(struct.pack("<B", 3))
        payload.extend(fstring("textures/body_bc.png"))
        payload.extend(struct.pack("<i", 7))
        payload.extend(fstring("/Game/Textures/T_Body_BC.T_Body_BC"))
        payload.extend(struct.pack("<BBi", 1, 0, 0))

    payload.extend(fstring("Roughness"))
    payload.extend(struct.pack("<B", 2))
    payload.extend(fstring("roughness"))
    payload.extend(struct.pack("<f", 0.25))

    payload.extend(fstring("Tint"))
    payload.extend(struct.pack("<B", 3))
    payload.extend(fstring("tint"))
    payload.extend(struct.pack("<ffff", 0.1, 0.2, 0.3, 1.0))
    return bytes(payload)


def textures_payload(*, version: int = 1, count: int = 1) -> bytes:
    payload = bytearray(struct.pack("<Bi", version, count))
    if count <= 0:
        return bytes(payload)
    texture_bytes = b"PNG"
    payload.extend(struct.pack("<i", 7))
    payload.extend(fstring("/Game/Textures/T_Body_BC.T_Body_BC"))
    payload.extend(fstring("image/png"))
    payload.extend(struct.pack("<i", len(texture_bytes)))
    payload.extend(texture_bytes)
    return bytes(payload)


def pbr_interfaces_payload(
    records=((4, 0, 1), (4, 0, 10), (4, 1, 9)),
    *,
    version: int = 2,
    count: int | None = None,
) -> bytes:
    records = tuple(records)
    payload = bytearray(struct.pack("<Bi", version, len(records) if count is None else count))
    for slot_index, parameter_index, role in records:
        payload.extend(struct.pack("<iiB", slot_index, parameter_index, role))
    return bytes(payload)


class FModelMaterialLinksReaderTests(unittest.TestCase):
    def read_model(self, *sections: bytes):
        return classes.UEModel.from_archive(reader.FArchiveReader(b"".join(sections)))

    def test_reads_material_links_and_texture_resources(self):
        model = self.read_model(
            chunk("FMODEL_MATERIALS", 1, material_links_payload()),
            chunk("FMODEL_TEXTURES", 1, textures_payload()),
        )

        self.assertEqual(len(model.material_links.slots), 1)
        slot = model.material_links.slots[0]
        self.assertEqual(slot.slot_index, 4)
        self.assertEqual(slot.slot_name, "Body")
        self.assertEqual(slot.unreal_material_path, "/Game/Materials/M_Body.M_Body")
        self.assertEqual(slot.material_json_uri, "materials/body.json")

        texture, scalar, vector = slot.parameters
        self.assertEqual(texture.name, "BaseColor")
        self.assertEqual(texture.kind, 1)
        self.assertEqual(texture.semantic, "base_color")
        self.assertEqual(texture.location_mode, 3)
        self.assertEqual(texture.uri, "textures/body_bc.png")
        self.assertEqual(texture.texture_id, 7)
        self.assertEqual(texture.unreal_object_path, "/Game/Textures/T_Body_BC.T_Body_BC")
        self.assertEqual(texture.color_space, 1)
        self.assertEqual(texture.packing, 0)
        self.assertEqual(texture.uv_set, 0)
        self.assertEqual(scalar.value, 0.25)
        self.assertEqual(len(vector.value), 4)
        for actual, expected in zip(vector.value, (0.1, 0.2, 0.3, 1.0), strict=True):
            self.assertAlmostEqual(actual, expected)

        self.assertEqual(len(model.textures), 1)
        resource = model.textures[0]
        self.assertEqual(resource.id, 7)
        self.assertEqual(resource.unreal_object_path, "/Game/Textures/T_Body_BC.T_Body_BC")
        self.assertEqual(resource.mime, "image/png")
        self.assertEqual(resource.data, b"PNG")

    def test_keeps_unknown_top_level_chunks_skippable(self):
        model = self.read_model(
            chunk("FUTURE_EXTENSION", 99, b"skip me"),
            chunk("FMODEL_MATERIALS", 0, material_links_payload(slot_count=0)),
        )

        self.assertEqual(model.material_links.slots, [])
        self.assertEqual(model.textures, [])

    def test_rejects_malformed_top_level_chunk_sizes_before_consuming_payload(self):
        for name, size, message in (
            ("FUTURE_EXTENSION", -1, "negative byte_size"),
            ("FUTURE_EXTENSION", 8, "exceeds available data"),
            ("LODS", -1, "negative byte_size"),
            ("LODS", 8, "exceeds available data"),
        ):
            with self.subTest(name=name, size=size), self.assertRaisesRegex(ValueError, message):
                self.read_model(chunk(name, 0, b"", byte_size=size))

    def test_rejects_top_level_count_that_cannot_fit_minimum_records(self):
        with self.assertRaisesRegex(ValueError, "array_size.*cannot fit"):
            self.read_model(chunk("LODS", 1, b""))

    def test_rejects_negative_or_truncated_fstrings(self):
        for raw, message in (
            (struct.pack("<i", -1), "negative FString length"),
            (struct.pack("<i", 4) + b"x", "FString length exceeds available data"),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.read_model(raw)

    def test_reads_strict_v2_pbr_interfaces_and_retains_v1_when_absent(self):
        v1_model = self.read_model(chunk("FMODEL_MATERIALS", 1, material_links_payload()))
        self.assertEqual(v1_model.material_links.pbr_interfaces, [])
        self.assertFalse(v1_model.material_links.pbr_interfaces_present)

        model = self.read_model(
            chunk("FMODEL_MATERIALS", 1, material_links_payload()),
            chunk("FUTURE_EXTENSION", 1, b"ignored"),
            chunk("FMODEL_PBR_INTERFACES", 3, pbr_interfaces_payload()),
        )

        self.assertEqual(
            [(record.slot_index, record.parameter_index, record.role) for record in model.material_links.pbr_interfaces],
            [(4, 0, 1), (4, 0, 10), (4, 1, 9)],
        )
        self.assertTrue(model.material_links.pbr_interfaces_present)

    def test_preserves_an_explicit_empty_v2_pbr_chunk(self):
        model = self.read_model(
            chunk("FMODEL_MATERIALS", 1, material_links_payload()),
            chunk("FMODEL_PBR_INTERFACES", 0, pbr_interfaces_payload(records=())),
        )

        self.assertEqual(model.material_links.pbr_interfaces, [])
        self.assertTrue(model.material_links.pbr_interfaces_present)

    def test_rejects_top_level_extension_counts_that_disagree_with_payload_counts(self):
        payloads = (
            ("FMODEL_MATERIALS", lambda count: material_links_payload(slot_count=count), "slot"),
            ("FMODEL_TEXTURES", textures_payload, "texture"),
            ("FMODEL_PBR_INTERFACES", pbr_interfaces_payload, "PBR interface"),
        )
        for name, make_payload, count_name in payloads:
            for outer_count, inner_count in ((0, 1), (2, 1)):
                with self.subTest(name=name, outer_count=outer_count, inner_count=inner_count):
                    with self.assertRaisesRegex(ValueError, f"{count_name} count.*top-level"):
                        self.read_model(chunk(name, outer_count, make_payload(count=inner_count)))

    def test_v2_slot_index_uses_serialized_slot_ordinal(self):
        model = self.read_model(
            chunk("FMODEL_MATERIALS", 1, material_links_payload()),
            chunk("FMODEL_PBR_INTERFACES", 1, pbr_interfaces_payload(records=((4, 0, 1),))),
        )

        self.assertEqual(model.material_links.pbr_interfaces[0].slot_index, 4)

        with self.assertRaisesRegex(ValueError, "Invalid FModel PBR interface slot_index: 0"):
            self.read_model(
                chunk("FMODEL_MATERIALS", 1, material_links_payload()),
                chunk("FMODEL_PBR_INTERFACES", 1, pbr_interfaces_payload(records=((0, 0, 1),))),
            )

    def test_rejects_duplicate_v2_slot_ordinals_and_singleton_roles(self):
        duplicate_slots = bytearray(material_links_payload())
        duplicate_slots[1:5] = struct.pack("<i", 2)
        duplicate_slots.extend(material_links_payload()[5:])
        materials = chunk("FMODEL_MATERIALS", 2, duplicate_slots)
        interfaces = chunk("FMODEL_PBR_INTERFACES", 1, pbr_interfaces_payload(records=((4, 0, 1),)))
        with self.assertRaisesRegex(ValueError, "duplicate slot_index: 4"):
            self.read_model(materials, interfaces)

        materials = chunk("FMODEL_MATERIALS", 1, material_links_payload())
        for records in (
            ((4, 0, 1), (4, 0, 1)),
            ((4, 0, 1), (4, 1, 1)),
        ):
            with self.subTest(records=records), self.assertRaisesRegex(ValueError, "duplicate role"):
                self.read_model(
                    materials,
                    chunk("FMODEL_PBR_INTERFACES", len(records), pbr_interfaces_payload(records=records)),
                )

        self.read_model(
            materials,
            chunk("FMODEL_PBR_INTERFACES", 2, pbr_interfaces_payload(records=((4, 0, 1), (4, 0, 10)))),
        )

    def test_rejects_invalid_or_duplicate_slot_ordinals_without_pbr_interfaces(self):
        duplicate_slots = bytearray(material_links_payload())
        duplicate_slots[1:5] = struct.pack("<i", 2)
        duplicate_slots.extend(material_links_payload()[5:])
        with self.assertRaisesRegex(ValueError, "duplicate slot_index: 4"):
            self.read_model(chunk("FMODEL_MATERIALS", 2, duplicate_slots))

        negative_slot = bytearray(material_links_payload())
        negative_slot[5:9] = struct.pack("<i", -1)
        with self.assertRaisesRegex(ValueError, "material slot_index: -1"):
            self.read_model(chunk("FMODEL_MATERIALS", 1, negative_slot))

    def test_rejects_malformed_v2_pbr_interfaces(self):
        materials = chunk("FMODEL_MATERIALS", 1, material_links_payload())
        invalid_payloads = (
            (pbr_interfaces_payload(version=1), "interface extension version"),
            (pbr_interfaces_payload(count=-1), "interface count"),
            (pbr_interfaces_payload(((-1, 0, 1),)), "slot_index"),
            (pbr_interfaces_payload(((0, 0, 1),)), "slot_index"),
            (pbr_interfaces_payload(((4, -1, 1),)), "parameter_index"),
            (pbr_interfaces_payload(((4, 3, 1),)), "parameter_index"),
            (pbr_interfaces_payload(((4, 0, 0),)), "PBR role"),
            (pbr_interfaces_payload(((4, 0, 13),)), "PBR role"),
            (pbr_interfaces_payload(records=((4, 0, 1),)) + b"junk", "Trailing bytes"),
        )
        for payload, message in invalid_payloads:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.read_model(materials, chunk("FMODEL_PBR_INTERFACES", 1, payload))

    def test_rejects_invalid_v2_interface_chunk_size(self):
        materials = chunk("FMODEL_MATERIALS", 1, material_links_payload())
        payload = pbr_interfaces_payload(records=())
        with self.assertRaisesRegex(ValueError, "interfaces chunk declared negative byte_size"):
            self.read_model(materials, chunk("FMODEL_PBR_INTERFACES", 0, payload, byte_size=-1))
        with self.assertRaisesRegex(ValueError, "interfaces chunk declared byte_size exceeds available data"):
            self.read_model(materials, chunk("FMODEL_PBR_INTERFACES", 0, payload, byte_size=len(payload) + 1))

    def test_rejects_duplicate_recognized_singleton_chunks(self):
        for name, payload in (
            ("FMODEL_MATERIALS", material_links_payload(slot_count=0)),
            ("FMODEL_TEXTURES", textures_payload(count=0)),
            ("FMODEL_PBR_INTERFACES", pbr_interfaces_payload(records=())),
        ):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "duplicate top-level chunk"):
                self.read_model(chunk(name, 0, payload), chunk(name, 0, payload))

    def test_rejects_duplicate_embedded_texture_ids_and_count_contradictions(self):
        first = textures_payload()
        second = textures_payload()
        duplicate_ids = first[:1] + struct.pack("<i", 2) + first[5:] + second[5:]
        with self.assertRaisesRegex(ValueError, "duplicate embedded texture id: 7"):
            self.read_model(chunk("FMODEL_TEXTURES", 2, duplicate_ids))

        with self.assertRaisesRegex(ValueError, "texture count cannot fit"):
            self.read_model(chunk("FMODEL_TEXTURES", 1, textures_payload(count=1)[:5]))

    def test_rejects_unsupported_material_extension_version(self):
        with self.assertRaisesRegex(ValueError, "extension version"):
            self.read_model(chunk("FMODEL_MATERIALS", 1, material_links_payload(extension_version=2)))

        with self.assertRaisesRegex(ValueError, "extension version"):
            self.read_model(chunk("FMODEL_TEXTURES", 1, textures_payload(version=2)))

    def test_rejects_negative_extension_counts(self):
        with self.assertRaisesRegex(ValueError, "slot count"):
            self.read_model(chunk("FMODEL_MATERIALS", 1, material_links_payload(slot_count=-1)))

        with self.assertRaisesRegex(ValueError, "parameter count"):
            self.read_model(chunk("FMODEL_MATERIALS", 1, material_links_payload(parameter_count=-1)))

        with self.assertRaisesRegex(ValueError, "texture count"):
            self.read_model(chunk("FMODEL_TEXTURES", 1, textures_payload(count=-1)))

    def test_rejects_extension_counts_above_documented_maxima_before_record_reads(self):
        limit_cases = (
            (
                "FMODEL_MATERIALS",
                classes.MAX_FMODEL_SLOTS,
                "slot count",
                lambda count: struct.pack("<Bi", 1, count) + (b"\0" * (count * 20)),
            ),
            (
                "FMODEL_TEXTURES",
                classes.MAX_FMODEL_TEXTURES,
                "texture count",
                lambda count: struct.pack("<Bi", 1, count) + (b"\0" * (count * 16)),
            ),
            (
                "FMODEL_PBR_INTERFACES",
                classes.MAX_FMODEL_PBR_INTERFACES,
                "PBR interface count",
                lambda count: struct.pack("<Bi", 2, count) + (b"\0" * (count * 9)),
            ),
        )
        for name, limit, count_name, make_payload in limit_cases:
            count = limit + 1
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, f"{count_name} exceeds maximum"):
                self.read_model(chunk(name, count, make_payload(count)))

        parameter_count = classes.MAX_FMODEL_PARAMETERS + 1
        parameter_payload = (
            struct.pack("<Bi", 1, 1)
            + struct.pack("<i", 0)
            + fstring("")
            + fstring("")
            + fstring("")
            + struct.pack("<i", parameter_count)
            + (b"\0" * (parameter_count * 13))
        )
        with self.assertRaisesRegex(ValueError, "parameter count exceeds maximum"):
            self.read_model(chunk("FMODEL_MATERIALS", 1, parameter_payload))

    def test_rejects_parameter_counts_that_cannot_fit_minimum_records(self):
        payload = (
            struct.pack("<Bi", 1, 1)
            + struct.pack("<i", 0)
            + fstring("")
            + fstring("")
            + fstring("")
            + struct.pack("<i", 1)
        )
        with self.assertRaisesRegex(ValueError, "parameter count cannot fit"):
            self.read_model(chunk("FMODEL_MATERIALS", 1, payload))

    def test_rejects_unknown_parameter_kind(self):
        with self.assertRaisesRegex(ValueError, "parameter kind"):
            self.read_model(chunk("FMODEL_MATERIALS", 1, material_links_payload(parameter_kind=99)))

    def test_rejects_truncated_texture_data(self):
        payload = bytearray(textures_payload())
        payload[-7:-3] = struct.pack("<i", 4)

        with self.assertRaisesRegex(ValueError, "Truncated FModel texture data"):
            self.read_model(chunk("FMODEL_TEXTURES", 1, bytes(payload)))

    def test_rejects_materials_chunk_declared_size_beyond_available_data(self):
        payload = material_links_payload(slot_count=0)

        with self.assertRaisesRegex(ValueError, "materials chunk declared byte_size exceeds available data"):
            self.read_model(chunk("FMODEL_MATERIALS", 1, payload, byte_size=len(payload) + 1))

    def test_rejects_textures_chunk_declared_size_beyond_available_data(self):
        payload = textures_payload(count=0)

        with self.assertRaisesRegex(ValueError, "textures chunk declared byte_size exceeds available data"):
            self.read_model(chunk("FMODEL_TEXTURES", 1, payload, byte_size=len(payload) + 1))

    def test_rejects_negative_materials_chunk_size_before_chunking(self):
        archive = reader.FArchiveReader(
            chunk("FMODEL_MATERIALS", 1, material_links_payload(slot_count=0), byte_size=-1)
        )
        archive.chunk = mock.Mock(side_effect=AssertionError("chunk() must not be called"))

        with self.assertRaisesRegex(ValueError, "materials chunk declared negative byte_size"):
            classes.UEModel.from_archive(archive)
        archive.chunk.assert_not_called()

    def test_rejects_negative_textures_chunk_size_before_chunking(self):
        archive = reader.FArchiveReader(
            chunk("FMODEL_TEXTURES", 1, textures_payload(count=0), byte_size=-1)
        )
        archive.chunk = mock.Mock(side_effect=AssertionError("chunk() must not be called"))

        with self.assertRaisesRegex(ValueError, "textures chunk declared negative byte_size"):
            classes.UEModel.from_archive(archive)
        archive.chunk.assert_not_called()

    def test_rejects_trailing_garbage_in_recognized_chunks(self):
        for name, payload in (
            ("FMODEL_MATERIALS", material_links_payload() + b"junk"),
            ("FMODEL_TEXTURES", textures_payload() + b"junk"),
        ):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Trailing bytes"):
                self.read_model(chunk(name, 1, payload))


class FModelResourceResolutionTests(unittest.TestCase):
    def setUp(self):
        self.model_path = Path("C:/exports/characters/hero/model.uemodel")
        self.ue_export_root = Path("C:/exports")
        self.bundle_root = Path("C:/bundles/hero")

    def resolve(self, texture, **kwargs):
        return fmodel.FModelResourceResolver(
            model_path=self.model_path,
            fmodel_textures=kwargs.pop("fmodel_textures", {}),
            ue_export_root=kwargs.pop("ue_export_root", self.ue_export_root),
            bundle_root=kwargs.pop("bundle_root", self.bundle_root),
        ).resolve_texture(texture)

    def test_relative_uri_resolves_from_model_parent_and_stays_export_root(self):
        texture = classes.TextureResource(id=1, unreal_object_path="", location_mode=1, uri="textures/body.png")

        self.assertEqual(
            self.resolve(texture),
            Path("C:/exports/characters/hero/textures/body.png"),
        )

    def test_relative_uri_resolves_without_export_root(self):
        texture = classes.TextureResource(id=1, unreal_object_path="", location_mode=1, uri="textures/body.png")

        expected = (self.model_path.parent / texture.uri).resolve(strict=False)
        self.assertEqual(self.resolve(texture, ue_export_root=None), expected)
        self.assertEqual(self.resolve(texture, ue_export_root=""), expected)

    def test_file_uri_modes_reject_noncanonical_relative_paths(self):
        unsafe_uris = (
            "",
            "../body.png",
            "./body.png",
            "textures//body.png",
            "textures/body.png/",
            "textures\\body.png",
            "/body.png",
            "//host/body.png",
            "C:/body.png",
            "a\0b",
        )
        for mode in (1, 2):
            for unsafe_uri in unsafe_uris:
                with self.subTest(mode=mode, uri=unsafe_uri), self.assertRaisesRegex(ValueError, "URI"):
                    self.resolve(classes.TextureResource(id=1, unreal_object_path="", location_mode=mode, uri=unsafe_uri))

    def test_bundle_uri_resolves_only_safe_posix_relative_paths(self):
        texture = classes.TextureResource(id=1, unreal_object_path="", location_mode=2, uri="textures/body.png")

        self.assertEqual(self.resolve(texture), Path("C:/bundles/hero/textures/body.png"))
        for unsafe_uri in ("../body.png", "./body.png", "textures//body.png", "textures\\body.png", "/body.png", "//host/body.png", "C:/body.png", "a\0b"):
            with self.subTest(uri=unsafe_uri), self.assertRaisesRegex(ValueError, "bundle URI"):
                self.resolve(classes.TextureResource(id=1, unreal_object_path="", location_mode=2, uri=unsafe_uri))

    def test_bundle_uri_requires_bundle_root(self):
        texture = classes.TextureResource(id=1, unreal_object_path="", location_mode=2, uri="textures/body.png")

        with self.assertRaisesRegex(ValueError, "bundle_root"):
            self.resolve(texture, bundle_root=None)

    def test_embedded_reference_returns_image_bytes_and_requires_known_id(self):
        texture = classes.TextureResource(id=7, unreal_object_path="", location_mode=3)
        embedded = classes.TextureResource(id=7, unreal_object_path="", mime="image/png", data=b"PNG")

        self.assertEqual(self.resolve(texture, fmodel_textures={7: embedded}), b"PNG")
        with self.assertRaisesRegex(ValueError, "texture id 7"):
            self.resolve(texture)

    def test_embedded_uri_is_empty_or_canonical_relative_metadata(self):
        embedded = classes.TextureResource(id=7, unreal_object_path="", mime="image/png", data=b"PNG")
        self.assertEqual(
            self.resolve(classes.TextureResource(id=7, unreal_object_path="", location_mode=3, uri="textures/body.png"), fmodel_textures={7: embedded}),
            b"PNG",
        )
        for unsafe_uri in ("../body.png", "textures//body.png", "textures\\body.png", "/body.png", "C:/body.png"):
            with self.subTest(uri=unsafe_uri), self.assertRaisesRegex(ValueError, "embedded URI"):
                self.resolve(
                    classes.TextureResource(id=7, unreal_object_path="", location_mode=3, uri=unsafe_uri),
                    fmodel_textures={7: embedded},
                )

    def test_root_confinement_follows_existing_symlinks(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            export_root = root / "export"
            model_path = export_root / "models" / "model.uemodel"
            outside = root / "outside"
            model_path.parent.mkdir(parents=True)
            outside.mkdir()
            try:
                (model_path.parent / "textures").symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"symlink creation is unavailable: {exc}")
            resolver = fmodel.FModelResourceResolver(
                model_path=model_path,
                fmodel_textures={},
                ue_export_root=export_root,
                bundle_root=None,
            )
            texture = classes.TextureResource(id=1, unreal_object_path="", location_mode=1, uri="textures/body.png")
            with self.assertRaisesRegex(ValueError, "escapes ue_export_root"):
                resolver.resolve_texture(texture)

    def test_rejects_unknown_location_mode(self):
        texture = classes.TextureResource(id=1, unreal_object_path="", location_mode=99)

        with self.assertRaisesRegex(ValueError, "location mode"):
            self.resolve(texture)


class FModelMetadataTests(unittest.TestCase):
    def test_serializes_full_material_link_records_without_image_loading(self):
        links = classes.FModelMaterialLinks(slots=[classes.FModelMaterialSlot(
            slot_index=4,
            slot_name="Body",
            unreal_material_path="/Game/Materials/M_Body.M_Body",
            material_json_uri="materials/body.json",
            parameters=[
                classes.FModelMaterialParameter(
                    name="BaseColor",
                    kind=1,
                    semantic="base_color",
                    value=classes.TextureResource(
                        id=7,
                        unreal_object_path="/Game/Textures/T_Body_BC.T_Body_BC",
                        location_mode=3,
                        uri="textures/body.png",
                        color_space=1,
                        packing=0,
                        uv_set=0,
                    ),
                ),
                classes.FModelMaterialParameter(name="Roughness", kind=2, semantic="roughness", value=0.25),
                classes.FModelMaterialParameter(name="Tint", kind=3, semantic="tint", value=(0.1, 0.2, 0.3, 1.0)),
            ],
        )])

        metadata = fmodel.material_links_metadata(links)

        self.assertEqual(metadata["version"], 1)
        self.assertEqual(metadata["slots"][0]["parameters"][0]["value"]["texture_id"], 7)
        self.assertEqual(metadata["slots"][0]["parameters"][1]["value"], 0.25)
        self.assertEqual(metadata["slots"][0]["parameters"][2]["value"], [0.1, 0.2, 0.3, 1.0])
        self.assertEqual(json.loads(json.dumps(metadata)), metadata)


class FModelImageIdentityTests(unittest.TestCase):
    def test_cache_identity_uses_canonical_unreal_identity_content_and_interpretation(self):
        texture = classes.TextureResource(
            id=1,
            unreal_object_path="  \\Game\\Textures\\T_Body.T_Body  ",
            location_mode=1,
            uri="textures/body.png",
        )
        alias = classes.TextureResource(
            id=99,
            unreal_object_path="/Game/Textures/T_Body.T_Body",
            location_mode=2,
            uri="another/path.png",
        )

        key = material_images._resource_cache_key(texture, b"same bytes", "COLOR")
        self.assertEqual(key, material_images._resource_cache_key(alias, b"same bytes", "COLOR"))
        self.assertNotEqual(key, material_images._resource_cache_key(texture, b"changed", "COLOR"))
        self.assertNotEqual(key, material_images._resource_cache_key(texture, b"same bytes", "DATA"))
        self.assertEqual(key[0], "/Game/Textures/T_Body.T_Body")

    def test_external_cache_identity_hashes_verified_file_contents_not_path_spelling(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            first = root / "first.png"
            second = root / "second.png"
            first.write_bytes(b"same bytes")
            second.write_bytes(b"same bytes")
            texture = classes.TextureResource(
                id=1,
                unreal_object_path="/Game/Textures/T_Body.T_Body",
                location_mode=1,
                uri="textures/body.png",
            )
            self.assertEqual(
                material_images._resource_cache_key(texture, first, "COLOR"),
                material_images._resource_cache_key(texture, second, "COLOR"),
            )

    def test_material_cache_identity_ignores_uri_aliases_but_tracks_content(self):
        def slot(uri, mode):
            return classes.FModelMaterialSlot(
                slot_index=0,
                slot_name="Body",
                unreal_material_path="/Game/Materials/M_Body.M_Body",
                material_json_uri="",
                parameters=[classes.FModelMaterialParameter(
                    name="BaseColor",
                    kind=1,
                    semantic="base_color",
                    value=classes.TextureResource(
                        id=1,
                        unreal_object_path="/Game/Textures/T_Body.T_Body",
                        location_mode=mode,
                        uri=uri,
                    ),
                )],
            )

        class Images:
            contents = b"same"

            def resource_cache_key(self, parameter, interpretation):
                return material_images._resource_cache_key(parameter.texture, self.contents, interpretation)

        images = Images()
        first = material_builder._material_cache_identity(slot("textures/body.png", 1), [], images)
        second = material_builder._material_cache_identity(slot("alias/body.png", 2), [], images)
        self.assertEqual(first, second)
        images.contents = b"changed"
        self.assertNotEqual(first, material_builder._material_cache_identity(slot("textures/body.png", 1), [], images))


class FModelMaterialBuilderPolicyTests(unittest.TestCase):
    def test_v2_role_kind_compatibility_matches_builder_source_paths(self):
        for role in (1, 4):
            self.assertTrue(material_builder._v2_role_accepts_parameter(role, 1))
            self.assertFalse(material_builder._v2_role_accepts_parameter(role, 2))
            self.assertTrue(material_builder._v2_role_accepts_parameter(role, 3))
        for role in (3, 10):
            self.assertTrue(material_builder._v2_role_accepts_parameter(role, 1))
            self.assertFalse(material_builder._v2_role_accepts_parameter(role, 2))
            self.assertFalse(material_builder._v2_role_accepts_parameter(role, 3))
        for role in (2, 5, 6, 7, 8, 9, 11, 12):
            self.assertFalse(material_builder._v2_role_accepts_parameter(role, 1))
            self.assertTrue(material_builder._v2_role_accepts_parameter(role, 2))
            self.assertTrue(material_builder._v2_role_accepts_parameter(role, 3))


if __name__ == "__main__":
    unittest.main()
