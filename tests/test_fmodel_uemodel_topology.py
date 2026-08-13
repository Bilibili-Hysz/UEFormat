from __future__ import annotations

import importlib
import struct
import sys
import types

import numpy as np
import pytest


PLUGIN_ROOT = __import__("pathlib").Path(__file__).parents[1] / "plugins" / "blender"
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

package = types.ModuleType("io_scene_ueformat")
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules.setdefault("io_scene_ueformat", package)
classes = importlib.import_module("io_scene_ueformat.importer.classes")
reader = importlib.import_module("io_scene_ueformat.importer.reader")


def fstring(value: str) -> bytes:
    encoded = value.encode("utf-8") + b"\0"
    return struct.pack("<i", len(encoded)) + encoded


def chunk(name: str, count: int, payload: bytes) -> bytes:
    return fstring(name) + struct.pack("<ii", count, len(payload)) + payload


def binary_lod(name: str, slot: int = 0) -> bytes:
    vertices = chunk("VERTICES", 3, struct.pack("<9f", *(0.0,) * 9))
    indices = chunk("INDICES", 3, struct.pack("<3i", 0, 1, 2))
    materials = chunk("MATERIALS", 1, fstring(f"M{slot}") + fstring(f"/Game/M{slot}.M{slot}") + struct.pack("<ii", 0, 1))
    payload = vertices + indices + materials
    return fstring(name) + struct.pack("<i", len(payload)) + payload


def binary_lod_with_tangents(name: str = "LOD0") -> bytes:
    vertices = chunk("VERTICES", 3, struct.pack("<9f", *(0.0,) * 9))
    tangents = chunk("TANGENTS", 3, struct.pack("<9f", *(0.0,) * 9))
    indices = chunk("INDICES", 3, struct.pack("<3i", 0, 1, 2))
    materials = chunk("MATERIALS", 1, fstring("M0") + fstring("/Game/M0.M0") + struct.pack("<ii", 0, 1))
    payload = vertices + tangents + indices + materials
    return fstring(name) + struct.pack("<i", len(payload)) + payload


def binary_lod_with_negative_child_count(child_name: str) -> bytes:
    vertices = chunk("VERTICES", 3, struct.pack("<9f", *(0.0,) * 9))
    indices = chunk("INDICES", 3, struct.pack("<3i", 0, 1, 2))
    materials = chunk(
        "MATERIALS",
        1,
        fstring("M0") + fstring("/Game/M0.M0") + struct.pack("<ii", 0, 1),
    )
    children = {"VERTICES": vertices, "INDICES": indices, "MATERIALS": materials}
    malformed = fstring(child_name) + struct.pack("<ii", -1, 0)
    payload = b"".join(malformed if name == child_name else value for name, value in children.items())
    return fstring("LOD0") + struct.pack("<i", len(payload)) + payload


def binary_lod_with_short_child_byte_size() -> bytes:
    vertices = chunk("VERTICES", 3, struct.pack("<9f", *(0.0,) * 9))
    indices_payload = struct.pack("<3i", 0, 1, 2)
    short_indices = fstring("INDICES") + struct.pack("<ii", 3, 1) + indices_payload
    materials = chunk(
        "MATERIALS",
        1,
        fstring("M0") + fstring("/Game/M0.M0") + struct.pack("<ii", 0, 1),
    )
    payload = vertices + short_indices + materials
    return fstring("LOD0") + struct.pack("<i", len(payload)) + payload


def archive_with_lod(lod_payload: bytes) -> reader.FArchiveReader:
    archive = reader.FArchiveReader(chunk("LODS", 1, lod_payload))
    archive.file_version = classes.EUEFormatVersion.LatestVersion
    archive.metadata["scale"] = 1.0
    return archive


def archive_with_payload(payload: bytes) -> reader.FArchiveReader:
    archive = reader.FArchiveReader(payload)
    archive.file_version = classes.EUEFormatVersion.LatestVersion
    archive.metadata["scale"] = 1.0
    return archive


