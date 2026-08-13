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
    )


classes, reader = load_modules()


def fstring(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return struct.pack("<i", len(encoded)) + encoded


def raw_fstring(payload: bytes) -> bytes:
    return struct.pack("<i", len(payload)) + payload


def chunk(name: str, count: int, payload: bytes, *, byte_size: int | None = None) -> bytes:
    declared_size = len(payload) if byte_size is None else byte_size
    return fstring(name) + struct.pack("<ii", count, declared_size) + payload


def texture_record(
    stable_id: str,
    *,
    logical_resource_uri: str,
    is_embedded: int,
    byte_length: int,
    content_hash: bytes,
    location_mode: int = 4,
    version: int = 1,
) -> bytes:
    # Match the canonical C# writer/inspector order:
    # StableId, LogicalResourceUri, [LocationMode in v2], IsEmbedded,
    # ByteLength, SHA-256.
    return (
        fstring(stable_id)
        + fstring(logical_resource_uri)
        + (bytes([location_mode]) if version >= 2 else b"")
        + bytes([is_embedded])
        + struct.pack("<i", byte_length)
        + content_hash
    )


def binding_record(material_path: str, map_kind: int, texture_id: str, is_srgb: int) -> bytes:
    return fstring(material_path) + bytes([map_kind]) + fstring(texture_id) + bytes([is_srgb])


def textures_payload(
    textures: tuple[tuple[str, str, int, int, bytes], ...] = (
        ("tex.base", "textures/body/basecolor.png", 1, 1024, b"\x11" * 32),
        ("tex.normal", "textures/body/normal.png", 0, 1536, b"\x22" * 32),
        ("tex.orm", "textures/body/orm.png", 0, 2048, b"\x33" * 32),
    ),
    bindings: tuple[tuple[str, int, str, int], ...] = (
        ("/Game/Materials/M_Body.M_Body", 0, "tex.base", 1),
        ("/Game/Materials/M_Body.M_Body", 1, "tex.normal", 0),
        ("/Game/Materials/M_Body.M_Body", 2, "tex.orm", 0),
    ),
    *,
    version: int = 1,
    location_modes: tuple[int, ...] | None = None,
    count: int | None = None,
    binding_count: int | None = None,
) -> bytes:
    payload = bytearray()
    payload.append(version)
    payload.extend(struct.pack("<i", len(textures) if count is None else count))
    for index, (stable_id, logical_resource_uri, is_embedded, byte_length, content_hash) in enumerate(textures):
        payload.extend(
            texture_record(
                stable_id,
                logical_resource_uri=logical_resource_uri,
                is_embedded=is_embedded,
                byte_length=byte_length,
                content_hash=content_hash,
                location_mode=(location_modes[index] if location_modes is not None else (3 if is_embedded else 4)),
                version=version,
            )
        )
    payload.extend(struct.pack("<i", len(bindings) if binding_count is None else binding_count))
    for material_path, map_kind, texture_id, is_srgb in bindings:
        payload.extend(binding_record(material_path, map_kind, texture_id, is_srgb))
    return bytes(payload)


def materials_payload_v2(
    slots: tuple[tuple[int, int, str, str], ...],
) -> bytes:
    payload = bytearray((2,))
    payload.extend(struct.pack("<i", len(slots)))
    for slot_index, source_material_index, slot_name, unreal_material_path in slots:
        payload.extend(struct.pack("<ii", slot_index, source_material_index))
        payload.extend(fstring(slot_name))
        payload.extend(fstring(unreal_material_path))
        payload.extend(fstring("materials/body.json"))
        payload.extend(struct.pack("<i", 0))
    return bytes(payload)


class FModelMaterialTexturesReaderTests(unittest.TestCase):
    def read_model(self, payload: bytes, *, count: int = 1):
        return classes.UEModel.from_archive(reader.FArchiveReader(chunk("FMODEL_TEXTURES", count, payload)))

    def test_projects_identity_bindings_into_every_matching_material_slot(self):
        materials = chunk(
            "FMODEL_MATERIALS",
            2,
            materials_payload_v2((
                (0, 0, "Body", "/Game/Materials/M_Body.M_Body"),
                (1, 0, "Body", "/Game/Materials/M_Body.M_Body"),
            )),
        )
        textures = chunk(
            "FMODEL_TEXTURES",
            1,
            textures_payload(
                textures=(
                    ("tex.base", "Game/Textures/T_Body_BC.png", 0, 1024, b"\x11" * 32),
                    ("tex.normal", "Game/Textures/T_Body_N.png", 0, 1536, b"\x22" * 32),
                    ("tex.orm", "Game/Textures/T_Body_ORM.png", 0, 2048, b"\x33" * 32),
                ),
                bindings=(
                    ("Body", 0, "tex.base", 1),
                    ("Body", 1, "tex.normal", 0),
                    ("Body", 2, "tex.orm", 0),
                ),
            ),
        )

        model = classes.UEModel.from_archive(reader.FArchiveReader(materials + textures))

        self.assertEqual(len(model.material_links.slots), 2)
        for slot in model.material_links.slots:
            self.assertEqual([parameter.semantic for parameter in slot.parameters], ["base_color", "normal", "orm"])
            self.assertEqual([parameter.texture.location_mode for parameter in slot.parameters], [4, 4, 4])
            self.assertEqual([parameter.texture.stable_id for parameter in slot.parameters], ["tex.base", "tex.normal", "tex.orm"])
            self.assertTrue(all(parameter.texture.kind is classes.TextureResourceKind.IDENTITY for parameter in slot.parameters))
            self.assertEqual([parameter.texture.expected_byte_length for parameter in slot.parameters], [1024, 1536, 2048])
            self.assertEqual(slot.parameters[0].texture.expected_content_hash, b"\x11" * 32)
            self.assertEqual(slot.parameters[0].texture.color_space, 1)
            self.assertEqual(slot.parameters[2].texture.packing, 1)

    def test_rejects_identity_binding_without_matching_material_id_when_material_links_exist(self):
        materials = chunk(
            "FMODEL_MATERIALS",
            1,
            materials_payload_v2(((0, 0, "Body", "/Game/Materials/M_Body.M_Body"),)),
        )
        textures = chunk(
            "FMODEL_TEXTURES",
            1,
            textures_payload(
                textures=(("tex.base", "Game/Textures/T_Body_BC.png", 0, 4, b"\x11" * 32),),
                bindings=(("Other", 0, "tex.base", 1),),
            ),
        )

        with self.assertRaisesRegex(ValueError, "material_id does not exist"):
            classes.UEModel.from_archive(reader.FArchiveReader(materials + textures))

    def test_accepts_slot_name_material_ids_used_by_the_new_pipeline(self):
        model = self.read_model(
            textures_payload(
                textures=(("tex.base", "Game/Textures/T_Body_BC.png", 0, 4, b"\x11" * 32),),
                bindings=(("Body", 0, "tex.base", 1),),
            ),
            count=1,
        )
        self.assertEqual(model.material_textures.bindings[0].material_path, "Body")
        self.assertEqual(model.material_textures.bindings[0].material_id, "Body")

    def test_reads_texture_identity_records_and_bindings(self):
        model = self.read_model(textures_payload())

        self.assertIsNotNone(model.material_textures)
        identity = model.material_textures
        self.assertEqual(identity.version, 1)
        self.assertEqual([record.texture_id for record in identity.textures], ["tex.base", "tex.normal", "tex.orm"])
        self.assertEqual(
            [record.logical_resource_uri for record in identity.textures],
            ["textures/body/basecolor.png", "textures/body/normal.png", "textures/body/orm.png"],
        )
        self.assertEqual([record.is_embedded for record in identity.textures], [True, False, False])
        self.assertEqual([record.byte_length for record in identity.textures], [1024, 1536, 2048])
        self.assertEqual([record.content_hash for record in identity.textures], [b"\x11" * 32, b"\x22" * 32, b"\x33" * 32])
        self.assertEqual(
            [
                (binding.material_path, binding.map_kind.value, binding.texture_id, binding.is_srgb)
                for binding in identity.bindings
            ],
            [
                ("/Game/Materials/M_Body.M_Body", 0, "tex.base", True),
                ("/Game/Materials/M_Body.M_Body", 1, "tex.normal", False),
                ("/Game/Materials/M_Body.M_Body", 2, "tex.orm", False),
            ],
        )
        self.assertEqual(model.textures, [])

    def test_parsed_identity_is_isolated_from_external_payload_mutation(self):
        payload = bytearray(textures_payload())
        model = self.read_model(bytes(payload))
        payload[:] = b"\0" * len(payload)

        self.assertEqual(model.material_textures.textures[0].texture_id, "tex.base")
        self.assertEqual(model.material_textures.textures[0].logical_resource_uri, "textures/body/basecolor.png")
        self.assertEqual(model.material_textures.textures[0].content_hash, b"\x11" * 32)

    def test_rejects_invalid_utf8_and_embedded_nul_strings(self):
        invalid_texture_id = textures_payload(
            textures=(("tex.base", "textures/body/basecolor.png", 1, 1024, b"\x11" * 32),),
            bindings=(),
        )
        invalid_texture_id = invalid_texture_id[:5] + raw_fstring(b"tex.\xff") + invalid_texture_id[5 + 4 + len("tex.base"):]
        with self.assertRaisesRegex(ValueError, "invalid UTF-8"):
            self.read_model(invalid_texture_id, count=1)

        invalid_logical_resource_uri = textures_payload(
            textures=(("tex.base", "/rooted/path.png", 1, 1024, b"\x11" * 32),),
            bindings=(),
        )
        with self.assertRaisesRegex(ValueError, "logical resource URI|logical_resource_uri"):
            self.read_model(invalid_logical_resource_uri, count=1)

        for control_uri in ("textures/body\x01/basecolor.png", "textures/body\x85/basecolor.png"):
            with self.subTest(control_uri=repr(control_uri)), self.assertRaisesRegex(
                ValueError, "logical resource URI|logical_resource_uri"
            ):
                self.read_model(
                    textures_payload(
                        textures=(("tex.base", control_uri, 1, 1024, b"\x11" * 32),),
                        bindings=(),
                    ),
                    count=1,
                )

        embedded_nul = textures_payload(
            textures=(("tex.base", "textures/body/basecolor.png", 1, 1024, b"\x11" * 32),),
            bindings=(("/Game/Materials/M_Body.M_Body\0", 0, "tex.base", 1),),
        )
        with self.assertRaisesRegex(ValueError, "embedded NUL|contains NUL"):
            self.read_model(embedded_nul, count=1)

    def test_rejects_malformed_material_binding_paths(self):
        invalid_material_paths = (
            "C:/tmp/x",
            "//bad/share",
            "/Game//Bad",
            "/Game/Materials/",
        )

        for material_path in invalid_material_paths:
            with self.subTest(material_path=material_path), self.assertRaisesRegex(ValueError, "material_path"):
                self.read_model(
                    textures_payload(
                        textures=(("tex.base", "textures/body/basecolor.png", 1, 1024, b"\x11" * 32),),
                        bindings=((material_path, 0, "tex.base", 1),),
                    ),
                    count=1,
                )

        valid_model = self.read_model(
            textures_payload(
                textures=(("tex.base", "textures/body/basecolor.png", 1, 1024, b"\x11" * 32),),
                bindings=(("/Game/Materials/M_Body.M_Body", 0, "tex.base", 1),),
            ),
            count=1,
        )
        self.assertEqual(valid_model.material_textures.bindings[0].material_path, "/Game/Materials/M_Body.M_Body")

    def test_rejects_short_reads_bad_version_bad_count_and_bad_hash_length(self):
        with self.assertRaisesRegex(ValueError, "version"):
            self.read_model(textures_payload(version=3))
        with self.assertRaisesRegex(ValueError, "texture count"):
            self.read_model(textures_payload(count=-1))
        with self.assertRaisesRegex(ValueError, "available data"):
            self.read_model(textures_payload()[:-1])
        with self.assertRaisesRegex(ValueError, "available data"):
            self.read_model(
                textures_payload(
                    textures=(("tex.base", "textures/body/basecolor.png", 1, 1024, b"\x11" * 31),),
                    bindings=(),
                ),
                count=1,
            )

    def test_rejects_invalid_flags_and_map_kinds(self):
        with self.assertRaisesRegex(ValueError, "is_embedded"):
            self.read_model(
                textures_payload(
                    textures=(("tex.base", "textures/body/basecolor.png", 2, 1024, b"\x11" * 32),),
                    bindings=(),
                ),
                count=1,
            )
        with self.assertRaisesRegex(ValueError, "is_srgb"):
            self.read_model(
                textures_payload(
                    textures=(("tex.base", "textures/body/basecolor.png", 1, 1024, b"\x11" * 32),),
                    bindings=(("/Game/Materials/M_Body.M_Body", 0, "tex.base", 2),),
                ),
                count=1,
            )
        with self.assertRaisesRegex(ValueError, "map kind"):
            self.read_model(
                textures_payload(
                    textures=(("tex.base", "textures/body/basecolor.png", 1, 1024, b"\x11" * 32),),
                    bindings=(("/Game/Materials/M_Body.M_Body", 3, "tex.base", 1),),
                ),
                count=1,
            )

    def test_rejects_missing_refs_duplicate_ids_descending_stable_ids_duplicate_material_map_pairs_and_noncanonical_order(self):
        with self.assertRaisesRegex(ValueError, "missing.*tex.missing"):
            self.read_model(
                textures_payload(
                    textures=(("tex.base", "textures/body/basecolor.png", 1, 1024, b"\x11" * 32),),
                    bindings=(("/Game/Materials/M_Body.M_Body", 0, "tex.missing", 1),),
                ),
                count=1,
            )
        with self.assertRaisesRegex(ValueError, "duplicate.*texture id"):
            self.read_model(
                textures_payload(
                    textures=(
                        ("tex.base", "textures/body/basecolor.png", 1, 1024, b"\x11" * 32),
                        ("tex.base", "textures/body/basecolor_alt.png", 0, 1536, b"\x22" * 32),
                    ),
                    bindings=(),
                ),
                count=1,
            )
        with self.assertRaisesRegex(ValueError, "ascending StableId|canonical ascending StableId order"):
            self.read_model(
                textures_payload(
                    textures=(
                        ("tex.zzzz", "textures/body/basecolor.png", 1, 1024, b"\x11" * 32),
                        ("tex.aaaa", "textures/body/normal.png", 0, 1536, b"\x22" * 32),
                    ),
                    bindings=(("/Game/Materials/M_Body.M_Body", 0, "tex.zzzz", 1), ("/Game/Materials/M_Body.M_Body", 1, "tex.aaaa", 0)),
                ),
                count=1,
            )
        with self.assertRaisesRegex(ValueError, "duplicate.*material/map"):
            self.read_model(
                textures_payload(
                    bindings=(
                        ("/Game/Materials/M_Body.M_Body", 0, "tex.base", 1),
                        ("/Game/Materials/M_Body.M_Body", 0, "tex.normal", 1),
                    ),
                )
            )
        with self.assertRaisesRegex(ValueError, "canonical"):
            self.read_model(
                textures_payload(
                    bindings=(
                        ("/Game/Materials/M_Body.M_Body", 1, "tex.normal", 0),
                        ("/Game/Materials/M_Body.M_Body", 0, "tex.base", 1),
                        ("/Game/Materials/M_Body.M_Body", 2, "tex.orm", 0),
                    ),
                )
            )

    def test_identity_envelope_count_is_independent_from_payload_texture_count(self):
        textures_v1 = (
            ("tex.base", "textures/body/T_Body_BC.png", 0, 1024, b"\x11" * 32),
            ("tex.normal", "textures/body/T_Body_N.png", 0, 1536, b"\x22" * 32),
        )
        textures_v2 = (
            ("tex.base", "../Texture/T_Body_BC.png", 0, 1024, b"\x11" * 32),
            ("tex.normal", "../Texture/T_Body_N.png", 0, 1536, b"\x22" * 32),
        )
        bindings = (
            ("/Game/Materials/M_Body.M_Body", 0, "tex.base", 1),
            ("/Game/Materials/M_Body.M_Body", 1, "tex.normal", 0),
        )

        for version in (1, 2):
            with self.subTest(version=version):
                model = self.read_model(
                    textures_payload(
                        version=version,
                        textures=textures_v2 if version == 2 else textures_v1,
                        bindings=bindings,
                        location_modes=(1, 1) if version == 2 else None,
                    ),
                    count=1,
                )
                identity = model.material_textures
                self.assertIsNotNone(identity)
                self.assertEqual(identity.version, version)
                self.assertEqual([record.texture_id for record in identity.textures], ["tex.base", "tex.normal"])
                self.assertEqual([record.location_mode for record in identity.textures], [1, 1] if version == 2 else [4, 4])

    def test_identity_envelope_rejects_non_singleton_top_level_count(self):
        with self.assertRaisesRegex(ValueError, "FMODEL_TEXTURES chunk array_size"):
            self.read_model(
                textures_payload(
                    textures=(
                        ("tex.base", "textures/body/basecolor.png", 0, 1024, b"\x11" * 32),
                        ("tex.normal", "textures/body/normal.png", 0, 1536, b"\x22" * 32),
                    ),
                    bindings=(),
                ),
                count=2,
            )

    def test_identity_v2_preserves_owner_relative_location_mode(self):
        model = self.read_model(
            textures_payload(
                version=2,
                textures=(("tex.base", "../Texture/T_Body.png", 0, 1024, b"\x11" * 32),),
                location_modes=(1,),
                bindings=(("/Game/Materials/M_Body.M_Body", 0, "tex.base", 1),),
            ),
            count=1,
        )
        record = model.material_textures.textures[0]
        self.assertEqual(record.location_mode, 1)
        self.assertEqual(record.logical_resource_uri, "../Texture/T_Body.png")

    def test_identity_v1_malformed_prefix_does_not_fallback_to_legacy(self):
        malformed_prefix = bytes((1,)) + struct.pack("<i", 1) + struct.pack("<i", len(b"tex.base")) + b"tex.base"

        with self.assertRaisesRegex(ValueError, "cannot fit minimum records|logical resource URI|remaining input|available data"):
            self.read_model(malformed_prefix, count=1)

    def test_rejects_trailing_bytes(self):
        with self.assertRaisesRegex(ValueError, "Trailing"):
            self.read_model(textures_payload() + b"junk")


if __name__ == "__main__":
    unittest.main()
