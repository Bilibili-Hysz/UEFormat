from __future__ import annotations

import importlib
import gzip
import json
from pathlib import Path
import struct
import sys
import tempfile
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


def _load_logic_module():
    bpy_extras = types.ModuleType("bpy_extras")
    bpy_extras.anim_utils = types.ModuleType("bpy_extras.anim_utils")
    sys.modules["bpy_extras"] = bpy_extras
    sys.modules["bpy_extras.anim_utils"] = bpy_extras.anim_utils
    for name in (
        "Action", "ArmatureModifier", "ByteColorAttribute", "EditBone", "FCurve",
        "Object", "PoseBone", "Armature", "bpy_prop_collection",
    ):
        setattr(bpy_types, name, object)
    mathutils.Matrix = tuple
    sys.modules.pop("io_scene_ueformat.importer.logic", None)
    return importlib.import_module("io_scene_ueformat.importer.logic")


classes, reader, fmodel = _load_importer_modules()
material_builder = importlib.import_module("io_scene_ueformat.importer.material_builder")
material_images = importlib.import_module("io_scene_ueformat.importer.material_images")
logic = _load_logic_module()


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
    source_material_indices: tuple[int, ...] | None = None,
    texture_uri: str = "textures/body_bc.png",
    texture_location_mode: int = 3,
) -> bytes:
    payload = bytearray(struct.pack("<Bi", extension_version, slot_count))
    if slot_count <= 0:
        return bytes(payload)

    payload.extend(struct.pack("<i", 4))
    if extension_version == 2:
        if source_material_indices is None:
            source_material_indices = (0,)
        payload.extend(struct.pack("<i", source_material_indices[0]))
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
        payload.extend(struct.pack("<B", texture_location_mode))
        payload.extend(fstring(texture_uri))
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
        payload.extend(struct.pack("<i", 0))
        return bytes(payload)
    payload.extend(struct.pack("<i", 0))
    return bytes(payload)


def legacy_embedded_textures_payload(*, version: int = 1, count: int = 1, data: bytes = b"PNG") -> bytes:
    payload = bytearray(struct.pack("<Bi", version, count))
    if count <= 0:
        return bytes(payload)
    payload.extend(struct.pack("<i", 7))
    payload.extend(fstring("/Game/Textures/T_Body_BC.T_Body_BC"))
    payload.extend(fstring("image/png"))
    payload.extend(struct.pack("<i", len(data)))
    payload.extend(data)
    return bytes(payload)


def identity_textures_payload(
    *,
    version: int = 1,
    textures: tuple[tuple[str, str, int, int, bytes], ...] = (("tex.base", "textures/body_bc.png", 1, 3, b"\x11" * 32),),
    bindings: tuple[tuple[str, int, str, int], ...] = (("/Game/Materials/M_Body.M_Body", 0, "tex.base", 1),),
) -> bytes:
    payload = bytearray(struct.pack("<Bi", version, len(textures)))
    for texture_id, logical_resource_uri, is_embedded, byte_length, content_hash in textures:
        payload.extend(struct.pack("<i", len(texture_id)))
        payload.extend(texture_id.encode("utf-8"))
        payload.extend(struct.pack("<i", len(logical_resource_uri)))
        payload.extend(logical_resource_uri.encode("utf-8"))
        payload.extend(struct.pack("<B", is_embedded))
        payload.extend(struct.pack("<i", byte_length))
        payload.extend(content_hash)
    payload.extend(struct.pack("<i", len(bindings)))
    for material_path, map_kind, texture_id, is_srgb in bindings:
        payload.extend(struct.pack("<i", len(material_path)))
        payload.extend(material_path.encode("utf-8"))
        payload.extend(struct.pack("<B", map_kind))
        payload.extend(struct.pack("<i", len(texture_id)))
        payload.extend(texture_id.encode("utf-8"))
        payload.extend(struct.pack("<B", is_srgb))
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


def uemodel_payload(*, source_materials: tuple[tuple[int, str], ...]) -> bytes:
    lod_payload = fstring("LOD0") + struct.pack("<i", 0)
    materials = bytearray(struct.pack("<Bi", 2, len(source_materials)))
    for section_index, (source_index, material_path) in enumerate(source_materials):
        materials.extend(struct.pack("<ii", section_index, source_index))
        materials.extend(fstring(f"Section{section_index}"))
        materials.extend(fstring(material_path))
        materials.extend(fstring(""))
        materials.extend(struct.pack("<i", 0))
    return chunk("LODS", 1, lod_payload) + chunk("FMODEL_MATERIALS", len(source_materials), bytes(materials))


def uemodel_bytes(*, source_materials: tuple[tuple[int, str], ...]) -> bytes:
    header = b"".join((b"UEFORMAT", fstring("UEMODEL"), b"\x04", fstring(""), b"\x00"))
    return header + uemodel_payload(source_materials=source_materials)