def binary_skeleton(
    bones: tuple[tuple[str, int], ...],
    skeleton_path: str = "/Game/Rigs/SK_Test.SK_Test",
) -> bytes:
    records = b"".join(fstring(name) + struct.pack("<i7f", parent, *(0.0,) * 6, 1.0) for name, parent in bones)
    return chunk("METADATA", 1, fstring(skeleton_path)) + chunk("BONES", len(bones), records)


def binary_skeleton_with_child_byte_size(byte_size: int) -> bytes:
    child = fstring("BONES") + struct.pack("<ii", 0, byte_size)
    return child


def binary_lod_with_weights(weights: tuple[tuple[int, int, float], ...]) -> bytes:
    base = binary_lod("LOD0")
    weight_payload = b"".join(struct.pack("<hif", bone, vertex, value) for bone, vertex, value in weights)
    lod_name_size = len(fstring("LOD0"))
    payload = base[lod_name_size + 4:] + chunk("WEIGHTS", len(weights), weight_payload)
    return fstring("LOD0") + struct.pack("<i", len(payload)) + payload


def binary_identity(
    bones: tuple[tuple[str, int], ...],
    skeleton_path: str = "/Game/Rigs/SK_Test.SK_Test",
) -> bytes:
    return b"\x01" + fstring(skeleton_path) + b"\x00" + classes.compute_skeleton_layout_hash(bones)


def binary_collision(name: str = "COL0") -> bytes:
    return fstring(name) + struct.pack("<ii", 0, 0)


def lod(name: str, *, indices=(0, 1, 2), slots=(0,)):
    return classes.UEModelLOD(
        name=name,
        vertices=np.zeros((3, 3), dtype=np.float32),
        indices=np.asarray(indices, dtype=np.int32).reshape(-1, 3),
        materials=[classes.Material(f"M{slot}", f"/Game/M{slot}.M{slot}", 0, 1, slot) for slot in slots],
    )


def skeleton(bones=(('root', -1), ('spine', 0))):
    return classes.UEModelSkeleton(
        bones=[classes.Bone(name, parent, [0, 0, 0], (0, 0, 0, 1)) for name, parent in bones],
    )


def identity(bones):
    return classes.FModelSkeletonIdentity(1, "/Game/Rigs/SK_Test.SK_Test", None, classes.compute_skeleton_layout_hash(bones))


def test_standard_uemodel_topology_summary_is_deterministic_and_complete():
    bones = [('root', -1), ('spine', 0)]
    model = classes.UEModel(lods=[lod("LOD0"), lod("LOD1")], skeleton=skeleton(bones), skeleton_identity=identity(bones))

    summary = model.topology_summary()

    assert [(entry.lod_index, entry.vertex_count, entry.index_count, entry.material_slots) for entry in summary.lods] == [
        (0, 3, 3, (0,)),
        (1, 3, 3, (0,)),
    ]
    assert summary.skeleton.bone_count == 2
    assert summary.skeleton.bone_layout_hash == model.skeleton_identity.bone_layout_hash


def test_standard_binary_uemodel_topology_chunks_validate_after_import():
    bones = (("root", -1), ("spine", 0))
    payload = b"".join((
        chunk("LODS", 2, binary_lod("LOD0") + binary_lod("LOD1")),
        chunk("SKELETON", 1, binary_skeleton(bones)),
        chunk("FMODEL_SKELETON_IDENTITY", 1, binary_identity(bones)),
    ))
    archive = reader.FArchiveReader(payload)
    archive.file_version = classes.EUEFormatVersion.LatestVersion
    archive.metadata["scale"] = 1.0

    model = classes.UEModel.from_archive(archive)
    summary = model.topology_summary()

    assert [(entry.lod_index, entry.vertex_count, entry.index_count) for entry in summary.lods] == [(0, 3, 3), (1, 3, 3)]
    assert summary.skeleton is not None and summary.skeleton.bone_count == 2


