from __future__ import annotations

import struct

import numpy as np
import pytest

from io_scene_ueformat.importer.archive.reader import FArchiveReader
from io_scene_ueformat.importer.deserialize import read_anim, read_model, read_pose


def fstring(value: str) -> bytes:
    raw = value.encode("utf-8")
    return struct.pack("<i", len(raw)) + raw


def attributes(*entries: tuple[str, bytes]) -> bytes:
    return struct.pack("<i", len(entries)) + b"".join(
        fstring(name) + struct.pack("<i", len(payload)) + payload
        for name, payload in entries
    )


def array(*items: bytes) -> bytes:
    return struct.pack("<i", len(items)) + b"".join(items)


def vector_key(frame: int, x: float, y: float, z: float) -> bytes:
    return struct.pack("<i3f", frame, x, y, z)


def quat_key(frame: int, x: float, y: float, z: float, w: float) -> bytes:
    return struct.pack("<i4f", frame, x, y, z, w)


def float_key(frame: int, value: float) -> bytes:
    return struct.pack("<if", frame, value)


def make_model_body(
    *,
    weight_bone_index: int = 0,
    weight_vertex_index: int = 0,
    bone_parent_index: int = -1,
    morph_vertex_index: int | None = None,
) -> bytes:
    morphs = b""
    if morph_vertex_index is not None:
        morph_delta = struct.pack(
            "<3f3fi",
            0.1,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            morph_vertex_index,
        )
        morphs = ("MORPHTARGETS", array(fstring("Smile") + array(morph_delta)))
    lod = attributes(
        ("VERTICES", struct.pack("<i3f", 1, 1.0, 2.0, 3.0)),
        ("NORMALS", struct.pack("<i4f", 1, 1.0, 0.0, 0.0, 1.0)),
        ("TEXCOORDS", struct.pack("<i", 1) + fstring("UV0") + struct.pack("<i2f", 1, 0.25, 0.75)),
        ("INDICES", struct.pack("<i3I", 3, 0, 0, 0)),
        ("MATERIALS", array(fstring("Body") + fstring("/Game/Materials/M_Body") + struct.pack("<ii", 0, 1))),
        ("WEIGHTS", array(struct.pack("<Hif", weight_bone_index, weight_vertex_index, 1.0))),
        *((morphs,) if morphs else ()),
        ("FUTURE_LOD_ATTRIBUTE", b"ignored"),
    )
    lods = struct.pack("<i", 1) + fstring("LOD0") + lod

    bone = (
        fstring("root")
        + struct.pack("<i", bone_parent_index)
        + struct.pack("<3f", 0.0, 0.0, 0.0)
        + struct.pack("<4f", 0.0, 0.0, 0.0, 1.0)
        + struct.pack("<3f", 1.0, 1.0, 1.0)
    )
    socket = (
        fstring("hand_socket")
        + fstring("root")
        + struct.pack("<3f", 1.0, 0.0, 0.0)
        + struct.pack("<4f", 0.0, 0.0, 0.0, 1.0)
        + struct.pack("<3f", 1.0, 1.0, 1.0)
    )
    virtual_bone = fstring("root") + fstring("root") + fstring("virtual_root")
    skeleton = attributes(
        ("METADATA", fstring("/Game/Characters/SKEL_Base")),
        ("BONES", array(bone)),
        ("SOCKETS", array(socket)),
        ("VIRTUALBONES", array(virtual_bone)),
    )

    collision = array(
        fstring("Body")
        + struct.pack("<i3f", 1, 0.0, 0.0, 0.0)
        + struct.pack("<i3I", 3, 0, 0, 0),
    )
    return attributes(
        ("LODS", lods),
        ("SKELETON", skeleton),
        ("COLLISION", collision),
        ("FUTURE_MODEL_ATTRIBUTE", b"ignored"),
    )


def make_anim_body() -> bytes:
    metadata = (
        struct.pack("<if", 30, 30.0)
        + fstring("/Game/Animations/RefPose")
        + bytes((0, 0))
        + struct.pack("<i", 0)
    )
    track = (
        fstring("root")
        + array(vector_key(0, 0.0, 0.0, 0.0))
        + array(quat_key(0, 0.0, 0.0, 0.0, 1.0))
        + array(vector_key(0, 1.0, 1.0, 1.0))
    )
    curve = fstring("Smile") + array(float_key(0, 0.5))
    return attributes(
        ("METADATA", metadata),
        ("TRACKS", array(track)),
        ("CURVES", array(curve)),
        ("FUTURE_ANIM_ATTRIBUTE", b"ignored"),
    )