def compressed_uemodel_bytes(
    *,
    source_materials: tuple[tuple[int, str], ...],
    compression_type: str = "GZIP",
    uncompressed_size: int | None = None,
    compressed_size: int | None = None,
    payload: bytes | None = None,
) -> bytes:
    uncompressed_payload = uemodel_payload(source_materials=source_materials)
    compressed_payload = gzip.compress(uncompressed_payload) if payload is None else payload
    header = b"".join((
        b"UEFORMAT", fstring("UEMODEL"), b"\x04", fstring(""), b"\x01",
        fstring(compression_type), struct.pack(
            "<ii",
            len(uncompressed_payload) if uncompressed_size is None else uncompressed_size,
            len(compressed_payload) if compressed_size is None else compressed_size,
        ),
    ))
    return header + compressed_payload


class FModelMaterialLinksReaderTests(unittest.TestCase):
    def read_model(self, *sections: bytes):
        return classes.UEModel.from_archive(reader.FArchiveReader(b"".join(sections)))

    def test_reads_material_links_and_texture_resources(self):
        model = self.read_model(
            chunk("FMODEL_MATERIALS", 1, material_links_payload()),
            chunk("FMODEL_TEXTURES", 1, legacy_embedded_textures_payload()),
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
        self.assertIs(texture.texture.kind, classes.TextureResourceKind.PARAMETER)
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
        self.assertIs(resource.kind, classes.TextureResourceKind.LEGACY_EMBEDDED)

    def test_reads_v2_source_material_indices_without_changing_section_indices(self):
        payload = bytearray(struct.pack("<Bi", 2, 2))
        for section_index, source_index, name, path in (
            (0, 1, "First", "/Game/Materials/M_Source1.M_Source1"),
            (1, 0, "Second", "/Game/Materials/M_Source0.M_Source0"),
        ):
            payload.extend(struct.pack("<ii", section_index, source_index))
            payload.extend(fstring(name))
            payload.extend(fstring(path))
            payload.extend(fstring(""))
            payload.extend(struct.pack("<i", 0))

        model = self.read_model(chunk("FMODEL_MATERIALS", 2, bytes(payload)))

        self.assertEqual(
            [(slot.slot_index, slot.source_material_index, slot.unreal_material_path) for slot in model.material_links.slots],
            [(0, 1, "/Game/Materials/M_Source1.M_Source1"), (1, 0, "/Game/Materials/M_Source0.M_Source0")],
        )

    def test_allows_repeated_v2_source_material_indices(self):
        payload = bytearray(struct.pack("<Bi", 2, 2))
        for section_index in range(2):
            payload.extend(struct.pack("<ii", section_index, 1))
            payload.extend(fstring("Slot"))
            payload.extend(fstring("/Game/M"))
            payload.extend(fstring(""))
            payload.extend(struct.pack("<i", 0))
        model = self.read_model(chunk("FMODEL_MATERIALS", 2, bytes(payload)))
        self.assertEqual([slot.source_material_index for slot in model.material_links.slots], [1, 1])

    def test_accepts_v2_absent_source_material_sentinel(self):
        payload = bytearray(struct.pack("<Bi", 2, 1))
        payload.extend(struct.pack("<ii", 0, -1))
        payload.extend(fstring("Slot"))
        payload.extend(fstring("/Game/M"))
        payload.extend(fstring(""))
        payload.extend(struct.pack("<i", 0))
        model = self.read_model(chunk("FMODEL_MATERIALS", 1, bytes(payload)))
        self.assertIsNone(model.material_links.slots[0].source_material_index)

    def test_read_uemodel_source_lods_aggregates_repeated_normalized_material_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "model.uemodel"
            fixture.write_bytes(uemodel_bytes(source_materials=(
                (3, "/Game/Materials/M_Body"),
                (3, "/Game/Materials/M_Body.M_Body"),
            )))

            preflight = logic.read_uemodel_source_lods(fixture)

        self.assertEqual(preflight.source_lods, frozenset({0}))
        self.assertEqual(preflight.material_slots, {3: "/Game/Materials/M_Body"})

    def test_read_uemodel_source_lods_reads_both_compressed_header_sizes(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "model.uemodel"
            fixture.write_bytes(compressed_uemodel_bytes(source_materials=(
                (3, "/Game/Materials/M_Body.M_Body"),
                (1, "/Game/Materials/M_Head.M_Head"),
            )))

            preflight = logic.read_uemodel_source_lods(fixture)

        self.assertEqual(preflight.source_lods, frozenset({0}))
        self.assertEqual(preflight.material_slots, {
            3: "/Game/Materials/M_Body",
            1: "/Game/Materials/M_Head",
        })

    def test_zstd_compressed_uemodel_validates_in_preflight_and_normal_import(self):
        uncompressed_payload = uemodel_payload(source_materials=((3, "/Game/Materials/M_Body.M_Body"),))
        compressed_payload = b"zstd fixture payload"
        data = compressed_uemodel_bytes(
            source_materials=((3, "/Game/Materials/M_Body.M_Body"),),
            compression_type="ZSTD",
            payload=compressed_payload,
        )

        class Decompressor:
            def __init__(self):
                self.calls = []

            def decompress(self, payload, max_output_size):
                self.calls.append((payload, max_output_size))
                if payload != compressed_payload:
                    raise AssertionError("ZSTD decompressor received the wrong payload")
                if max_output_size != len(uncompressed_payload):
                    raise AssertionError("ZSTD decompressor received the wrong output size")
                return uncompressed_payload

        decompressor = Decompressor()
        package = sys.modules["io_scene_ueformat"]
        previous = getattr(package, "zstd_decompressor", None)
        package.zstd_decompressor = decompressor
        try:
            with tempfile.TemporaryDirectory() as directory:
                fixture = Path(directory) / "model.uemodel"
                fixture.write_bytes(data)
                preflight = logic.read_uemodel_source_lods(fixture)

            importer = logic.UEFormatImport.__new__(logic.UEFormatImport)
            importer.options = types.SimpleNamespace(scale_factor=1.0)
            importer.material_options = None
            imported_archives = []
            importer.import_uemodel_data = lambda archive, name, material_options: imported_archives.append((archive, name))
            importer.import_data_by_reader(reader.FArchiveReader(data))
        finally:
            if previous is None:
                del package.zstd_decompressor
            else:
                package.zstd_decompressor = previous

        self.assertEqual(preflight.source_lods, frozenset({0}))
        self.assertEqual(preflight.material_slots, {3: "/Game/Materials/M_Body"})
        self.assertEqual(len(imported_archives), 1)
        self.assertEqual(len(decompressor.calls), 2)

    def test_zstd_header_size_mismatches_reject_preflight_and_normal_import(self):
        uncompressed_payload = uemodel_payload(source_materials=((3, "/Game/Materials/M_Body.M_Body"),))
        compressed_payload = b"zstd fixture payload"

        class Decompressor:
            def decompress(self, payload, max_output_size):
                return uncompressed_payload

        package = sys.modules["io_scene_ueformat"]
        previous = getattr(package, "zstd_decompressor", None)
        package.zstd_decompressor = Decompressor()
        try:
            for label, sizes, message in (
                ("compressed", {"compressed_size": 1}, "Compressed payload size does not match header"),
                ("uncompressed", {"uncompressed_size": 1}, "Decompressed payload size does not match header"),
            ):
                with self.subTest(header=label):
                    data = compressed_uemodel_bytes(
                        source_materials=((3, "/Game/Materials/M_Body.M_Body"),),
                        compression_type="ZSTD",
                        payload=compressed_payload,
                        **sizes,
                    )
                    with tempfile.TemporaryDirectory() as directory:
                        fixture = Path(directory) / "model.uemodel"
                        fixture.write_bytes(data)
                        with self.assertRaisesRegex(ValueError, message):
                            logic.read_uemodel_source_lods(fixture)
                    with self.assertRaisesRegex(ValueError, message):
                        logic.UEFormatImport.__new__(logic.UEFormatImport).import_data_by_reader(reader.FArchiveReader(data))
        finally:
            if previous is None:
                del package.zstd_decompressor
            else:
                package.zstd_decompressor = previous

    def test_rejects_compressed_payload_size_mismatch_before_parsing(self):
        data = compressed_uemodel_bytes(
            source_materials=((3, "/Game/Materials/M_Body.M_Body"),),
            compressed_size=1,
        )

        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "model.uemodel"
            fixture.write_bytes(data)
            with self.assertRaisesRegex(ValueError, "Compressed payload size does not match header"):
                logic.read_uemodel_source_lods(fixture)
        with self.assertRaisesRegex(ValueError, "Compressed payload size does not match header"):
            logic.UEFormatImport.__new__(logic.UEFormatImport).import_data_by_reader(reader.FArchiveReader(data))

    def test_rejects_uncompressed_payload_size_mismatch_before_parsing(self):
        data = compressed_uemodel_bytes(
            source_materials=((3, "/Game/Materials/M_Body.M_Body"),),
            uncompressed_size=1,
        )

        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "model.uemodel"
            fixture.write_bytes(data)
            with self.assertRaisesRegex(ValueError, "Decompressed payload size does not match header"):
                logic.read_uemodel_source_lods(fixture)
        with self.assertRaisesRegex(ValueError, "Decompressed payload size does not match header"):
            logic.UEFormatImport.__new__(logic.UEFormatImport).import_data_by_reader(reader.FArchiveReader(data))

    def test_rejects_truncated_compressed_payload_before_decompression(self):
        original = compressed_uemodel_bytes(source_materials=((3, "/Game/Materials/M_Body.M_Body"),))
        data = original[:-1]

        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "model.uemodel"
            fixture.write_bytes(data)
            with self.assertRaisesRegex(ValueError, "Compressed payload size does not match header"):
                logic.read_uemodel_source_lods(fixture)
        with self.assertRaisesRegex(ValueError, "Compressed payload size does not match header"):
            logic.UEFormatImport.__new__(logic.UEFormatImport).import_data_by_reader(reader.FArchiveReader(data))

    def test_read_uemodel_source_lods_rejects_conflicting_repeated_material_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "model.uemodel"
            fixture.write_bytes(uemodel_bytes(source_materials=(
                (3, "/Game/Materials/M_Body"),
                (3, "/Game/Materials/M_Head"),
            )))

            with self.assertRaisesRegex(logic.UEMODELSourceMaterialAmbiguityError, "source material index 3"):
                logic.read_uemodel_source_lods(fixture)

    def test_read_uemodel_source_lods_rejects_repeated_malformed_material_identities(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "model.uemodel"
            fixture.write_bytes(uemodel_bytes(source_materials=(
                (3, "/Game//Materials/M_Body"),
                (3, "/Game/Materials/"),
            )))

            with self.assertRaisesRegex(logic.UEMODELSourceMaterialAmbiguityError, "invalid identity"):
                logic.read_uemodel_source_lods(fixture)

    def test_read_uemodel_source_lods_rejects_malformed_and_valid_shared_source_index(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "model.uemodel"
            fixture.write_bytes(uemodel_bytes(source_materials=(
                (3, "/Game//Materials/M_Body"),
                (3, "/Game/Materials/M_Body.M_Body"),
            )))

            with self.assertRaisesRegex(logic.UEMODELSourceMaterialAmbiguityError, "invalid identity"):
                logic.read_uemodel_source_lods(fixture)

    def test_read_uemodel_source_lods_omits_absent_source_material_sentinel(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "model.uemodel"
            fixture.write_bytes(uemodel_bytes(source_materials=((-1, "/Game/Materials/M_Body"),)))

            preflight = logic.read_uemodel_source_lods(fixture)

        self.assertEqual(preflight.material_slots, {})

    def test_rejects_invalid_v2_source_material_indices(self):
        for source_indices, message in (((-2,), "source_material_index"),):
            payload = bytearray(struct.pack("<Bi", 2, 2 if len(source_indices) > 1 else 1))
            for section_index, source_index in enumerate(source_indices):
                payload.extend(struct.pack("<ii", section_index, source_index))
                payload.extend(fstring("Slot"))
                payload.extend(fstring("/Game/M"))
                payload.extend(fstring(""))
                payload.extend(struct.pack("<i", 0))
            with self.subTest(source_indices=source_indices), self.assertRaisesRegex(ValueError, message):
                self.read_model(chunk("FMODEL_MATERIALS", len(source_indices), bytes(payload)))

    def test_reads_deep_relative_uri_from_protocol_fixture(self):
        # The current canonical CUE4Parse checkout no longer carries the old
        # GoldenFixtureWriterTests producer. Keep this reader-side contract
        # test independent of an optional producer test while exercising the
        # same UEMODEL material-link payload and relative URI semantics.
        model = self.read_model(
            chunk(
                "FMODEL_MATERIALS",
                1,
                material_links_payload(
                    texture_uri="../../Textures/T_Deep.png",
                    texture_location_mode=fmodel.RELATIVE_LOCATION_MODE,
                ),
            ),
        )

        texture = model.material_links.slots[0].parameters[0].texture
        self.assertEqual(texture.location_mode, fmodel.RELATIVE_LOCATION_MODE)
        self.assertEqual(texture.uri, "../../Textures/T_Deep.png")

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
                    expected = (
                        "FMODEL_TEXTURES chunk array_size"
                        if name == "FMODEL_TEXTURES"
                        else f"{count_name} count.*top-level"
                    )
                    with self.assertRaisesRegex(ValueError, expected):
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
        for name, payload, envelope_count in (
            ("FMODEL_MATERIALS", material_links_payload(slot_count=0), 0),
            ("FMODEL_TEXTURES", textures_payload(count=0), 1),
            ("FMODEL_PBR_INTERFACES", pbr_interfaces_payload(records=()), 0),
        ):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "duplicate top-level chunk"):
                self.read_model(
                    chunk(name, envelope_count, payload),
                    chunk(name, envelope_count, payload),
                )

    def test_rejects_duplicate_embedded_texture_ids_and_count_contradictions(self):
        first = legacy_embedded_textures_payload()
        second = legacy_embedded_textures_payload()
        duplicate_ids = first[:1] + struct.pack("<i", 2) + first[5:] + second[5:]
        with self.assertRaisesRegex(ValueError, "duplicate embedded texture id: 7"):
            self.read_model(chunk("FMODEL_TEXTURES", 2, duplicate_ids))

        with self.assertRaisesRegex(ValueError, "texture count cannot fit"):
            self.read_model(chunk("FMODEL_TEXTURES", 1, legacy_embedded_textures_payload(count=1)[:5]))

    def test_rejects_unsupported_material_extension_version(self):
        with self.assertRaisesRegex(ValueError, "extension version"):
            self.read_model(chunk("FMODEL_MATERIALS", 1, material_links_payload(extension_version=3)))

        with self.assertRaisesRegex(ValueError, "extension version"):
            self.read_model(chunk("FMODEL_TEXTURES", 1, textures_payload(version=3)))

    def test_identity_v1_malformed_payload_does_not_silently_fallback_to_legacy_embedded_texture_reader(self):
        malformed_new_payload = bytes((1,)) + struct.pack("<i", 1) + struct.pack("<i", len(b"tex.base")) + b"tex.base"

        with mock.patch.object(classes.TextureResource, "_read_legacy_resources", side_effect=AssertionError("legacy fallback used")) as legacy_reader:
            with self.assertRaises(ValueError):
                self.read_model(chunk("FMODEL_TEXTURES", 1, malformed_new_payload))
        legacy_reader.assert_not_called()

    def test_rejects_negative_extension_counts(self):
        with self.assertRaisesRegex(ValueError, "slot count"):
            self.read_model(chunk("FMODEL_MATERIALS", 1, material_links_payload(slot_count=-1)))

        with self.assertRaisesRegex(ValueError, "parameter count"):
            self.read_model(chunk("FMODEL_MATERIALS", 1, material_links_payload(parameter_count=-1)))

        with self.assertRaisesRegex(ValueError, "texture count"):
            self.read_model(chunk("FMODEL_TEXTURES", 1, legacy_embedded_textures_payload(count=-1)))

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
            envelope_count = 1 if name == "FMODEL_TEXTURES" else count
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, f"{count_name} exceeds maximum"):
                self.read_model(chunk(name, envelope_count, make_payload(count)))

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
        payload = legacy_embedded_textures_payload(data=b"PNG")[:-1]

        with self.assertRaises(ValueError):
            self.read_model(chunk("FMODEL_TEXTURES", 1, payload))

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
        for name, payload, message in (
            ("FMODEL_MATERIALS", material_links_payload() + b"junk", "Trailing bytes"),
            ("FMODEL_TEXTURES", identity_textures_payload() + b"junk", "Trailing FModel texture identity data"),
        ):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, message):
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

    def test_relative_uri_without_export_root_is_confined_to_model_parent(self):
        texture = classes.TextureResource(id=1, unreal_object_path="", location_mode=1, uri="textures/body.png")

        expected = (self.model_path.parent / texture.uri).resolve(strict=False)
        self.assertEqual(self.resolve(texture, ue_export_root=None), expected)
        self.assertEqual(self.resolve(texture, ue_export_root=""), expected)

    def test_owner_relative_leading_parents_infer_content_root(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            content_root = Path(temporary_directory) / "Output/Exports/CodeWorld/Content"
            model_path = content_root / "Characters/Hero/Model/SM_Body.uemodel"
            texture_path = content_root / "Characters/Hero/Texture/T_Body.png"
            model_path.parent.mkdir(parents=True)
            texture_path.parent.mkdir(parents=True)
            resolver = fmodel.FModelResourceResolver(
                model_path=model_path,
                fmodel_textures={},
                ue_export_root=None,
                bundle_root=None,
            )
            texture = classes.TextureResource(
                id=1,
                unreal_object_path="",
                location_mode=1,
                uri="../Texture/T_Body.png",
            )

            self.assertEqual(resolver.resolve_texture(texture), texture_path.resolve(strict=False))

    def test_owner_relative_leading_parents_without_known_root_fail_closed(self):
        resolver = fmodel.FModelResourceResolver(
            model_path=Path("C:/loose/models/mesh.uemodel"),
            fmodel_textures={},
            ue_export_root=None,
            bundle_root=None,
        )
        texture = classes.TextureResource(
            id=1,
            unreal_object_path="",
            location_mode=1,
            uri="../textures/body.png",
        )

        with self.assertRaisesRegex(ValueError, "requires ue_export_root|Content root"):
            resolver.resolve_texture(texture)

    def test_relative_uri_allows_leading_parents_when_resolved_inside_export_root(self):
        model_path = Path("C:/exports/characters/hero/models/mesh.uemodel")
        texture = classes.TextureResource(
            id=1, unreal_object_path="", location_mode=1, uri="../../Textures/body.png"
        )
        resolver = fmodel.FModelResourceResolver(
            model_path=model_path,
            fmodel_textures={},
            ue_export_root=Path("C:/exports"),
            bundle_root=None,
        )

        self.assertEqual(
            resolver.resolve_texture(texture),
            Path("C:/exports/characters/Textures/body.png"),
        )

    def test_relative_uri_rejects_leading_parents_that_escape_export_root(self):
        texture = classes.TextureResource(
            id=1, unreal_object_path="", location_mode=1, uri="../../../../outside.png"
        )

        with self.assertRaisesRegex(ValueError, "escapes configured or inferred export root"):
            self.resolve(texture)

    def test_file_uri_modes_reject_noncanonical_relative_paths(self):
        unsafe_uris = (
            "",
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
            mode_unsafe_uris = unsafe_uris if mode == 1 else ("../body.png", *unsafe_uris)
            for unsafe_uri in mode_unsafe_uris:
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
        with self.assertRaisesRegex(ValueError, "does not carry embedded bytes"):
            self.resolve(
                texture,
                fmodel_textures={
                    7: classes.TextureResource(
                        id=7,
                        unreal_object_path="",
                        location_mode=1,
                        uri="texture.png",
                    )
                },
            )

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
            with self.assertRaisesRegex(ValueError, "escapes configured or inferred export root"):
                resolver.resolve_texture(texture)

    def test_export_root_identity_uri_verifies_size_and_sha256(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            export_root = Path(temporary_directory) / "Exports"
            model_path = export_root / "Game/Models/SM_Body.uemodel"
            texture_path = export_root / "Game/Textures/T_Body.png"
            model_path.parent.mkdir(parents=True)
            texture_path.parent.mkdir(parents=True)
            contents = b"verified texture bytes"
            texture_path.write_bytes(contents)
            texture = classes.TextureResource(
                id=0,
                unreal_object_path="",
                location_mode=fmodel.EXPORT_ROOT_LOCATION_MODE,
                uri="Game/Textures/T_Body.png",
                stable_id="tex.body",
                expected_byte_length=len(contents),
                expected_content_hash=__import__("hashlib").sha256(contents).digest(),
            )
            resolver = fmodel.FModelResourceResolver(
                model_path=model_path,
                fmodel_textures={},
                ue_export_root=export_root,
                bundle_root=None,
            )

            self.assertEqual(resolver.resolve_texture(texture), texture_path.resolve(strict=False))

    def test_export_root_identity_uri_auto_discovers_export_root_from_model_ancestors(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            export_root = Path(temporary_directory) / "Output" / "Exports"
            model_path = export_root / "CodeWorld/Content/Characters/Hero/Model/SM_Body.uemodel"
            texture_path = export_root / "CodeWorld/Content/Characters/Hero/Textures/T_Body.png"
            model_path.parent.mkdir(parents=True)
            texture_path.parent.mkdir(parents=True)
            contents = b"auto discovered"
            texture_path.write_bytes(contents)
            texture = classes.TextureResource(
                id=0,
                unreal_object_path="",
                location_mode=fmodel.EXPORT_ROOT_LOCATION_MODE,
                uri="CodeWorld/Content/Characters/Hero/Textures/T_Body.png",
                stable_id="tex.body",
                expected_byte_length=len(contents),
                expected_content_hash=__import__("hashlib").sha256(contents).digest(),
            )
            resolver = fmodel.FModelResourceResolver(
                model_path=model_path,
                fmodel_textures={},
                ue_export_root=None,
                bundle_root=None,
            )

            self.assertEqual(resolver.resolve_texture(texture), texture_path.resolve(strict=False))

    def test_export_root_identity_uri_rejects_size_hash_and_embedded_metadata_mismatches(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            export_root = Path(temporary_directory)
            texture_path = export_root / "Game/Textures/T_Body.png"
            texture_path.parent.mkdir(parents=True)
            contents = b"texture"
            texture_path.write_bytes(contents)
            base = dict(
                id=0,
                unreal_object_path="",
                location_mode=fmodel.EXPORT_ROOT_LOCATION_MODE,
                uri="Game/Textures/T_Body.png",
                stable_id="tex.body",
            )
            resolver = fmodel.FModelResourceResolver(
                model_path=export_root / "Game/Models/SM_Body.uemodel",
                fmodel_textures={},
                ue_export_root=export_root,
                bundle_root=None,
            )

            with self.assertRaisesRegex(ValueError, "byte length mismatch"):
                resolver.resolve_texture(classes.TextureResource(
                    **base,
                    expected_byte_length=len(contents) + 1,
                    expected_content_hash=__import__("hashlib").sha256(contents).digest(),
                ))
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                resolver.resolve_texture(classes.TextureResource(
                    **base,
                    expected_byte_length=len(contents),
                    expected_content_hash=b"\x00" * 32,
                ))
            with self.assertRaisesRegex(ValueError, "marked embedded"):
                resolver.resolve_texture(classes.TextureResource(
                    **base,
                    expected_byte_length=len(contents),
                    expected_content_hash=__import__("hashlib").sha256(contents).digest(),
                    identity_embedded=True,
                ))

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
    def test_external_cache_key_uses_blender_compatible_windows_path(self):
        texture = classes.TextureResource(
            id=1,
            unreal_object_path="/Game/Textures/T_Body.T_Body",
            location_mode=1,
            uri="textures/body.png",
        )
        resource = Path(r"C:\exports\textures\body.png")
        with mock.patch.object(material_images, "_blender_file_path", return_value="short-body.png") as convert:
            with mock.patch("builtins.open", mock.mock_open(read_data=b"texture")) as opened:
                with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("raw Path.read_bytes used")):
                    key = material_images._resource_cache_key(texture, resource, "COLOR")
        convert.assert_called_once_with(resource)
        opened.assert_called_once_with("short-body.png", "rb")
        self.assertEqual(key[1], __import__("hashlib").sha256(b"texture").hexdigest())

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

    def test_identity_texture_cache_uses_stable_id_when_unreal_path_is_unavailable(self):
        texture = classes.TextureResource(
            id=0,
            unreal_object_path="",
            stable_id="tex.body.base",
        )
        key = material_images._resource_cache_key(texture, b"same bytes", "COLOR")
        self.assertEqual(key[0], "tex.body.base")

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
    def test_v2_source_material_index_maps_link_slot_to_dense_lod_material(self):
        lod_materials = [
            classes.Material("LOD 0", "", 0, 1, 0),
            classes.Material("LOD 1", "", 0, 1, 1),
        ]
        links = classes.FModelMaterialLinks(
            slots=[classes.FModelMaterialSlot(0, "Section", "", "", 1, [])],
        )

        self.assertEqual(
            material_builder.source_slot_mapping_for_lod_materials(lod_materials, links),
            {0: 1},
        )

    def test_v1_link_slot_falls_back_to_dense_lod_material_index(self):
        lod_materials = [classes.Material("LOD 0", "", 0, 1, 0)]
        links = classes.FModelMaterialLinks(
            slots=[classes.FModelMaterialSlot(0, "Section", "", "", None, [])],
        )

        self.assertEqual(
            material_builder.source_slot_mapping_for_lod_materials(lod_materials, links),
            {0: 0},
        )

    def test_repeated_v2_source_material_indices_can_share_one_blender_slot(self):
        slots = [
            classes.FModelMaterialSlot(0, "Section A", "", "", 1, []),
            classes.FModelMaterialSlot(1, "Section B", "", "", 1, []),
        ]

        self.assertEqual(
            material_builder._source_slot_to_blender_indices(slots, {0: 1, 1: 1}),
            {0: 1, 1: 1},
        )

    def test_source_slots_require_an_explicit_dense_blender_mapping(self):
        slots = [
            classes.FModelMaterialSlot(2, "Sparse", "", "", []),
        ]

        self.assertEqual(
            material_builder._source_slot_to_blender_indices(slots, {2: 0}),
            {2: 0},
        )
        self.assertEqual(
            material_builder._source_slot_to_blender_indices(slots, {0: 0, 2: 1}),
            {2: 1},
        )
        with self.assertRaisesRegex(ValueError, "source slot 2 has no Blender material mapping"):
            material_builder._source_slot_to_blender_indices(slots, {})
        with self.assertRaisesRegex(ValueError, "duplicate Blender material index 0"):
            material_builder._source_slot_to_blender_indices(
                slots + [classes.FModelMaterialSlot(3, "Other", "", "", [])],
                {2: 0, 3: 0},
            )

    def test_identity_textures_obey_the_external_texture_loading_switch(self):
        slot = classes.FModelMaterialSlot(
            0,
            "Body",
            "/Game/Materials/M_Body.M_Body",
            "materials/body.json",
            parameters=[classes.FModelMaterialParameter(
                name="FMODEL_TEXTURES.BaseColor",
                kind=1,
                semantic="base_color",
                value=classes.TextureResource(
                    id=0,
                    unreal_object_path="",
                    location_mode=fmodel.EXPORT_ROOT_LOCATION_MODE,
                    uri="Game/Textures/T_Body.png",
                    stable_id="tex.body",
                    expected_byte_length=4,
                    expected_content_hash=b"\x11" * 32,
                ),
            )],
        )
        images = types.SimpleNamespace(
            resource_cache_key=lambda *_: self.fail("disabled external texture must not resolve"),
        )
        options = material_builder.FModelMaterialImportOptions(load_external=False)

        identity = material_builder._material_cache_identity(slot, [], images, options)

        self.assertIn('"parameters":[]', identity)
        self.assertIn(fmodel.EXPORT_ROOT_LOCATION_MODE, material_builder._EXTERNAL_LOCATION_MODES)

    def test_orm_material_path_separates_green_roughness_and_blue_metallic(self):
        class Socket:
            def __init__(self, name):
                self.name = name

        class Location:
            def __init__(self, value=(0, 0)):
                self.x, self.y = value

        class Node:
            def __init__(self, node_type):
                self.type = "FRAME" if node_type == "NodeFrame" else node_type
                self.bl_idname = node_type
                self.label = ""
                self.name = ""
                self.image = None
                self._location = Location()
                self.parent = None
                self.shrink = False
                self.use_custom_color = False
                self.color = (0.0, 0.0, 0.0)
                self.inputs = {}
                self.outputs = {}
                if node_type == "ShaderNodeOutputMaterial":
                    self.inputs = {"Surface": Socket("Surface")}
                elif node_type == "ShaderNodeBsdfPrincipled":
                    self.inputs = {
                        "Base Color": Socket("Base Color"),
                        "Normal": Socket("Normal"),
                        "Emission Color": Socket("Emission Color"),
                        "Roughness": Socket("Roughness"),
                        "Metallic": Socket("Metallic"),
                    }
                    self.outputs = {"BSDF": Socket("BSDF")}
                elif node_type == "ShaderNodeTexImage":
                    self.inputs = {"Vector": Socket("Vector")}
                    self.outputs = {"Color": Socket("Color")}
                elif node_type == "ShaderNodeUVMap":
                    self.outputs = {"UV": Socket("UV")}
                    self.uv_map = ""
                elif node_type == "ShaderNodeSeparateColor":
                    self.inputs = {"Color": Socket("Color")}
                    self.outputs = {
                        "Red": Socket("Red"),
                        "Green": Socket("Green"),
                        "Blue": Socket("Blue"),
                    }

            @property
            def location(self):
                return self._location

            @location.setter
            def location(self, value):
                self._location = Location(value)

        class Nodes(list):
            def clear(self):
                super().clear()

            def new(self, node_type):
                node = Node(node_type)
                self.append(node)
                return node

        class Links(list):
            def new(self, source, target):
                self.append((source.name, target.name))

        nodes = Nodes()
        links = Links()
        material = types.SimpleNamespace(
            use_nodes=False,
            node_tree=types.SimpleNamespace(nodes=nodes, links=links),
        )
        mesh = types.SimpleNamespace(
            uv_layers=types.SimpleNamespace(get=lambda name: object() if name == "UV0" else None),
        )
        slot = classes.FModelMaterialSlot(
            0,
            "Body",
            "/Game/Materials/M_Body.M_Body",
            "materials/body.json",
            parameters=[classes.FModelMaterialParameter(
                name="FMODEL_TEXTURES.Orm",
                kind=1,
                semantic="orm",
                value=classes.TextureResource(
                    id=0,
                    unreal_object_path="",
                    location_mode=fmodel.EXPORT_ROOT_LOCATION_MODE,
                    uri="Game/Textures/T_Body_ORM.png",
                    stable_id="tex.orm",
                    expected_byte_length=4,
                    expected_content_hash=b"\x11" * 32,
                ),
            )],
        )
        images = types.SimpleNamespace(get_image=lambda parameter, interpretation: object())

        material_builder._build_material(
            material,
            mesh,
            slot,
            images,
            self.fail,
            material_builder.FModelMaterialImportOptions(),
        )

        self.assertIn(("Green", "Roughness"), links)
        self.assertIn(("Blue", "Metallic"), links)
        self.assertTrue(any(node.type == "ShaderNodeSeparateColor" for node in nodes))
        frame = next(node for node in nodes if node.type == "FRAME")
        texture = next(node for node in nodes if node.type == "ShaderNodeTexImage")
        uv = next(node for node in nodes if node.type == "ShaderNodeUVMap")
        separate = next(node for node in nodes if node.type == "ShaderNodeSeparateColor")
        self.assertTrue(frame.shrink)
        self.assertIs(texture.parent, frame)
        self.assertIs(uv.parent, frame)
        self.assertEqual((uv.location.x, texture.location.x, separate.location.x), (-260, 0, 260))
        self.assertEqual(texture.location.y, uv.location.y)

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