def test_standard_skeleton_derives_rooted_identity_when_extension_is_absent():
    bones = (("root", -1), ("spine", 0))
    payload = b"".join((
        chunk("LODS", 1, binary_lod("LOD0")),
        chunk(
            "SKELETON",
            1,
            binary_skeleton(
                bones,
                "CodeWorld/Content/Characters/Hero/SK_Hero.SK_Hero",
            ),
        ),
    ))

    model = classes.UEModel.from_archive(archive_with_payload(payload))

    assert model.skeleton_identity is not None
    assert model.skeleton_identity_derived is True
    assert model.skeleton_identity.skeleton_path == "/CodeWorld/Content/Characters/Hero/SK_Hero.SK_Hero"
    assert model.skeleton_identity.skeleton_guid is None
    assert model.skeleton_identity.bone_layout_hash == classes.compute_skeleton_layout_hash(bones)


def test_explicit_skeleton_identity_is_not_marked_as_derived():
    bones = (("root", -1),)
    payload = b"".join((
        chunk("LODS", 1, binary_lod("LOD0")),
        chunk("SKELETON", 1, binary_skeleton(bones)),
        chunk("FMODEL_SKELETON_IDENTITY", 1, binary_identity(bones)),
    ))

    model = classes.UEModel.from_archive(archive_with_payload(payload))

    assert model.skeleton_identity is not None
    assert model.skeleton_identity_derived is False


def test_explicit_identity_matches_project_relative_standard_skeleton_metadata():
    bones = (("root", -1),)
    payload = b"".join((
        chunk("LODS", 1, binary_lod("LOD0")),
        chunk(
            "SKELETON",
            1,
            binary_skeleton(
                bones,
                "CodeWorld/Content/Characters/Hero/SK_Hero.SK_Hero",
            ),
        ),
        chunk(
            "FMODEL_SKELETON_IDENTITY",
            1,
            binary_identity(
                bones,
                "/CodeWorld/Content/Characters/Hero/SK_Hero.SK_Hero",
            ),
        ),
    ))

    model = classes.UEModel.from_archive(archive_with_payload(payload))

    assert model.skeleton is not None
    assert model.skeleton.metadata is not None
    assert model.skeleton.metadata.skeleton_path == "/CodeWorld/Content/Characters/Hero/SK_Hero.SK_Hero"
    assert model.skeleton_identity is not None
    model.topology_summary()


def test_explicit_identity_rejects_a_real_skeleton_path_mismatch():
    bones = (("root", -1),)
    payload = b"".join((
        chunk("LODS", 1, binary_lod("LOD0")),
        chunk(
            "SKELETON",
            1,
            binary_skeleton(
                bones,
                "CodeWorld/Content/Characters/Hero/SK_Hero.SK_Hero",
            ),
        ),
        chunk("FMODEL_SKELETON_IDENTITY", 1, binary_identity(bones)),
    ))

    model = classes.UEModel.from_archive(archive_with_payload(payload))

    with pytest.raises(ValueError, match="Skeleton identity path must match"):
        model.topology_summary()


def test_standard_binary_uemodel_reads_three_float_tangents_without_trailing_bytes():
    archive = archive_with_lod(binary_lod_with_tangents())

    model = classes.UEModel.from_archive(archive)

    assert [lod.name for lod in model.lods] == ["LOD0"]


@pytest.mark.parametrize("child_name", ["INDICES", "MATERIALS"])
def test_standard_binary_uemodel_rejects_duplicate_known_lod_child(child_name):
    lod_payload = binary_lod("LOD0")
    name_size = len(fstring("LOD0"))
    payload = lod_payload[name_size + 4:]
    duplicate = next(part for part in (chunk(child_name, 3, struct.pack("<3i", 0, 1, 2)),) if part)
    malformed = fstring("LOD0") + struct.pack("<i", len(payload + duplicate)) + payload + duplicate
    with pytest.raises(ValueError, match="duplicate LOD child"):
        classes.UEModel.from_archive(archive_with_lod(malformed))