def make_pose_body(*, curve_index: int = 0) -> bytes:
    pose_key = (
        fstring("root")
        + struct.pack("<3f", 0.0, 0.0, 0.0)
        + struct.pack("<4f", 0.0, 0.0, 0.0, 1.0)
        + struct.pack("<3f", 1.0, 1.0, 1.0)
    )
    pose_entry = fstring("Neutral") + array(pose_key) + array(struct.pack("<if", curve_index, 1.0))
    return attributes(
        ("POSES", array(pose_entry)),
        ("CURVES", array(fstring("Smile"))),
        ("FUTURE_POSE_ATTRIBUTE", b"ignored"),
    )


def test_v10_model_deserializer_reads_standard_nested_attributes() -> None:
    data = read_model(FArchiveReader(make_model_body()))

    assert len(data.lods) == 1
    lod = data.lods[0]
    assert lod.name == "LOD0"
    np.testing.assert_allclose(lod.vertices, [[1.0, 2.0, 3.0]])
    np.testing.assert_allclose(lod.normals, [[0.0, 0.0, 1.0]])
    np.testing.assert_allclose(lod.uvs[0], [[0.25, 0.75]])
    assert lod.indices.tolist() == [[0, 0, 0]]
    assert lod.materials[0].material_path == "/Game/Materials/M_Body"
    assert lod.weights[0].bone_index == 0
    assert data.skeleton is not None
    assert data.skeleton.skeleton_path == "/Game/Characters/SKEL_Base"
    assert data.skeleton.bones[0].name == "root"
    assert data.skeleton.sockets[0].parent_name == "root"
    assert data.skeleton.virtual_bones[0].virtual_name == "virtual_root"
    assert data.collisions[0].name == "Body"


def test_v10_animation_deserializer_reads_metadata_tracks_and_curves() -> None:
    data = read_anim(FArchiveReader(make_anim_body()))

    assert data.metadata is not None
    assert data.metadata.num_frames == 30
    assert data.metadata.frames_per_second == pytest.approx(30.0)
    assert data.metadata.ref_pose_path == "/Game/Animations/RefPose"
    assert data.tracks[0].name == "root"
    np.testing.assert_allclose(data.tracks[0].rotation_keys[0].value, [0.0, 0.0, 0.0, 1.0])
    assert data.curves[0].name == "Smile"
    assert data.curves[0].keys[0].value == pytest.approx(0.5)


def test_v10_pose_deserializer_reads_pose_keys_and_curve_influences() -> None:
    data = read_pose(FArchiveReader(make_pose_body()))

    assert data.curve_names == ["Smile"]
    assert data.poses[0].name == "Neutral"
    assert data.poses[0].keys[0].bone_name == "root"
    assert data.poses[0].curves[0].curve_index == 0
    assert data.poses[0].curves[0].influence == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"weight_bone_index": 1}, "weight bone index"),
        ({"weight_vertex_index": 1}, "weight vertex index"),
        ({"bone_parent_index": 1}, "bone parent index"),
        ({"morph_vertex_index": 1}, "Morph 0 vertex index"),
    ],
)
def test_v10_model_deserializer_rejects_cross_field_references(kwargs, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        read_model(FArchiveReader(make_model_body(**kwargs)))


def test_v10_pose_deserializer_rejects_curve_index_outside_curve_table() -> None:
    with pytest.raises(ValueError, match="pose curve index"):
        read_pose(FArchiveReader(make_pose_body(curve_index=1)))


def test_v10_standard_deserializer_rejects_attribute_trailing_bytes() -> None:
    with pytest.raises(ValueError, match="Trailing bytes in V10 LODS"):
        read_model(FArchiveReader(attributes(("LODS", struct.pack("<i", 0) + b"x"))))

    with pytest.raises(ValueError, match="Trailing bytes after V10 attribute envelope"):
        read_model(FArchiveReader(attributes(("LODS", struct.pack("<i", 0))) + b"x"))


def test_v10_skeleton_deserializer_rejects_nested_attribute_trailing_bytes() -> None:
    skeleton = attributes(("METADATA", fstring("/Game/Characters/SKEL_Base") + b"x"))

    with pytest.raises(ValueError, match="Trailing bytes in V10 METADATA attribute"):
        read_model(FArchiveReader(attributes(("SKELETON", skeleton))))


def test_v10_standard_deserializer_rejects_unbounded_attribute_count() -> None:
    with pytest.raises(ValueError, match="Invalid V10 attribute count"):
        read_model(FArchiveReader(struct.pack("<i", 4097)))


@pytest.mark.parametrize(
    ("reader", "attribute_name"),
    [(read_model, "LODS"), (read_anim, "TRACKS"), (read_pose, "POSES")],
)
def test_v10_standard_deserializers_reject_truncated_attribute_payloads(reader, attribute_name) -> None:
    malformed = attributes((attribute_name, struct.pack("<i", 1)))
    with pytest.raises(ValueError):
        reader(FArchiveReader(malformed))