def test_standard_binary_uemodel_rejects_duplicate_skeleton_metadata_and_bones():
    bones = binary_skeleton((("root", -1),))
    with pytest.raises(ValueError, match="duplicate skeleton child: METADATA"):
        classes.UEModel.from_archive(archive_with_payload(chunk("SKELETON", 1, bones + chunk("METADATA", 1, fstring("/Game/Rigs/SK_Test.SK_Test")))))
    with pytest.raises(ValueError, match="duplicate skeleton child: BONES"):
        classes.UEModel.from_archive(archive_with_payload(chunk("SKELETON", 1, bones + chunk("BONES", 1, b""))))


def test_standard_binary_uemodel_rejects_missing_skeleton_metadata_and_non_singleton_top_level_count():
    bones = binary_skeleton((("root", -1),))
    missing_metadata = bones[bones.find(fstring("BONES")):]
    with pytest.raises(ValueError, match="requires exactly one METADATA"):
        classes.UEModel.from_archive(archive_with_payload(chunk("SKELETON", 1, missing_metadata)))
    with pytest.raises(ValueError, match="SKELETON chunk array_size must be 1"):
        classes.UEModel.from_archive(archive_with_payload(chunk("SKELETON", 2, bones)))


@pytest.mark.parametrize(
    "section_name,payload",
    [
        ("LODS", binary_lod("LOD0")),
        ("SKELETON", binary_skeleton((("root", -1),))),
        ("COLLISION", binary_collision()),
    ],
)
def test_standard_binary_uemodel_rejects_duplicate_known_top_level_chunks(section_name, payload):
    with pytest.raises(ValueError, match=f"duplicate top-level chunk: {section_name}"):
        classes.UEModel.from_archive(
            archive_with_payload(chunk(section_name, 1, payload) + chunk(section_name, 1, payload)),
        )


@pytest.mark.parametrize("metadata_count", [0, 2])
def test_standard_binary_skeleton_requires_metadata_array_size_exactly_one(metadata_count):
    bones = binary_skeleton((("root", -1),))
    metadata = chunk("METADATA", metadata_count, fstring("/Game/Rigs/SK_Test.SK_Test"))
    skeleton_payload = metadata + bones[bones.find(fstring("BONES")):]
    with pytest.raises(ValueError, match="METADATA child array_size must be 1"):
        classes.UEModel.from_archive(archive_with_payload(chunk("SKELETON", 1, skeleton_payload)))


def test_standard_binary_skeleton_rejects_empty_bones_array():
    metadata = chunk("METADATA", 1, fstring("/Game/Rigs/SK_Test.SK_Test"))
    with pytest.raises(ValueError, match="BONES child array_size must be greater than 0"):
        classes.UEModel.from_archive(archive_with_payload(chunk("SKELETON", 1, metadata + chunk("BONES", 0, b""))))


def test_unsigned_weight_bone_index_preserves_32768():
    archive = reader.FArchiveReader(struct.pack("<Hif", 0x8000, 7, 0.5))
    weight = classes.Weight.from_archive(archive)
    assert weight.bone_index == 32768
    assert weight.vertex_index == 7


def test_standard_uemodel_topology_rejects_weights_without_skeleton_and_out_of_range_indices():
    with pytest.raises(ValueError, match="Weights require SKELETON"):
        classes.UEModel(lods=[classes.UEModelLOD(name="LOD0", vertices=np.zeros((3, 3)), indices=np.array([[0, 1, 2]]), materials=[classes.Material("M0", "/Game/M0.M0", 0, 1, 0)], weights=[classes.Weight(0, 0, 1.0)])]).topology_summary()
    model = classes.UEModel(lods=[lod("LOD0")], skeleton=skeleton((("root", -1),)))
    model.lods[0].weights = [classes.Weight(1, 0, 1.0)]
    with pytest.raises(ValueError, match="Weight bone_index"):
        model.topology_summary()
    model.lods[0].weights = [classes.Weight(0, 3, 1.0)]
    with pytest.raises(ValueError, match="Weight vertex_index"):
        model.topology_summary()
    model.lods[0].weights = [classes.Weight(0, 2, 1.0)]
    model.topology_summary()


@pytest.mark.parametrize("child_name", ["INDICES", "MATERIALS"])
def test_standard_binary_uemodel_rejects_negative_lod_child_array_size(child_name):
    with pytest.raises(ValueError, match="negative LOD child array_size"):
        classes.UEModel.from_archive(archive_with_lod(binary_lod_with_negative_child_count(child_name)))


def test_standard_binary_uemodel_rejects_lod_child_payload_crossing_declared_boundary():
    with pytest.raises(ValueError):
        classes.UEModel.from_archive(archive_with_lod(binary_lod_with_short_child_byte_size()))


@pytest.mark.parametrize("byte_size", [-1, 1])
def test_standard_binary_uemodel_rejects_invalid_skeleton_child_byte_size(byte_size):
    malformed_skeleton = binary_skeleton_with_child_byte_size(byte_size)
    archive = reader.FArchiveReader(chunk("SKELETON", 1, malformed_skeleton))
    archive.file_version = classes.EUEFormatVersion.LatestVersion
    archive.metadata["scale"] = 1.0

    with pytest.raises(ValueError):
        classes.UEModel.from_archive(archive)


def test_standard_binary_skeleton_rejects_declared_bone_count_mismatch():
    one_bone = binary_skeleton((("root", -1),))
    bones_offset = one_bone.find(fstring("BONES"))
    bones_count_offset = bones_offset + len(fstring("BONES"))
    malformed_skeleton = one_bone[:bones_count_offset] + struct.pack("<i", 2) + one_bone[bones_count_offset + 4:]
    malformed = chunk("SKELETON", 1, malformed_skeleton)
    archive = reader.FArchiveReader(malformed)
    archive.file_version = classes.EUEFormatVersion.LatestVersion
    archive.metadata["scale"] = 1.0

    with pytest.raises(ValueError, match="exceeds available data"):
        classes.UEModel.from_archive(archive)


@pytest.mark.parametrize(
    ("lods", "message"),
    [
        ([lod("LOD0"), lod("LOD0")], "LOD indices must be unique"),
        ([lod("LOD1"), lod("LOD0")], "LOD order must be deterministic"),
        ([lod("LOD0", indices=(0, 1, 3))], "Indices must be within"),
        ([classes.UEModelLOD(name="LOD0")], "missing required VERTICES"),
        ([lod("LOD0", slots=(0,)), lod("LOD1", slots=(1,))], "Material slots must match"),
    ],
)
def test_standard_uemodel_topology_rejects_invalid_lods(lods, message):
    with pytest.raises(ValueError, match=message):
        classes.UEModel(lods=lods).topology_summary()


@pytest.mark.parametrize(
    ("bones", "message"),
    [
        ((('root', 0),), "parents must be -1 or reference an earlier bone"),
        ((('root', -1), ('root', 0)), "bone names must be unique"),
    ],
)
def test_standard_uemodel_topology_rejects_invalid_skeleton_structure(bones, message):
    with pytest.raises(ValueError, match=message):
        classes.UEModel(skeleton=skeleton(bones)).topology_summary()


def test_standard_uemodel_topology_rejects_skeleton_identity_hash_mismatch():
    bones = [('root', -1)]
    invalid = classes.FModelSkeletonIdentity(1, "/Game/Rigs/SK_Test.SK_Test", None, b"x" * 32)
    with pytest.raises(ValueError, match="bone layout hash must match"):
        classes.UEModel(skeleton=skeleton(bones), skeleton_identity=invalid).topology_summary()


def test_standard_uemodel_topology_rejects_identity_without_matching_skeleton():
    bones = [('root', -1)]
    with pytest.raises(ValueError, match="requires SKELETON"):
        classes.UEModel(skeleton_identity=identity(bones)).topology_summary()
