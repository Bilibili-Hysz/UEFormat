from __future__ import annotations

import importlib
import json
import struct
import sys
import types
import zlib
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).parents[1] / "plugins" / "blender" / "io_scene_ueformat"

def _module():
    package = types.ModuleType("io_scene_ueformat"); package.__path__ = [str(PACKAGE_ROOT)]
    sys.modules.setdefault("io_scene_ueformat", package)
    return importlib.import_module("io_scene_ueformat.importer.uescene")

def _strings(values: list[bytes]) -> bytes:
    return struct.pack("<I", len(values)) + b"".join(struct.pack("<I", len(v)) + v for v in values)
def _meta(required: int = 0, optional_features: int = 0) -> bytes:
    return struct.pack("<I8IQ4BIQQ", 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 0, required, optional_features)
def _table(count: int, records: bytes = b"") -> bytes: return struct.pack("<I", count) + records
def _mato(*, flags: int = 1, base: int = 0, override: int = 0, payload: bytes = b"\x01\x00\x00\x00\x00\x00\x00\x00") -> bytes:
    return struct.pack("<II", 1, len(payload)) + struct.pack("<QIIQQQIIII", 30, 7, flags, base, override, 0, len(payload), 0, 0, 0) + payload
def _inst() -> bytes:
    transforms = struct.pack("<3f4f3f", 7, 8, 9, 0, 0, 0, 1, 2, 3, 4)
    return struct.pack("<II", 1, 1) + struct.pack("<QQIIII", 40, 30, 1, 0, 0, 1) + transforms
def _component(ident: int, *, parent: int = 0, kind: int = 1, instance_set: int = 0) -> bytes:
    return struct.pack("<QQQB3xIIIQQIIQIIII", ident, 20, parent, kind, 4, 10, 11, 101, 10, 0, 0, instance_set, 0, 0, 0, 0)
def _light(ident: int, light_type: int, flags: int, *, parent: int = 0, **values: float) -> bytes:
    fields = {"intensity": 1.0, "attenuation_radius": 0.0, "falloff_exponent": 0.0, "inner": 0.0, "outer": 0.0, "width": 0.0, "height": 0.0, "source_angle": 0.0, "source_soft_angle": 0.0, "source_radius": 0.0, "soft_source_radius": 0.0, "source_length": 0.0, "barn_door_angle": 0.0, "barn_door_length": 0.0}
    fields.update(values)
    return struct.pack("<QQQQIIIBBBB4sI7fI7f", ident, 20, parent, 100, 3, 8, 9, light_type, 1, 2, 0, bytes((255, 240, 200, 255)), flags, fields["intensity"], fields["attenuation_radius"], fields["falloff_exponent"], fields["inner"], fields["outer"], fields["width"], fields["height"], 0, fields["source_angle"], fields["source_soft_angle"], fields["source_radius"], fields["soft_source_radius"], fields["source_length"], fields["barn_door_angle"], fields["barn_door_length"])
def _lite(records: bytes) -> bytes:
    return _table(len(records) // 116, records)
def _scene(*, strs: bytes | None = None, chunks: dict[bytes, bytes] | None = None, optional_features: int = 0, lights: bytes | None = None) -> bytes:
    content = {
        b"META": _meta(optional_features=optional_features), b"STRS": strs or _strings([b"uescene", b"models/cube.uemodel", b"Actor", b"Component", b"/Game/Cube", b"Cube", b"model/uemodel", b"ActorClass", b"/Game/Actor", b"ComponentClass", b"/Game/Component"]),
        b"ASST": struct.pack("<II", 1, 1) + struct.pack("<QBBBBIIII32sQIIII", 10, 1, 2, 1, 0, 5, 6, 2, 7, sha256(b"model").digest(), 5, 0, 0, 0, 1) + struct.pack("<II32sQ6dfI", 0, 2, sha256(b"model").digest(), 5, 0, 0, 0, 1, 1, 1, float("nan"), 0),
        b"ACTR": _table(1, struct.pack("<QQIIIQQIIIII", 20, 0, 3, 8, 9, 30, 100, 0, 1, 0, 0, 0)),
        b"COMP": _table(1, struct.pack("<QQQB3xIIIQQIIQIIII", 30, 20, 0, 1, 4, 10, 11, 101, 10, 0, 0, 0, 0, 0, 0, 0)),
        b"XFRM": _table(2, struct.pack("<Q3d4f3fI", 100, 1, 2, 3, 0, 0, 0, 1, 1, 1, 1, 0) + struct.pack("<Q3d4f3fI", 101, 4, 5, 6, 0, 0, 0, 1, 1, 1, 1, 0)),
        b"MATO": struct.pack("<II", 0, 0), b"DIAG": struct.pack("<II", 0, 0),
        b"BNDX": _table(1, struct.pack("<BBBBIQI32sQIII", 1, 2, 1, 0, 2, 10, 0xffffffff, sha256(b"model").digest(), 5, 7, 0, 0)),
    }
    if lights is not None:
        content[b"LITE"] = _lite(lights)
    if chunks: content.update(chunks)
    ordered = list(content.items()); directory_size = 40 * len(ordered); offset = (44 + directory_size + 7) & ~7; entries = []; body = bytearray()
    for name, payload in ordered:
        offset = (offset + 7) & ~7
        body.extend(b"\0" * (offset - (44 + directory_size) - len(body)))
        entries.append((name, offset, payload)); body.extend(payload); offset += len(payload)
    size = 44 + directory_size + len(body); prefix = struct.pack("<8sHHIIQIQ", b"UESCENE\0", 1, 0, 0, 44, 44, len(entries), size)
    header = prefix + struct.pack("<I", zlib.crc32(prefix) & 0xffffffff)
    directory = b"".join(struct.pack("<4sHHQQQII", name, 1, 0, at, len(payload), len(payload), zlib.crc32(payload) & 0xffffffff, struct.unpack_from("<I", payload)[0]) for name, at, payload in entries)
    return header + directory + body

def _mutate_chunk(data: bytes, name: bytes, relative_offset: int, value: bytes) -> bytes:
    result = bytearray(data)
    directory_count = int.from_bytes(result[28:32], "little")
    for directory_offset in range(44, 44 + directory_count * 40, 40):
        if result[directory_offset:directory_offset + 4] != name:
            continue
        payload_offset = int.from_bytes(result[directory_offset + 8:directory_offset + 16], "little")
        payload_length = int.from_bytes(result[directory_offset + 16:directory_offset + 24], "little")
        result[payload_offset + relative_offset:payload_offset + relative_offset + len(value)] = value
        result[directory_offset + 32:directory_offset + 36] = zlib.crc32(result[payload_offset:payload_offset + payload_length]).to_bytes(4, "little")
        return bytes(result)
    raise AssertionError(f"missing {name!r} chunk")

def test_parses_complete_v1_without_blender_import():
    bpy = types.ModuleType("bpy"); sys.modules["bpy"] = bpy
    scene = _module().UEScene.from_bytes(_scene())
    assert scene.metadata.format_name == "uescene"
    assert scene.actors[0].id == 20
    assert scene.components[0].transform_id == 101


def test_retains_all_lite_types_in_pure_import_plan():
    lights = b"".join((
        _light(40, 1, 1, source_angle=0.5, source_soft_angle=0.25),
        _light(41, 2, 2 | 16, attenuation_radius=100),
        _light(42, 3, 2 | 4, attenuation_radius=500, falloff_exponent=8, inner=10, outer=30, source_radius=4, soft_source_radius=2, source_length=6),
        _light(43, 4, 8, width=4, height=2, barn_door_angle=45, barn_door_length=5),
    ))

    scene = _module().UEScene.from_bytes(_scene(optional_features=2, lights=lights))
    plan = scene.build_import_plan(Path("level.uescene"), None)

    assert [light.light_type for light in scene.lights] == [1, 2, 3, 4]
    assert [light.id for light in plan.lights] == [40, 41, 42, 43]
    assert plan.lights[0].light_source_angle == 0.5
    assert plan.lights[1].attenuation_radius == 100
    assert plan.lights[2].outer_cone_angle_degrees == 30
    assert plan.lights[3].barn_door_length == 5


def test_accepts_rect_attenuation_and_raw_inverse_square_falloff_exponent():
    lights = b"".join((
        _light(40, 2, 2 | 16, attenuation_radius=100, falloff_exponent=8),
        _light(41, 3, 2 | 4 | 16, attenuation_radius=500, falloff_exponent=8, inner=10, outer=30),
        _light(42, 4, 2 | 8, attenuation_radius=250, width=4, height=2),
    ))

    scene = _module().UEScene.from_bytes(_scene(optional_features=2, lights=lights))

    assert [light.falloff_exponent for light in scene.lights[:2]] == [8, 8]
    assert scene.lights[2].flags & _module().LightFlags.HAS_ATTENUATION


@pytest.mark.parametrize("optional_features, lights, match", [
    (2, None, "LITE feature"),
    (0, _light(40, 1, 0), "LITE feature"),
    (2, b"", "nonempty"),
    (2, _light(40, 2, 0), "flags/type"),
    (2, _light(40, 1, 0, source_angle=-0.0), "negative zero"),
])
def test_rejects_lite_feature_mismatch_and_corruption(optional_features: int, lights: bytes | None, match: str):
    with pytest.raises(_module().UESceneError, match=match):
        _module().UEScene.from_bytes(_scene(optional_features=optional_features, lights=lights))


@pytest.mark.parametrize("chunk, offset, value", [
    (b"XFRM", 4 + 8, struct.pack("<Q", 0x8000000000000000)),
    (b"ASST", 8 + 84 + 48, struct.pack("<Q", 0x8000000000000000)),
    (b"INST", 8 + 32, struct.pack("<I", 0x80000000)),
])
def test_rejects_negative_zero_outside_lite(chunk: bytes, offset: int, value: bytes):
    chunks = {b"META": _meta(1), b"COMP": _table(1, _component(30, kind=2, instance_set=40)), b"INST": _inst()} if chunk == b"INST" else None
    with pytest.raises(_module().UESceneError, match="negative zero"):
        _module().UEScene.from_bytes(_mutate_chunk(_scene(chunks=chunks), chunk, offset, value))


@pytest.mark.parametrize("optional_features, chunks", [
    (4, None),
    (0, {b"CAMR": struct.pack("<I", 0)}),
    (4, {b"CAMR": struct.pack("<I", 0)}),
])
def test_rejects_retired_camera_declarations(optional_features: int, chunks: dict[bytes, bytes] | None):
    with pytest.raises(_module().UESceneError, match="CAMR|camera"):
        _module().UEScene.from_bytes(_scene(optional_features=optional_features, chunks=chunks))

@pytest.mark.parametrize("mutate", ["magic", "header_crc", "range", "chunk_crc"])
def test_rejects_corrupt_framing_and_crc(mutate: str):
    data = bytearray(_scene())
    if mutate == "magic": data[0] = 0
    elif mutate == "header_crc": data[40] ^= 1
    elif mutate == "range": data[44 + 8:44 + 16] = (44).to_bytes(8, "little")
    else: data[44 + 32:44 + 36] = (0).to_bytes(4, "little")
    with pytest.raises(_module().UESceneError): _module().UEScene.from_bytes(data)

def test_rejects_utf8_and_missing_required_chunk():
    with pytest.raises(_module().UESceneError): _module().UEScene.from_bytes(_scene(strs=_strings([b"\xff"])))
    data = bytearray(_scene()); data[44:48] = b"FUTR"
    with pytest.raises(_module().UESceneError, match="missing required"): _module().UEScene.from_bytes(data)

def test_rejects_utf8_bom_prefix_in_strings():
    with pytest.raises(_module().UESceneError, match="UTF-8 BOM"):
        _module().UEScene.from_bytes(_scene(strs=_strings([b"\xef\xbb\xbfuescene"])))

def test_rejects_component_ownership_and_bad_reference():
    data = bytearray(_scene()); comp_offset = int.from_bytes(data[44 + 4 * 40 + 8:44 + 4 * 40 + 16], "little")
    data[comp_offset + 12:comp_offset + 20] = (999).to_bytes(8, "little")
    data[44 + 4 * 40 + 32:44 + 4 * 40 + 36] = zlib.crc32(data[comp_offset:comp_offset + 92]).to_bytes(4, "little")
    with pytest.raises(_module().UESceneError, match="ownership"): _module().UEScene.from_bytes(data)

def test_plan_reports_missing_required_model_as_structured_partial_outcome(tmp_path: Path):
    model = tmp_path / "models" / "cube.uemodel"; model.parent.mkdir(); model.write_bytes(b"model")
    scene = _module().UEScene.from_bytes(_scene())
    plan = scene.build_import_plan(tmp_path / "level.uescene", tmp_path)
    assert plan.components[0].model_path == model
    model.unlink()
    plan = scene.build_import_plan(tmp_path / "level.uescene", tmp_path, strict_required_models=False)
    assert plan.components[0].model_path is None
    assert plan.outcome == "partial"
    assert [(failure.component_id, failure.code) for failure in plan.required_model_failures] == [
        (30, "missing"),
    ]
    assert "unavailable" in plan.warnings[0]


def test_plan_rejects_missing_required_model_in_strict_mode(tmp_path: Path):
    scene = _module().UEScene.from_bytes(_scene())

    with pytest.raises(_module().RequiredModelError) as caught:
        scene.build_import_plan(tmp_path / "level.uescene", tmp_path, strict_required_models=True)

    assert caught.value.failures[0].component_id == 30
    assert caught.value.failures[0].code == "missing"


def test_plan_distinguishes_required_model_hash_mismatch(tmp_path: Path):
    model = tmp_path / "models" / "cube.uemodel"
    model.parent.mkdir()
    model.write_bytes(b"wrong")
    scene = _module().UEScene.from_bytes(_scene())

    plan = scene.build_import_plan(tmp_path / "level.uescene", tmp_path, strict_required_models=False)

    assert plan.required_model_failures[0].code == "hash_mismatch"


def test_infers_bundle_root_from_standard_scenes_layout(tmp_path: Path):
    scene_path = tmp_path / "bundle" / "Scenes" / "Map.uescene"
    assert _module().infer_bundle_root(scene_path, "", 0) == scene_path.parent.parent
    assert _module().infer_bundle_root(scene_path, str(tmp_path / "explicit"), 1) == tmp_path / "explicit"
    assert _module().infer_bundle_root(tmp_path / "Map.uescene", "", 0) is None


def test_infers_bundle_root_from_self_contained_uescenes_layout(tmp_path: Path):
    scene_path = tmp_path / "Exports" / "Game" / "Uescenes" / "Content" / "Maps" / "City" / "City.uescene"

    assert _module().infer_bundle_root(scene_path, "", 0) == scene_path.parent


def test_infers_game_export_root_from_optional_metadata_bit(tmp_path: Path):
    root = tmp_path / "Exports" / "Game"
    scene_path = root / "Content" / "Maps" / "City" / "City.umap.uescene"
    model = root / "Content" / "Meshes" / "Chair.uemodel"
    model.parent.mkdir(parents=True); model.write_bytes(b"model")
    scene_path.parent.mkdir(parents=True)
    scene = _module().UEScene.from_bytes(_scene(
        strs=_strings([b"uescene", b"Content/Meshes/Chair.uemodel", b"Actor", b"Component", b"/Game/Cube", b"Cube", b"model/uemodel", b"ActorClass", b"/Game/Actor", b"ComponentClass", b"/Game/Component"]),
        optional_features=1,
    ))

    bundle_root = _module().infer_bundle_root(scene_path, "", scene.metadata.optional_features)

    assert bundle_root == root
    assert scene.build_import_plan(scene_path, bundle_root).components[0].model_path == model
    assert _module().infer_bundle_root(scene_path, str(tmp_path / "explicit"), scene.metadata.optional_features) == tmp_path / "explicit"


def test_game_export_root_bit_requires_valid_content_ancestor(tmp_path: Path):
    invalid = tmp_path / "Exports" / "Game" / "Maps" / "City" / "City.umap.uescene"

    assert _module().infer_bundle_root(invalid, "", 1) is None


def test_plan_retains_supplied_bundle_root_for_primary_model_material_links(tmp_path: Path):
    bundle_root = tmp_path / "bundle"
    model = bundle_root / "Models" / "M-Hero.uemodel"
    material = bundle_root / "Materials" / "MI-Hero.json"
    texture = bundle_root / "Textures" / "T-Hero.png"
    for path in (model, material, texture):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"model" if path == model else b"resource")

    scene = _module().UEScene.from_bytes(_scene(strs=_strings([
        b"uescene", b"Models/M-Hero.uemodel", b"Actor", b"Component", b"/Game/Cube", b"Cube",
        b"model/uemodel", b"ActorClass", b"/Game/Actor", b"ComponentClass", b"/Game/Component",
    ])))
    plan = scene.build_import_plan(
        bundle_root / "level.uescene", bundle_root
    )

    assert plan.bundle_root == bundle_root
    assert plan.components[0].model_path == model
    assert material == plan.bundle_root / "Materials" / "MI-Hero.json"
    assert material.is_relative_to(plan.bundle_root)
    assert texture.is_relative_to(plan.bundle_root)


def test_plan_verifies_material_and_texture_sidecars_under_bundle_root(tmp_path: Path):
    bundle_root = tmp_path / "bundle"
    model = bundle_root / "Models" / "M-Hero.uemodel"
    material = bundle_root / "Materials" / "MI-Hero.json"
    texture = bundle_root / "Textures" / "T-Hero.png"
    model.parent.mkdir(parents=True)
    material.parent.mkdir()
    texture.parent.mkdir()
    model.write_bytes(b"model")
    material.write_bytes(b"material")
    texture.write_bytes(b"texture")
    strings = _strings([
        b"uescene", b"Models/M-Hero.uemodel", b"Actor", b"Component", b"/Game/Cube", b"Cube",
        b"model/uemodel", b"ActorClass", b"/Game/Actor", b"ComponentClass", b"/Game/Component",
        b"/Game/Materials/MI-Hero", b"MI-Hero", b"Materials/MI-Hero.json", b"application/json",
        b"/Game/Textures/T-Hero", b"T-Hero", b"Textures/T-Hero.png", b"image/png",
    ])
    model_hash, material_hash, texture_hash = (sha256(value).digest() for value in (b"model", b"material", b"texture"))
    assets = struct.pack("<II", 3, 1)
    assets += struct.pack("<QBBBBIIII32sQIIII", 10, 1, 2, 1, 0, 5, 6, 2, 7, model_hash, 5, 0, 0, 0, 1)
    assets += struct.pack("<QBBBBIIII32sQIIII", 11, 2, 2, 1, 0, 12, 13, 14, 15, material_hash, 8, 0, 0, 0, 0)
    assets += struct.pack("<QBBBBIIII32sQIIII", 12, 3, 2, 1, 0, 16, 17, 18, 19, texture_hash, 7, 0, 0, 0, 0)
    assets += struct.pack("<II32sQ6dfI", 0, 2, model_hash, 5, 0, 0, 0, 1, 1, 1, float("nan"), 0)
    bundle_entries = _table(3,
        struct.pack("<BBBBIQI32sQIII", 1, 2, 1, 0, 2, 10, 0xffffffff, model_hash, 5, 7, 0, 0)
        + struct.pack("<BBBBIQI32sQIII", 2, 2, 1, 0, 14, 11, 0xffffffff, material_hash, 8, 15, 0, 0)
        + struct.pack("<BBBBIQI32sQIII", 3, 2, 1, 0, 18, 12, 0xffffffff, texture_hash, 7, 19, 0, 0),
    )
    scene = _module().UEScene.from_bytes(_scene(strs=strings, chunks={b"ASST": assets, b"BNDX": bundle_entries}))

    assert scene.build_import_plan(bundle_root / "level.uescene", bundle_root).components[0].model_path == model
    material.write_bytes(b"tampered")
    with pytest.raises(_module().UESceneError, match="sidecar does not match BNDX"):
        scene.build_import_plan(bundle_root / "level.uescene", bundle_root)
    material.write_bytes(b"material")
    texture.write_bytes(b"tampered")
    with pytest.raises(_module().UESceneError, match="sidecar does not match BNDX"):
        scene.build_import_plan(bundle_root / "level.uescene", bundle_root)


def test_plan_rejects_tampered_unreferenced_available_mesh_sidecar(tmp_path: Path):
    bundle_root = tmp_path / "bundle"
    model = bundle_root / "Models" / "M-Unreferenced.uemodel"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"model")
    strings = _strings([
        b"uescene", b"Models/M-Hero.uemodel", b"Actor", b"Component", b"/Game/Cube", b"Cube",
        b"model/uemodel", b"ActorClass", b"/Game/Actor", b"ComponentClass", b"/Game/Component",
        b"/Game/Unused", b"Unused", b"Models/M-Unreferenced.uemodel",
    ])
    model_hash = sha256(b"model").digest()
    unused_hash = sha256(b"unused").digest()
    assets = struct.pack("<II", 2, 2)
    assets += struct.pack("<QBBBBIIII32sQIIII", 10, 1, 2, 1, 0, 5, 6, 2, 7, model_hash, 5, 0, 0, 0, 1)
    assets += struct.pack("<QBBBBIIII32sQIIII", 11, 1, 2, 1, 0, 12, 13, 14, 7, unused_hash, 6, 0, 0, 1, 1)
    assets += struct.pack("<II32sQ6dfI", 0, 2, model_hash, 5, 0, 0, 0, 1, 1, 1, float("nan"), 0)
    assets += struct.pack("<II32sQ6dfI", 0, 2, unused_hash, 6, 0, 0, 0, 1, 1, 1, float("nan"), 0)
    bundle_entries = _table(2,
        struct.pack("<BBBBIQI32sQIII", 1, 2, 1, 0, 2, 10, 0xffffffff, model_hash, 5, 7, 0, 0)
        + struct.pack("<BBBBIQI32sQIII", 1, 2, 1, 0, 14, 11, 0xffffffff, unused_hash, 6, 7, 0, 0),
    )
    scene = _module().UEScene.from_bytes(_scene(strs=strings, chunks={b"ASST": assets, b"BNDX": bundle_entries}))

    with pytest.raises(_module().UESceneError, match="sidecar does not match BNDX"):
        scene.build_import_plan(bundle_root / "level.uescene", bundle_root)


def test_windows_extended_filesystem_path_supports_long_bundle_sidecars(monkeypatch):
    module = _module()

    monkeypatch.setattr(module.os, "name", "nt")
    monkeypatch.setattr(module.os.path, "isabs", lambda value: True)

    assert module._filesystem_path(Path("C:/" + "segment/" * 40 + "texture.png")) == (
        "\\\\?\\C:" + "\\segment" * 40 + "\\texture.png"
    )
    assert module._filesystem_path(Path("//server/share/texture.png")) == (
        "\\\\?\\UNC\\server\\share\\texture.png"
    )

def test_rejects_per_lod_model_bndx_and_uses_primary_for_multilod(tmp_path: Path):
    model = tmp_path / "models" / "cube.uemodel"; model.parent.mkdir(); model.write_bytes(b"model")
    asset = struct.pack("<QBBBBIIII32sQIIII", 10, 1, 2, 1, 0, 5, 6, 2, 7, sha256(b"model").digest(), 5, 0, 0, 0, 2)
    lod0 = struct.pack("<II32sQ6dfI", 0, 2, sha256(b"lod0").digest(), 4, 0, 0, 0, 1, 1, 1, float("nan"), 0)
    lod1 = struct.pack("<II32sQ6dfI", 1, 2, sha256(b"lod1").digest(), 4, 0, 0, 0, 1, 1, 1, 0.5, 0)
    scene = _module().UEScene.from_bytes(_scene(chunks={b"ASST": struct.pack("<II", 1, 2) + asset + lod0 + lod1}))
    plan = scene.build_import_plan(tmp_path / "level.uescene", tmp_path)
    assert plan.components[0].model_path == model
    assert [lod.index for lod in plan.components[0].mesh_lods] == [0, 1]
    per_lod = struct.pack("<BBBBIQI32sQIII", 1, 2, 1, 0, 2, 10, 0, sha256(b"model").digest(), 5, 7, 0, 0)
    with pytest.raises(_module().UESceneError, match="BNDX"):
        _module().UEScene.from_bytes(_scene(chunks={b"BNDX": _table(1, per_lod)}))
    with pytest.raises(_module().UESceneError, match="BNDX"):
        _module().UEScene.from_bytes(_scene(chunks={b"BNDX": _table(0)}))

def test_plan_resolves_actor_transform(tmp_path: Path):
    plan = _module().UEScene.from_bytes(_scene()).build_import_plan(tmp_path / "level.uescene", tmp_path)
    transform = plan.actor_transform(plan.actors[0])
    assert transform.translation == (1.0, 2.0, 3.0)
    assert transform.rotation == (0.0, 0.0, 0.0, 1.0)
    assert transform.scale == (1.0, 1.0, 1.0)

def test_plan_rejects_escape_uri_as_warning(tmp_path: Path):
    scene = _module().UEScene.from_bytes(_scene())
    # Directly exercise URI policy: semantic strings make a BNDX URI safe in this fixture.
    with pytest.raises(_module().UESceneError): _module()._resolve_uri("../escape.uemodel", 2, tmp_path / "x.uescene", tmp_path)

@pytest.mark.parametrize("uri", ["bundle:models/cube.uemodel", "https://example.test/cube.uemodel"])
def test_rejects_colon_bearing_logical_uri(uri: str, tmp_path: Path):
    with pytest.raises(_module().UESceneError, match="unsafe model URI"):
        _module()._resolve_uri(uri, 2, tmp_path / "x.uescene", tmp_path)

@pytest.mark.parametrize("owner, availability", [(999, 1), (10, 2)])
def test_rejects_bndx_orphan_or_wrong_availability(owner: int, availability: int):
    entry = struct.pack("<BBBBIQI32sQIII", 1, 2, availability, 0, 2, owner, 0, sha256(b"model").digest(), 5, 7, 0, 0)
    with pytest.raises(_module().UESceneError, match="BNDX"):
        _module().UEScene.from_bytes(_scene(chunks={b"BNDX": _table(1, entry)}))

def test_allows_available_material_without_materialjson_bndx():
    material = struct.pack("<QBBBBIIII32sQIIII", 11, 2, 2, 1, 0, 12, 13, 14, 15, sha256(b"material").digest(), 8, 0, 0, 0, 0)
    scene = _module().UEScene.from_bytes(_scene(
            strs=_strings([b"uescene", b"models/cube.uemodel", b"Actor", b"Component", b"/Game/Cube", b"Cube", b"model/uemodel", b"ActorClass", b"/Game/Actor", b"ComponentClass", b"/Game/Component", b"materials/cube.material.json", b"/Game/Material", b"CubeMaterial", b"application/json"]),
            chunks={b"ASST": struct.pack("<II", 2, 1) + struct.pack("<QBBBBIIII32sQIIII", 10, 1, 2, 1, 0, 5, 6, 2, 7, sha256(b"model").digest(), 5, 0, 0, 0, 1) + material + struct.pack("<II32sQ6dfI", 0, 2, sha256(b"model").digest(), 5, 0, 0, 0, 1, 1, 1, float("nan"), 0)},
        ))
    assert scene.assets[1].kind == 2

@pytest.mark.parametrize("material_entries", [
    (struct.pack("<BBBBIQI32sQIII", 1, 2, 1, 0, 12, 11, 0xffffffff, sha256(b"material").digest(), 8, 15, 0, 0),),
    (struct.pack("<BBBBIQI32sQIII", 3, 2, 1, 0, 12, 11, 0xffffffff, sha256(b"material").digest(), 8, 15, 0, 0),) * 2,
])
def test_rejects_malformed_or_duplicate_materialjson_bndx(material_entries: tuple[bytes, ...]):
    material = struct.pack("<QBBBBIIII32sQIIII", 11, 2, 2, 1, 0, 12, 13, 14, 15, sha256(b"material").digest(), 8, 0, 0, 0, 0)
    primary = struct.pack("<BBBBIQI32sQIII", 1, 2, 1, 0, 2, 10, 0xffffffff, sha256(b"model").digest(), 5, 7, 0, 0)
    with pytest.raises(_module().UESceneError, match="BNDX"):
        _module().UEScene.from_bytes(_scene(
            strs=_strings([b"uescene", b"models/cube.uemodel", b"Actor", b"Component", b"/Game/Cube", b"Cube", b"model/uemodel", b"ActorClass", b"/Game/Actor", b"ComponentClass", b"/Game/Component", b"materials/cube.material.json", b"/Game/Material", b"CubeMaterial", b"application/json"]),
            chunks={b"ASST": struct.pack("<II", 2, 1) + struct.pack("<QBBBBIIII32sQIIII", 10, 1, 2, 1, 0, 5, 6, 2, 7, sha256(b"model").digest(), 5, 0, 0, 0, 1) + material + struct.pack("<II32sQ6dfI", 0, 2, sha256(b"model").digest(), 5, 0, 0, 0, 1, 1, 1, float("nan"), 0), b"BNDX": _table(1 + len(material_entries), primary + b"".join(material_entries))},
        ))

def test_plan_retains_mato_provenance_and_diagnostics():
    subject_id = int.from_bytes(sha256(b"UESceneV1\0mato\0" + struct.pack("<QI", 30, 7)).digest()[:8], "little")
    diagnostic = struct.pack("<BBHIQIIQII", 2, 5, 1, 0, subject_id, 0, 12, 0, 0, 0)
    scene = _module().UEScene.from_bytes(_scene(
        strs=_strings([b"uescene", b"models/cube.uemodel", b"Actor", b"Component", b"/Game/Cube", b"Cube", b"model/uemodel", b"ActorClass", b"/Game/Actor", b"ComponentClass", b"/Game/Component", b"override warning"]),
        chunks={
            b"META": _meta(2),
            b"COMP": _table(1, struct.pack("<QQQB3xIIIQQIIQIIII", 30, 20, 0, 1, 4, 10, 11, 101, 10, 0, 1, 0, 0, 0, 0, 0)),
            b"MATO": struct.pack("<II", 1, 8) + struct.pack("<QIIQQQIIII", 30, 7, 1, 0, 0, 0, 8, 0, 1, 0) + b"\x01\x00\x00\x00\x00\x00\x00\x00",
            b"DIAG": struct.pack("<II", 1, 0) + diagnostic,
        },
    ))
    plan = scene.build_import_plan(Path("level.uescene"), None)
    override = plan.components[0].material_overrides[0]
    assert (override.slot_ordinal, override.base_material_asset_id, override.override_material_asset_id) == (7, 0, 0)
    assert override.flags == 1 and override.parameter_payload == b"\x01\x00\x00\x00\x00\x00\x00\x00"
    assert plan.diagnostics[0].subject_id == subject_id

def test_plan_retains_instance_source_ordered_local_transforms():
    scene = _module().UEScene.from_bytes(_scene(
        chunks={
            b"META": _meta(1),
            b"COMP": _table(1, struct.pack("<QQQB3xIIIQQIIQIIII", 30, 20, 0, 2, 4, 10, 11, 101, 10, 0, 0, 40, 0, 0, 0, 0)),
            b"INST": _inst(),
        },
    ))
    instance = scene.build_import_plan(Path("level.uescene"), None).components[0].instance_set
    assert instance.transforms[0].translation == (7.0, 8.0, 9.0)
    assert instance.transforms[0].rotation == (0.0, 0.0, 0.0, 1.0)
    assert instance.transforms[0].scale == (2.0, 3.0, 4.0)

def test_plan_resolves_ism_lod0_without_materializing_instances(tmp_path: Path):
    model = tmp_path / "models" / "cube.uemodel"; model.parent.mkdir(); model.write_bytes(b"model")
    scene = _module().UEScene.from_bytes(_scene(
        chunks={
            b"META": _meta(1),
            b"COMP": _table(1, struct.pack("<QQQB3xIIIQQIIQIIII", 30, 20, 0, 2, 4, 10, 11, 101, 10, 0, 0, 40, 0, 0, 0, 0)),
            b"INST": _inst(),
        },
    ))
    plan = scene.build_import_plan(tmp_path / "level.uescene", tmp_path)
    assert plan.components[0].model_path == model
    assert plan.components[0].instance_set.transforms[0].translation == (7.0, 8.0, 9.0)

def test_relative_uri_is_bounded_by_scene_parent(tmp_path: Path):
    scene_path = tmp_path / "scene" / "level.uescene"; scene_path.parent.mkdir()
    resource = scene_path.parent / "models" / "cube.uemodel"; resource.parent.mkdir(); resource.write_bytes(b"model")
    scene = _module().UEScene.from_bytes(_scene(chunks={
        b"ASST": struct.pack("<II", 1, 1) + struct.pack("<QBBBBIIII32sQIIII", 10, 1, 1, 1, 0, 5, 6, 2, 7, sha256(b"model").digest(), 5, 0, 0, 0, 1) + struct.pack("<II32sQ6dfI", 0, 2, sha256(b"model").digest(), 5, 0, 0, 0, 1, 1, 1, float("nan"), 0),
        b"BNDX": _table(1, struct.pack("<BBBBIQI32sQIII", 1, 1, 1, 0, 2, 10, 0xffffffff, sha256(b"model").digest(), 5, 7, 0, 0)),
    }))
    assert scene.build_import_plan(scene_path, None).components[0].model_path == resource

def test_plan_rejects_unselected_relative_resource_outside_scene_root(tmp_path: Path):
    scene_path = tmp_path / "scene" / "level.uescene"; scene_path.parent.mkdir()
    outside = tmp_path / "outside"; outside.mkdir()
    try: (scene_path.parent / "linked").symlink_to(outside, target_is_directory=True)
    except OSError: pytest.skip("creating directory symlinks is unavailable")
    strings = _strings([b"uescene", b"models/cube.uemodel", b"Actor", b"Component", b"/Game/Cube", b"Cube", b"model/uemodel", b"ActorClass", b"/Game/Actor", b"ComponentClass", b"/Game/Component", b"linked/other.uemodel"])
    scene = _module().UEScene.from_bytes(_scene(strs=strings, chunks={
        b"ASST": struct.pack("<II", 1, 2) + struct.pack("<QBBBBIIII32sQIIII", 10, 1, 1, 1, 0, 5, 6, 2, 7, sha256(b"model").digest(), 5, 0, 0, 0, 2) + struct.pack("<II32sQ6dfI", 0, 2, sha256(b"model").digest(), 5, 0, 0, 0, 1, 1, 1, float("nan"), 0) + struct.pack("<II32sQ6dfI", 1, 12, sha256(b"other").digest(), 5, 0, 0, 0, 1, 1, 1, float("nan"), 0),
        b"BNDX": _table(1, struct.pack("<BBBBIQI32sQIII", 1, 1, 1, 0, 2, 10, 0xffffffff, sha256(b"model").digest(), 5, 7, 0, 0)),
    }))
    with pytest.raises(_module().UESceneError, match="escapes its root"):
        scene.build_import_plan(scene_path, None)

def test_accepts_instance_set_diagnostic_and_rejects_missing_set_subject():
    diagnostic = struct.pack("<BBHIQIIQII", 2, 4, 1, 0, 40, 0, 12, 0, 0, 0)
    scene = _module().UEScene.from_bytes(_scene(
        strs=_strings([b"uescene", b"models/cube.uemodel", b"Actor", b"Component", b"/Game/Cube", b"Cube", b"model/uemodel", b"ActorClass", b"/Game/Actor", b"ComponentClass", b"/Game/Component", b"instance warning"]),
        chunks={b"META": _meta(1), b"COMP": _table(1, _component(30, kind=2, instance_set=40)), b"INST": _inst(), b"DIAG": struct.pack("<II", 1, 0) + diagnostic},
    ))
    assert scene.diagnostics[0].subject_id == 40
    invalid = struct.pack("<BBHIQIIQII", 2, 4, 1, 0, 41, 0, 12, 0, 0, 0)
    with pytest.raises(_module().UESceneError, match="instance set diagnostic"):
        _module().UEScene.from_bytes(_scene(
            strs=_strings([b"uescene", b"models/cube.uemodel", b"Actor", b"Component", b"/Game/Cube", b"Cube", b"model/uemodel", b"ActorClass", b"/Game/Actor", b"ComponentClass", b"/Game/Component", b"instance warning"]),
            chunks={b"META": _meta(1), b"COMP": _table(1, _component(30, kind=2, instance_set=40)), b"INST": _inst(), b"DIAG": struct.pack("<II", 1, 0) + invalid},
        ))

def test_accepts_structurally_valid_noncanonical_directory_offset():
    data = bytearray(_scene())
    directory = bytes(data[44:44 + 9 * 40])
    del data[44:44 + 9 * 40]
    directory_offset = len(data)
    data.extend(directory)
    for i in range(9):
        entry = directory_offset + i * 40
        old_offset = int.from_bytes(data[entry + 8:entry + 16], "little")
        data[entry + 8:entry + 16] = (old_offset - 9 * 40).to_bytes(8, "little")
    data[20:28] = directory_offset.to_bytes(8, "little")
    data[32:40] = len(data).to_bytes(8, "little")
    data[40:44] = zlib.crc32(data[:40]).to_bytes(4, "little")
    assert _module().UEScene.from_bytes(data).metadata.format_name == "uescene"

def test_rejects_unknown_required_chunk_and_duplicate_known_chunk():
    data = bytearray(_scene()); data[44:48] = b"FUTR"; data[44 + 6 * 40 + 6:44 + 6 * 40 + 8] = (1).to_bytes(2, "little")
    with pytest.raises(_module().UESceneError): _module().UEScene.from_bytes(data)
    data = bytearray(_scene()); data[44 + 40:44 + 44] = b"META"
    with pytest.raises(_module().UESceneError, match="duplicate"): _module().UEScene.from_bytes(data)

@pytest.mark.parametrize("chunk, offset", [(b"META", 4), (b"ASST", 28), (b"ASST", 96), (b"ACTR", 28), (b"COMP", 40), (b"BNDX", 8)])
def test_rejects_required_semantic_string_ids(chunk: bytes, offset: int):
    data = _mutate_chunk(_scene(), chunk, offset, b"\0\0\0\0")
    with pytest.raises(_module().UESceneError, match="required string"):
        _module().UEScene.from_bytes(data)

def test_accepts_absent_optional_string_references():
    scene = _module().UEScene.from_bytes(_scene(chunks={
        b"ASST": struct.pack("<II", 1, 1) + struct.pack("<QBBBBIIII32sQIIII", 10, 1, 2, 1, 0, 0, 0, 2, 0, sha256(b"model").digest(), 5, 0, 0, 0, 1) + struct.pack("<II32sQ6dfI", 0, 2, sha256(b"model").digest(), 5, 0, 0, 0, 1, 1, 1, float("nan"), 0),
        b"ACTR": _table(1, struct.pack("<QQIIIQQIIIII", 20, 0, 0, 0, 9, 30, 100, 0, 1, 0, 0, 0)),
        b"COMP": _table(1, struct.pack("<QQQB3xIIIQQIIQIIII", 30, 20, 0, 1, 0, 0, 11, 101, 10, 0, 0, 0, 0, 0, 0, 0)),
        b"BNDX": _table(1, struct.pack("<BBBBIQI32sQIII", 1, 2, 1, 0, 2, 10, 0xffffffff, sha256(b"model").digest(), 5, 0, 0, 0)),
    }))
    assert scene.assets[0].path is scene.assets[0].name is scene.assets[0].mime is None
    assert scene.actors[0].name is scene.actors[0].class_path is None
    assert scene.components[0].name is scene.components[0].class_path is None
    assert scene.bundle_entries[0].mime is None

def test_rejects_absent_diagnostic_message():
    diagnostic = struct.pack("<BBHIQIIQII", 1, 0, 1, 0, 0, 0, 0, 0, 0, 0)
    with pytest.raises(_module().UESceneError, match="required string"):
        _module().UEScene.from_bytes(_scene(chunks={b"DIAG": struct.pack("<II", 1, 0) + diagnostic}))

def test_retains_complete_lod_and_component_wire_fields():
    scene = _module().UEScene.from_bytes(_scene(chunks={
        b"ASST": struct.pack("<II", 1, 1) + struct.pack("<QBBBBIIII32sQIIII", 10, 1, 2, 1, 0, 5, 6, 2, 7, sha256(b"model").digest(), 5, 0, 0, 0, 1) + struct.pack("<II32sQ6dfI", 0, 2, sha256(b"model").digest(), 5, -1, -2, -3, 4, 5, 6, 0.75, 0),
        b"COMP": _table(1, struct.pack("<QQQB3xIIIQQIIQIIII", 30, 20, 0, 1, 4, 10, 11, 101, 10, 0, 0, 0, 0x23, 0, 0, 0)),
    }))
    assert scene.lods[0].bounds_min == (-1.0, -2.0, -3.0)
    assert scene.lods[0].bounds_max == (4.0, 5.0, 6.0)
    assert scene.lods[0].source_screen_size == 0.75
    assert scene.components[0].visibility_flags == 0x23
    component = scene.build_import_plan(Path("level.uescene"), None).components[0]
    assert component.mesh_asset.path == "/Game/Cube"
    assert component.mesh_lods[0].bounds_max == (4.0, 5.0, 6.0)
    assert component.visibility_flags == 0x23

@pytest.mark.parametrize("root, expected", [(0, "root component"), (999, "root component")])
def test_rejects_actor_root_that_is_not_exactly_owned_root(root: int, expected: str):
    data = _mutate_chunk(_scene(), b"ACTR", 32, root.to_bytes(8, "little"))
    with pytest.raises(_module().UESceneError, match=expected):
        _module().UEScene.from_bytes(data)

def test_rejects_actor_root_with_component_parent():
    actor = struct.pack("<QQIIIQQIIIII", 20, 0, 3, 8, 9, 30, 100, 0, 2, 0, 0, 0)
    component = struct.pack("<QQQB3xIIIQQIIQIIII", 30, 20, 31, 1, 4, 10, 11, 101, 10, 0, 0, 0, 0, 0, 0, 0) + struct.pack("<QQQB3xIIIQQIIQIIII", 31, 20, 0, 1, 4, 10, 11, 101, 10, 0, 0, 0, 0, 0, 0, 0)
    with pytest.raises(_module().UESceneError, match="root component"):
        _module().UEScene.from_bytes(_scene(chunks={b"ACTR": _table(1, actor), b"COMP": _table(2, component)}))

def test_rejects_actor_with_multiple_parentless_owned_components():
    actor = struct.pack("<QQIIIQQIIIII", 20, 0, 3, 8, 9, 30, 100, 0, 2, 0, 0, 0)
    with pytest.raises(_module().UESceneError, match="root component"):
        _module().UEScene.from_bytes(_scene(chunks={b"ACTR": _table(1, actor), b"COMP": _table(2, _component(30) + _component(31))}))

def test_rejects_shared_instance_set_between_instanced_components():
    actor = struct.pack("<QQIIIQQIIIII", 20, 0, 3, 8, 9, 30, 100, 0, 2, 0, 0, 0)
    with pytest.raises(_module().UESceneError, match="instance set ownership"):
        _module().UEScene.from_bytes(_scene(chunks={
            b"META": _meta(1), b"ACTR": _table(1, actor),
            b"COMP": _table(2, _component(30, kind=2, instance_set=40) + _component(31, parent=30, kind=2, instance_set=40)),
            b"INST": _inst(),
        }))

def test_rejects_instanced_component_without_a_reciprocal_instance_set():
    actor = struct.pack("<QQIIIQQIIIII", 20, 0, 3, 8, 9, 30, 100, 0, 2, 0, 0, 0)
    with pytest.raises(_module().UESceneError, match="instance set"):
        _module().UEScene.from_bytes(_scene(chunks={
            b"META": _meta(1), b"ACTR": _table(1, actor),
            b"COMP": _table(2, _component(30, kind=2, instance_set=40) + _component(31, parent=30, kind=2, instance_set=41)),
            b"INST": _inst(),
        }))

def test_rejects_unsafe_resource_uri_during_structural_read():
    strings = _strings([b"uescene", b"../cube.uemodel", b"Actor", b"Component", b"/Game/Cube", b"Cube", b"model/uemodel", b"ActorClass", b"/Game/Actor", b"ComponentClass", b"/Game/Component"])
    with pytest.raises(_module().UESceneError, match="unsafe model URI"):
        _module().UEScene.from_bytes(_scene(strs=strings))

def test_rejects_inst_overlapping_or_unowned_transform_ranges():
    records = struct.pack("<QQIIII", 40, 30, 1, 0, 0, 1) + struct.pack("<QQIIII", 41, 30, 1, 0, 0, 1)
    transforms = struct.pack("<3f4f3f", 0, 0, 0, 0, 0, 0, 1, 1, 1, 1) * 2
    with pytest.raises(_module().UESceneError, match="instance transform"):
        _module().UEScene.from_bytes(_scene(chunks={
            b"META": _meta(1),
            b"COMP": _table(1, struct.pack("<QQQB3xIIIQQIIQIIII", 30, 20, 0, 2, 4, 10, 11, 101, 10, 0, 0, 40, 0, 0, 0, 0)),
            b"INST": struct.pack("<II", 2, 2) + records + transforms,
        }))

def test_rejects_known_chunk_over_one_gib_before_payload_slice():
    data = bytearray(_scene())
    data[44 + 16:44 + 24] = (1024 ** 3 + 1).to_bytes(8, "little")
    data[44 + 24:44 + 32] = (1024 ** 3 + 1).to_bytes(8, "little")
    with pytest.raises(_module().UESceneError, match="1 GiB"):
        _module().UEScene.from_bytes(data)

def test_rejects_total_fixed_records_from_directory_before_decoding():
    data = bytearray(_scene())
    data[44 + 36:44 + 40] = (10_000_001).to_bytes(4, "little")
    with pytest.raises(_module().UESceneError, match="total fixed records"):
        _module().UEScene.from_bytes(data)

def test_rejects_unknown_chunk_record_count_over_global_predecode_limit():
    with pytest.raises(_module().UESceneError, match="total fixed records"):
        _module().UEScene.from_bytes(_scene(chunks={b"FUTR": _table(10_000_001)}))

def test_rejects_lods_when_asst_has_no_assets_before_decoding_records():
    with pytest.raises(_module().UESceneError, match="ASST count exceeds limit"):
        _module().UEScene.from_bytes(_scene(chunks={b"ASST": struct.pack("<II", 0, 1) + b"\0" * 104}))

def test_accepts_supported_diagnostic_code_upper_bound_and_rejects_code_twelve():
    accepted = struct.pack("<BBHIQIIQII", 1, 0, 9, 0, 0, 0, 12, 0, 0, 0)
    scene = _module().UEScene.from_bytes(_scene(
        strs=_strings([b"uescene", b"models/cube.uemodel", b"Actor", b"Component", b"/Game/Cube", b"Cube", b"model/uemodel", b"ActorClass", b"/Game/Actor", b"ComponentClass", b"/Game/Component", b"diagnostic"]),
        chunks={b"DIAG": struct.pack("<II", 1, 0) + accepted},
    ))
    assert scene.diagnostics[0].code == 9

    upper_bound = struct.pack("<BBHIQIIQII", 1, 0, 10, 0, 0, 0, 12, 0, 0, 0)
    scene = _module().UEScene.from_bytes(_scene(
        strs=_strings([b"uescene", b"models/cube.uemodel", b"Actor", b"Component", b"/Game/Cube", b"Cube", b"model/uemodel", b"ActorClass", b"/Game/Actor", b"ComponentClass", b"/Game/Component", b"diagnostic"]),
        chunks={b"DIAG": struct.pack("<II", 1, 0) + upper_bound},
    ))
    assert scene.diagnostics[0].code == 10

    code_eleven = struct.pack("<BBHIQIIQII", 1, 0, 11, 0, 0, 0, 12, 0, 0, 0)
    scene = _module().UEScene.from_bytes(_scene(
        strs=_strings([b"uescene", b"models/cube.uemodel", b"Actor", b"Component", b"/Game/Cube", b"Cube", b"model/uemodel", b"ActorClass", b"/Game/Actor", b"ComponentClass", b"/Game/Component", b"diagnostic"]),
        chunks={b"DIAG": struct.pack("<II", 1, 0) + code_eleven},
    ))
    assert scene.diagnostics[0].code == 11

    rejected = struct.pack("<BBHIQIIQII", 1, 0, 12, 0, 0, 0, 12, 0, 0, 0)
    with pytest.raises(_module().UESceneError, match="invalid DIAG record"):
        _module().UEScene.from_bytes(_scene(
            strs=_strings([b"uescene", b"models/cube.uemodel", b"Actor", b"Component", b"/Game/Cube", b"Cube", b"model/uemodel", b"ActorClass", b"/Game/Actor", b"ComponentClass", b"/Game/Component", b"diagnostic"]),
            chunks={b"DIAG": struct.pack("<II", 1, 0) + rejected},
        ))

@pytest.mark.parametrize("chunk, offset", [(b"ACTR", 28), (b"COMP", 40)])
def test_rejects_actor_and_component_source_paths_over_16384_utf8_bytes(chunk: bytes, offset: int):
    strings = [b"uescene", b"models/cube.uemodel", b"Actor", b"Component", b"/Game/Cube", b"Cube", b"model/uemodel", b"ActorClass", b"/Game/Actor", b"ComponentClass", b"/Game/Component", b"x" * 16_385]
    data = _mutate_chunk(_scene(strs=_strings(strings)), chunk, offset, (12).to_bytes(4, "little"))
    with pytest.raises(_module().UESceneError, match="source path exceeds"):
        _module().UEScene.from_bytes(data)

def test_rejects_diagnostic_source_path_over_16384_utf8_bytes():
    strings = [b"uescene", b"models/cube.uemodel", b"Actor", b"Component", b"/Game/Cube", b"Cube", b"model/uemodel", b"ActorClass", b"/Game/Actor", b"ComponentClass", b"/Game/Component", b"x" * 16_385, b"diagnostic"]
    diagnostic = struct.pack("<BBHIQIIQII", 1, 0, 1, 0, 0, 12, 13, 0, 0, 0)
    with pytest.raises(_module().UESceneError, match="source path exceeds"):
        _module().UEScene.from_bytes(_scene(strs=_strings(strings), chunks={b"DIAG": struct.pack("<II", 1, 0) + diagnostic}))


def _scene_import_module(monkeypatch):
    class Object(dict):
        __eq__ = object.__eq__

        def __init__(self, name, data=None):
            super().__init__()
            self.name = name
            self.data = data
            self.type = "LIGHT" if isinstance(data, Light) else "EMPTY"
            self.parent = None
            self.users_collection = []
            self.location = None
            self.rotation_mode = None
            self.rotation_quaternion = None
            self.scale = None
            self.hide_viewport = False
            self.hide_render = False

        def as_pointer(self):
            return id(self)

        def copy(self):
            copy = Object(self.name, self.data)
            copy.update(self)
            bpy.data.objects.append(copy)
            return copy

        def __setitem__(self, key, value):
            if isinstance(value, int) and not -(2 ** 31) <= value < 2 ** 31:
                raise OverflowError("Blender IDProperty integer out of range")
            super().__setitem__(key, value)

    class Objects(list):
        def new(self, name, data):
            obj = Object(name, data)
            self.append(obj)
            return obj

        def remove(self, obj, do_unlink=False):
            if do_unlink:
                for collection in tuple(obj.users_collection):
                    collection.objects.unlink(obj)
            super().remove(obj)

    class Light:
        def __init__(self, name, kind):
            self.name = name
            self.type = kind

    class Lights(list):
        def new(self, name, kind):
            light = Light(name, kind)
            self.append(light)
            return light

    class ObjectLinks(list):
        def link(self, obj):
            self.append(obj)
            obj.users_collection.append(self.collection)

        def unlink(self, obj):
            self.remove(obj)
            obj.users_collection.remove(self.collection)

    class Collection(dict):
        __hash__ = object.__hash__
        __eq__ = object.__eq__
        __ne__ = object.__ne__

        def __init__(self, name):
            super().__init__()
            self.name = name
            self.objects = ObjectLinks()
            self.objects.collection = self
            self.children = Children()
            self.children.parent = self
            self.users_collection = []

    class Collections(list):
        def new(self, name):
            existing = {collection.name for collection in self}
            if name in existing:
                suffix = 1
                while f"{name}.{suffix:03d}" in existing:
                    suffix += 1
                name = f"{name}.{suffix:03d}"
            collection = Collection(name)
            self.append(collection)
            return collection

        def remove(self, collection, do_unlink=False):
            if do_unlink:
                for parent in tuple(collection.users_collection):
                    parent.children.unlink(collection)
            list.remove(self, collection)

    class Children(list):
        def link(self, collection):
            self.append(collection)
            collection.users_collection.append(self.parent)

        def unlink(self, collection):
            self.remove(collection)
            collection.users_collection.remove(self.parent)

    bpy = types.ModuleType("bpy")
    root_collection = Collection("Scene Collection")
    bpy.data = types.SimpleNamespace(objects=Objects(), collections=Collections(), lights=Lights())
    bpy.context = types.SimpleNamespace(scene=types.SimpleNamespace(collection=root_collection))
    bpy.types = types.SimpleNamespace(Collection=Collection, Object=Object)
    logic = types.ModuleType("io_scene_ueformat.importer.logic")
    logic.UEFormatImport = object
    logic.read_uemodel_source_lods = lambda _path: None
    monkeypatch.setitem(sys.modules, "bpy", bpy)
    monkeypatch.setitem(sys.modules, "io_scene_ueformat.importer.logic", logic)
    sys.modules.pop("io_scene_ueformat.importer.scene_import", None)
    return importlib.import_module("io_scene_ueformat.importer.scene_import"), bpy, logic


@pytest.mark.parametrize("target_lod, expected_state, imported", [(0, "model_imported", True), (1, "model_imported", True), (2, "requested_lod_unavailable", False)])
def test_scene_import_uses_primary_model_for_requested_existing_lod(monkeypatch, tmp_path: Path, target_lod: int, expected_state: str, imported: bool):
    model = tmp_path / "models" / "cube.uemodel"
    model.parent.mkdir()
    model.write_bytes(b"model")
    asset = struct.pack("<QBBBBIIII32sQIIII", 10, 1, 2, 1, 0, 5, 6, 2, 7, sha256(b"model").digest(), 5, 0, 0, 0, 2)
    lod0 = struct.pack("<II32sQ6dfI", 0, 2, sha256(b"lod0").digest(), 4, 0, 0, 0, 1, 1, 1, float("nan"), 0)
    lod1 = struct.pack("<II32sQ6dfI", 1, 2, sha256(b"lod1").digest(), 4, 0, 0, 0, 1, 1, 1, 0.5, 0)
    plan = _module().UEScene.from_bytes(_scene(chunks={b"ASST": struct.pack("<II", 1, 2) + asset + lod0 + lod1})).build_import_plan(tmp_path / "level.uescene", tmp_path)
    scene_import, bpy, logic = _scene_import_module(monkeypatch)
    calls = []

    class Importer:
        def __init__(self, options, material_options):
            calls.append((options, material_options))

        def import_file(self, path):
            calls[-1] += (path,)
            root = bpy.data.objects.new("Imported root", None)
            child = bpy.data.objects.new("Imported child", None)
            bpy.context.scene.collection.objects.link(root)
            bpy.context.scene.collection.objects.link(child)
            child.parent = root

    logic.UEFormatImport = Importer
    scene_import.UEFormatImport = Importer
    options = types.SimpleNamespace(scale_factor=1.0, target_lod=target_lod)
    @dataclass(frozen=True)
    class MaterialOptions:
        bundle_root: str | None = None

    summary = scene_import.import_uescene_plan(plan, options, MaterialOptions())
    component = next(obj for obj in bpy.data.objects if obj.name == "Component")
    assert component["uescene_import_state"] == expected_state
    assert component.parent.name == "Actor"
    assert component["uescene_component_id"] == "30"
    assert component["uescene_actor_id"] == "20"
    assert bool(calls)
    if imported:
        assert calls == [(options, calls[0][1], model)]
        assert calls[0][0].target_lod == target_lod
        assert summary.collection.name == "Import_uescene"
        assert summary.collection in bpy.context.scene.collection.children
        assert all(summary.models_collection in obj.users_collection for obj in bpy.data.objects)
        assert all(obj.users_collection == [summary.models_collection] for obj in bpy.data.objects)
        assert next(obj for obj in bpy.data.objects if obj.name == "Imported root").parent is component
        assert next(obj for obj in bpy.data.objects if obj.name == "Imported child").parent.name == "Imported root"
    else:
        assert summary.warnings == ["component 30: requested LOD 2 is unavailable"]
        assert not any(obj.name.startswith("Imported ") for obj in bpy.data.objects)


def test_scene_import_passes_plan_bundle_root_to_material_link_importer(monkeypatch, tmp_path: Path):
    bundle_root = tmp_path / "bundle"
    model = bundle_root / "models" / "cube.uemodel"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"model")
    plan = _module().UEScene.from_bytes(_scene()).build_import_plan(bundle_root / "level.uescene", bundle_root)
    scene_import, _bpy, _logic = _scene_import_module(monkeypatch)
    calls = []

    class Importer:
        def __init__(self, options, material_options):
            calls.append((options, material_options))

        def import_file(self, path):
            calls[-1] += (path,)

    @dataclass(frozen=True)
    class MaterialOptions:
        bundle_root: str | None = None

    scene_import.UEFormatImport = Importer
    options = types.SimpleNamespace(scale_factor=1.0, target_lod=0)
    scene_import.import_uescene_plan(plan, options, MaterialOptions())

    assert calls == [(options, MaterialOptions(str(bundle_root)), model)]


def test_scene_import_rolls_back_objects_created_by_failed_model_import(monkeypatch, tmp_path: Path):
    bundle_root = tmp_path / "bundle"
    model = bundle_root / "models" / "cube.uemodel"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"model")
    plan = _module().UEScene.from_bytes(_scene()).build_import_plan(bundle_root / "level.uescene", bundle_root)
    scene_import, bpy, _logic = _scene_import_module(monkeypatch)

    class Importer:
        def __init__(self, _options, _material_options):
            pass

        def import_file(self, _path):
            partial = bpy.data.objects.new("Partial model", None)
            bpy.context.scene.collection.objects.link(partial)
            raise RuntimeError("synthetic malformed model")

    scene_import.UEFormatImport = Importer

    @dataclass(frozen=True)
    class MaterialOptions:
        bundle_root: str | None = None

    summary = scene_import.import_uescene_plan(
        plan, types.SimpleNamespace(scale_factor=1.0, target_lod=0), MaterialOptions()
    )

    component = next(obj for obj in bpy.data.objects if obj.name == "Component")
    assert component["uescene_import_state"] == "model_import_failed"
    assert summary.failed_models == 1
    assert not any(obj.name == "Partial model" for obj in bpy.data.objects)


def test_scene_import_represents_ism_with_instances_not_copied_model_trees(monkeypatch, tmp_path: Path):
    bundle_root = tmp_path / "bundle"
    model = bundle_root / "models" / "cube.uemodel"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"model")
    transforms = b"".join(
        struct.pack("<3f4f3f", float(index), 0, 0, 0, 0, 0, 1, 1, 1, 1)
        for index in range(3)
    )
    instance = struct.pack("<II", 1, 3) + struct.pack("<QQIIII", 40, 30, 3, 0, 0, 3) + transforms
    plan = _module().UEScene.from_bytes(_scene(chunks={
        b"META": _meta(1),
        b"COMP": _table(1, _component(30, kind=2, instance_set=40)),
        b"INST": instance,
    })).build_import_plan(
        bundle_root / "level.uescene", bundle_root
    )
    scene_import, bpy, _logic = _scene_import_module(monkeypatch)

    class Importer:
        def __init__(self, _options, _material_options):
            pass

        def import_file(self, _path):
            bpy.context.scene.collection.objects.link(bpy.data.objects.new("Template mesh", None))

    scene_import.UEFormatImport = Importer

    @dataclass(frozen=True)
    class MaterialOptions:
        bundle_root: str | None = None

    summary = scene_import.import_uescene_plan(
        plan, types.SimpleNamespace(scale_factor=1.0, target_lod=0), MaterialOptions()
    )

    controller = next(
        obj for obj in bpy.data.objects
        if obj.get("uescene_instance_representation") == "Collection Instance"
    )
    instances = [obj for obj in bpy.data.objects if obj.parent is controller]
    assert len(instances) == 3
    assert summary.deferred_instances == 3
    assert controller.name == "HISM Cube [3 instances]"
    assert controller["uescene_source_template_collection"] == controller["uescene_instance_template_collection"]
    instanced_geometry = next(child for child in summary.collection.children if child.name == "Instanced Geometry")
    assert controller.users_collection == [instanced_geometry]
    technical = next(child for child in summary.collection.children if child.name == "UEFormat Technical")
    source_templates = next(child for child in technical.children if child.name == "Source Templates")
    template = next(
        item for item in bpy.data.collections
        if item.name == controller["uescene_source_template_collection"]
    )
    assert template in source_templates.children


def test_scene_import_returns_structured_partial_result_for_malformed_model(monkeypatch, tmp_path: Path):
    model = tmp_path / "models" / "cube.uemodel"
    model.parent.mkdir()
    model.write_bytes(b"model")
    plan = _module().UEScene.from_bytes(_scene()).build_import_plan(tmp_path / "level.uescene", tmp_path)
    scene_import, _bpy, _logic = _scene_import_module(monkeypatch)

    class Importer:
        def __init__(self, options, material_options):
            pass

        def import_file(self, path):
            raise ValueError("malformed model")

    scene_import.UEFormatImport = Importer
    summary = scene_import.import_uescene_plan(
        plan,
        types.SimpleNamespace(scale_factor=1.0, target_lod=0),
        object(),
        strict_required_models=False,
    )

    assert summary.outcome == "partial"
    assert summary.required_model_failures[0].component_id == 30
    assert summary.required_model_failures[0].code == "malformed"


def test_scene_import_raises_machine_readable_failure_for_malformed_model_in_strict_mode(monkeypatch, tmp_path: Path):
    model = tmp_path / "models" / "cube.uemodel"
    model.parent.mkdir()
    model.write_bytes(b"model")
    plan = _module().UEScene.from_bytes(_scene()).build_import_plan(tmp_path / "level.uescene", tmp_path)
    scene_import, _bpy, _logic = _scene_import_module(monkeypatch)

    class Importer:
        def __init__(self, options, material_options):
            pass

        def import_file(self, path):
            raise ValueError("malformed model")

    scene_import.UEFormatImport = Importer

    with pytest.raises(scene_import.RequiredModelImportError) as caught:
        scene_import.import_uescene_plan(
            plan,
            types.SimpleNamespace(scale_factor=1.0, target_lod=0),
            object(),
            strict_required_models=True,
        )

    assert caught.value.failures[0].code == "malformed"


def test_scene_import_rejects_lod_manifest_mismatch_before_creating_collections(monkeypatch, tmp_path: Path):
    model = tmp_path / "models" / "cube.uemodel"
    model.parent.mkdir()
    model.write_bytes(b"model")
    plan = _module().UEScene.from_bytes(_scene()).build_import_plan(tmp_path / "level.uescene", tmp_path)
    scene_import, bpy, _logic = _scene_import_module(monkeypatch)
    scene_import.read_uemodel_source_lods = lambda _path: types.SimpleNamespace(source_lods=frozenset({1}), material_slots={})

    with pytest.raises(scene_import.RequiredModelImportError) as caught:
        scene_import.import_uescene_plan(
            plan, types.SimpleNamespace(scale_factor=1.0, target_lod=0), object()
        )

    assert caught.value.failures[0].code == "lod_manifest_mismatch"
    assert not bpy.data.collections
    assert not bpy.data.objects


def test_scene_import_rejects_mato_material_link_mismatch_before_creating_collections(monkeypatch, tmp_path: Path):
    model = tmp_path / "models" / "cube.uemodel"
    model.parent.mkdir()
    model.write_bytes(b"model")
    plan = _module().UEScene.from_bytes(_scene()).build_import_plan(tmp_path / "level.uescene", tmp_path)
    scene_import, bpy, _logic = _scene_import_module(monkeypatch)
    scene_import.read_uemodel_source_lods = lambda _path: types.SimpleNamespace(source_lods=frozenset({0}), material_slots={0: "/Game/M_Effective"}, source_material_mapping_present=True)
    override = _module().MaterialOverride(30, 0, 0, 0, 11, b"", 0, 0)
    material = replace(plan.assets[0], id=11, kind=2, path="/Game/M_Override")
    plan = replace(plan, assets=(plan.assets[0], material), components=(replace(plan.components[0], material_overrides=(override,)),))

    with pytest.raises(scene_import.RequiredModelImportError) as caught:
        scene_import.import_uescene_plan(plan, types.SimpleNamespace(scale_factor=1.0, target_lod=0), object())

    assert caught.value.failures[0].code == "mato_material_link_mismatch"
    assert not bpy.data.collections
    assert not bpy.data.objects


def test_scene_import_accepts_matching_mato_material_link(monkeypatch, tmp_path: Path):
    model = tmp_path / "models" / "cube.uemodel"
    model.parent.mkdir()
    model.write_bytes(b"model")
    plan = _module().UEScene.from_bytes(_scene()).build_import_plan(tmp_path / "level.uescene", tmp_path)
    scene_import, _bpy, _logic = _scene_import_module(monkeypatch)
    scene_import.read_uemodel_source_lods = lambda _path: types.SimpleNamespace(source_lods=frozenset({0}), material_slots={0: "/Game/M_Override"}, source_material_mapping_present=True)
    override = _module().MaterialOverride(30, 0, 0, 0, 11, b"", 0, 0)
    material = replace(plan.assets[0], id=11, kind=2, path="/Game/M_Override")
    plan = replace(plan, assets=(plan.assets[0], material), components=(replace(plan.components[0], material_overrides=(override,)),))

    class Importer:
        def __init__(self, _options, _material_options):
            pass

        def import_file(self, _path):
            pass

    scene_import.UEFormatImport = Importer
    summary = scene_import.import_uescene_plan(plan, types.SimpleNamespace(scale_factor=1.0, target_lod=0), object())

    assert summary.imported_static_meshes == 1


def test_scene_import_maps_mato_slot_ordinal_by_source_material_index(monkeypatch, tmp_path: Path):
    model = tmp_path / "models" / "cube.uemodel"
    model.parent.mkdir()
    model.write_bytes(b"model")
    plan = _module().UEScene.from_bytes(_scene()).build_import_plan(tmp_path / "level.uescene", tmp_path)
    scene_import, _bpy, _logic = _scene_import_module(monkeypatch)
    scene_import.read_uemodel_source_lods = lambda _path: types.SimpleNamespace(
        source_lods=frozenset({0}),
        material_slots={1: "/Game/M_Source1", 0: "/Game/M_Source0"},
        source_material_mapping_present=True,
    )
    override = _module().MaterialOverride(30, 1, 0, 0, 11, b"", 0, 0)
    material = replace(plan.assets[0], id=11, kind=2, path="/Game/M_Source1")
    plan = replace(plan, assets=(plan.assets[0], material), components=(replace(plan.components[0], material_overrides=(override,)),))

    scene_import.import_uescene_plan(plan, types.SimpleNamespace(scale_factor=1.0, target_lod=0), object())


def test_scene_import_accepts_shared_source_material_identity(monkeypatch, tmp_path: Path):
    model = tmp_path / "models" / "cube.uemodel"
    model.parent.mkdir()
    model.write_bytes(b"model")
    plan = _module().UEScene.from_bytes(_scene()).build_import_plan(tmp_path / "level.uescene", tmp_path)
    scene_import, _bpy, _logic = _scene_import_module(monkeypatch)
    scene_import.read_uemodel_source_lods = lambda _path: types.SimpleNamespace(
        source_lods=frozenset({0}),
        material_slots={0: "/Game/M_Source.M_Source", 1: "/Game/M_Source"},
        source_material_mapping_present=True,
    )
    override = _module().MaterialOverride(30, 0, 0, 0, 11, b"", 0, 0)
    material = replace(plan.assets[0], id=11, kind=2, path="/Game/M_Source")
    plan = replace(plan, assets=(plan.assets[0], material), components=(replace(plan.components[0], material_overrides=(override,)),))
    scene_import.import_uescene_plan(plan, types.SimpleNamespace(scale_factor=1.0, target_lod=0), object())


def test_scene_import_rejects_ambiguous_shared_source_material_identity(monkeypatch, tmp_path: Path):
    model = tmp_path / "models" / "cube.uemodel"
    model.parent.mkdir()
    model.write_bytes(b"model")
    plan = _module().UEScene.from_bytes(_scene()).build_import_plan(tmp_path / "level.uescene", tmp_path)
    scene_import, bpy, _logic = _scene_import_module(monkeypatch)
    imported = []
    class Importer:
        def __init__(self, _options, _material_options):
            imported.append("constructed")

        def import_file(self, _path):
            imported.append("imported")

    scene_import.UEFormatImport = Importer
    scene_import.read_uemodel_source_lods = lambda _path: (_ for _ in ()).throw(
        scene_import.UEMODELSourceMaterialAmbiguityError(
            "mato_source_material_mapping_ambiguous: source material index 0 has conflicting identities"
        )
    )
    with pytest.raises(scene_import.RequiredModelImportError) as caught:
        scene_import.import_uescene_plan(plan, types.SimpleNamespace(scale_factor=1.0, target_lod=0), object())
    assert caught.value.failures[0].code == "mato_source_material_mapping_ambiguous"
    assert imported == []
    assert not bpy.data.collections


def test_scene_import_rejects_absent_source_material_mapping(monkeypatch, tmp_path: Path):
    model = tmp_path / "models" / "cube.uemodel"
    model.parent.mkdir()
    model.write_bytes(b"model")
    plan = _module().UEScene.from_bytes(_scene()).build_import_plan(tmp_path / "level.uescene", tmp_path)
    scene_import, _bpy, _logic = _scene_import_module(monkeypatch)
    scene_import.read_uemodel_source_lods = lambda _path: types.SimpleNamespace(
        source_lods=frozenset({0}), material_slots={}, source_material_mapping_present=True,
    )
    override = _module().MaterialOverride(30, 0, 0, 0, 11, b"", 0, 0)
    material = replace(plan.assets[0], id=11, kind=2, path="/Game/M_Source")
    plan = replace(plan, assets=(plan.assets[0], material), components=(replace(plan.components[0], material_overrides=(override,)),))
    with pytest.raises(scene_import.RequiredModelImportError) as caught:
        scene_import.import_uescene_plan(plan, types.SimpleNamespace(scale_factor=1.0, target_lod=0), object())
    assert caught.value.failures[0].code == "mato_source_material_mapping_missing"


def test_scene_import_rejects_mato_without_source_material_mapping(monkeypatch, tmp_path: Path):
    model = tmp_path / "models" / "cube.uemodel"
    model.parent.mkdir()
    model.write_bytes(b"model")
    plan = _module().UEScene.from_bytes(_scene()).build_import_plan(tmp_path / "level.uescene", tmp_path)
    scene_import, _bpy, _logic = _scene_import_module(monkeypatch)
    scene_import.read_uemodel_source_lods = lambda _path: types.SimpleNamespace(
        source_lods=frozenset({0}), material_slots={0: "/Game/M_OldV1"}, source_material_mapping_present=False,
    )
    override = _module().MaterialOverride(30, 0, 0, 0, 11, b"", 0, 0)
    material = replace(plan.assets[0], id=11, kind=2, path="/Game/M_OldV1")
    plan = replace(plan, assets=(plan.assets[0], material), components=(replace(plan.components[0], material_overrides=(override,)),))

    with pytest.raises(scene_import.RequiredModelImportError) as caught:
        scene_import.import_uescene_plan(plan, types.SimpleNamespace(scale_factor=1.0, target_lod=0), object())
    assert "source material mapping" in caught.value.failures[0].detail


def test_scene_import_accepts_valid_unreal_material_object_alias(monkeypatch, tmp_path: Path):
    model = tmp_path / "models" / "cube.uemodel"
    model.parent.mkdir()
    model.write_bytes(b"model")
    plan = _module().UEScene.from_bytes(_scene()).build_import_plan(tmp_path / "level.uescene", tmp_path)
    scene_import, _bpy, _logic = _scene_import_module(monkeypatch)
    scene_import.read_uemodel_source_lods = lambda _path: types.SimpleNamespace(
        source_lods=frozenset({0}), material_slots={0: "/Game/M_Override.M_Override"}, source_material_mapping_present=True
    )
    override = _module().MaterialOverride(30, 0, 0, 0, 11, b"", 0, 0)
    material = replace(plan.assets[0], id=11, kind=2, path="/Game/M_Override")
    plan = replace(plan, assets=(plan.assets[0], material), components=(replace(plan.components[0], material_overrides=(override,)),))

    scene_import.import_uescene_plan(plan, types.SimpleNamespace(scale_factor=1.0, target_lod=0), object())


def test_scene_import_accepts_unreal_material_link_without_leading_slash(monkeypatch, tmp_path: Path):
    model = tmp_path / "models" / "cube.uemodel"
    model.parent.mkdir()
    model.write_bytes(b"model")
    plan = _module().UEScene.from_bytes(_scene()).build_import_plan(tmp_path / "level.uescene", tmp_path)
    scene_import, _bpy, _logic = _scene_import_module(monkeypatch)
    scene_import.read_uemodel_source_lods = lambda _path: types.SimpleNamespace(
        source_lods=frozenset({0}), material_slots={0: "Engine/M_Override.M_Override"}, source_material_mapping_present=True
    )
    override = _module().MaterialOverride(30, 0, 0, 0, 11, b"", 0, 0)
    material = replace(plan.assets[0], id=11, kind=2, path="/Engine/M_Override")
    plan = replace(plan, assets=(plan.assets[0], material), components=(replace(plan.components[0], material_overrides=(override,)),))

    scene_import.import_uescene_plan(plan, types.SimpleNamespace(scale_factor=1.0, target_lod=0), object())


def test_scene_import_rejects_identical_malformed_mato_material_link(monkeypatch, tmp_path: Path):
    model = tmp_path / "models" / "cube.uemodel"
    model.parent.mkdir()
    model.write_bytes(b"model")
    plan = _module().UEScene.from_bytes(_scene()).build_import_plan(tmp_path / "level.uescene", tmp_path)
    scene_import, bpy, _logic = _scene_import_module(monkeypatch)
    malformed = "/Game//M"
    scene_import.read_uemodel_source_lods = lambda _path: types.SimpleNamespace(
        source_lods=frozenset({0}), material_slots={0: malformed}, source_material_mapping_present=True
    )
    override = _module().MaterialOverride(30, 0, 0, 0, 11, b"", 0, 0)
    material = replace(plan.assets[0], id=11, kind=2, path=malformed)
    plan = replace(plan, assets=(plan.assets[0], material), components=(replace(plan.components[0], material_overrides=(override,)),))

    with pytest.raises(scene_import.RequiredModelImportError) as caught:
        scene_import.import_uescene_plan(plan, types.SimpleNamespace(scale_factor=1.0, target_lod=0), object())

    assert caught.value.failures[0].code == "mato_material_link_mismatch"
    assert not bpy.data.collections


def test_strict_scene_import_failure_keeps_existing_source_graph(monkeypatch, tmp_path: Path):
    plan = _module().UEScene.from_bytes(_scene()).build_import_plan(tmp_path / "level.uescene", tmp_path)
    scene_import, bpy, _logic = _scene_import_module(monkeypatch)
    options = types.SimpleNamespace(scale_factor=1.0, target_lod=0)
    previous = scene_import.import_uescene_plan(plan, options, object())
    model = tmp_path / "models" / "cube.uemodel"
    model.parent.mkdir()
    model.write_bytes(b"model")
    plan = _module().UEScene.from_bytes(_scene()).build_import_plan(tmp_path / "level.uescene", tmp_path)

    class Importer:
        def __init__(self, _options, _material_options):
            pass

        def import_file(self, _path):
            bpy.context.scene.collection.objects.link(bpy.data.objects.new("Partial model", None))
            raise ValueError("malformed model")

    scene_import.UEFormatImport = Importer
    with pytest.raises(scene_import.RequiredModelImportError):
        scene_import.import_uescene_plan(plan, options, object(), strict_required_models=True)

    assert any(collection is previous.collection for collection in bpy.data.collections)
    assert any(obj.name == "Actor" for obj in bpy.data.objects)
    assert not any("_staging" in collection.name for collection in bpy.data.collections)
    assert not any(obj.name == "Partial model" for obj in bpy.data.objects)


def test_strict_scene_import_promotes_candidate_after_success(monkeypatch, tmp_path: Path):
    plan = _module().UEScene.from_bytes(_scene()).build_import_plan(tmp_path / "level.uescene", tmp_path)
    scene_import, bpy, _logic = _scene_import_module(monkeypatch)
    options = types.SimpleNamespace(scale_factor=1.0, target_lod=0)
    previous = scene_import.import_uescene_plan(plan, options, object())
    model = tmp_path / "models" / "cube.uemodel"
    model.parent.mkdir()
    model.write_bytes(b"model")
    plan = _module().UEScene.from_bytes(_scene()).build_import_plan(tmp_path / "level.uescene", tmp_path)

    class Importer:
        def __init__(self, _options, _material_options):
            pass

        def import_file(self, _path):
            bpy.context.scene.collection.objects.link(bpy.data.objects.new("Imported model", None))

    scene_import.UEFormatImport = Importer
    summary = scene_import.import_uescene_plan(plan, options, object(), strict_required_models=True)

    source_key = scene_import._scene_source_key(plan)
    assert not any(collection is previous.collection for collection in bpy.data.collections)
    assert summary.collection in bpy.context.scene.collection.children
    assert summary.collection.name == "Import_uescene"
    assert summary.collection["uescene_source_key"] == source_key
    assert [collection for collection in bpy.data.collections if collection.get("uescene_source_key") == source_key] == [summary.collection]
    assert not any("_staging" in collection.name for collection in bpy.data.collections)


def test_strict_scene_import_missing_requested_lod_keeps_existing_source_graph(monkeypatch, tmp_path: Path):
    model = tmp_path / "models" / "cube.uemodel"
    model.parent.mkdir()
    model.write_bytes(b"model")
    asset = struct.pack("<QBBBBIIII32sQIIII", 10, 1, 2, 1, 0, 5, 6, 2, 7, sha256(b"model").digest(), 5, 0, 0, 0, 1)
    lod = struct.pack("<II32sQ6dfI", 0, 2, sha256(b"lod").digest(), 4, 0, 0, 0, 1, 1, 1, float("nan"), 0)
    plan = _module().UEScene.from_bytes(_scene(chunks={b"ASST": struct.pack("<II", 1, 1) + asset + lod})).build_import_plan(tmp_path / "level.uescene", tmp_path)
    scene_import, bpy, _logic = _scene_import_module(monkeypatch)
    previous = scene_import.import_uescene_plan(plan, types.SimpleNamespace(scale_factor=1.0, target_lod=0), object())

    class Importer:
        def __init__(self, _options, _material_options):
            pass

        def import_file(self, _path):
            bpy.context.scene.collection.objects.link(bpy.data.objects.new("Imported model", None))

    scene_import.UEFormatImport = Importer
    with pytest.raises(scene_import.RequiredModelImportError) as caught:
        scene_import.import_uescene_plan(plan, types.SimpleNamespace(scale_factor=1.0, target_lod=1), object(), strict_required_models=True)

    assert caught.value.failures[0].code == "requested_lod_unavailable"
    assert any(collection is previous.collection for collection in bpy.data.collections)
    assert not any("_staging" in collection.name for collection in bpy.data.collections)


def test_scene_import_keeps_mato_as_provenance_after_model_material_link_import(monkeypatch, tmp_path: Path):
    bundle_root = tmp_path / "bundle"
    model = bundle_root / "models" / "cube.uemodel"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"model")
    plan = _module().UEScene.from_bytes(_scene()).build_import_plan(bundle_root / "level.uescene", bundle_root)
    override = _module().MaterialOverride(30, 0, 1, 0, 999, b"", 0, 0)
    plan = replace(plan, components=(replace(plan.components[0], material_overrides=(override,)),))
    scene_import, _bpy, _logic = _scene_import_module(monkeypatch)
    calls = []

    class Importer:
        def __init__(self, options, material_options):
            calls.append((options, material_options))

        def import_file(self, path):
            calls[-1] += (path,)

    scene_import.UEFormatImport = Importer
    options = types.SimpleNamespace(scale_factor=1.0, target_lod=0)

    @dataclass(frozen=True)
    class MaterialOptions:
        bundle_root: str | None = None

    summary = scene_import.import_uescene_plan(plan, options, MaterialOptions())

    assert calls == [(options, MaterialOptions(str(bundle_root)), model)]
    assert summary.warnings == []


def test_scene_import_groups_each_import_under_a_safe_unique_collection(monkeypatch, tmp_path: Path):
    plan = _module().UEScene.from_bytes(_scene()).build_import_plan(tmp_path / "level.uescene", tmp_path)
    plan = replace(plan, provenance=replace(plan.provenance, source_world_path="/Game/Maps/City Night"))
    scene_import, bpy, _logic = _scene_import_module(monkeypatch)
    options = types.SimpleNamespace(scale_factor=1.0, target_lod=2)

    first = scene_import.import_uescene_plan(plan, options, object())
    second = scene_import.import_uescene_plan(plan, options, object())

    assert [first.collection.name, second.collection.name] == ["City_Night_uescene", "City_Night_uescene"]
    assert all(collection is not first.collection for collection in bpy.data.collections)
    assert second.collection in bpy.data.collections


def test_scene_import_collapses_repeated_terminal_world_name_without_changing_provenance(monkeypatch, tmp_path: Path):
    source_world_path = "/Game/Maps/House/photo/house_photo001.house_photo001"
    plan = _module().UEScene.from_bytes(_scene()).build_import_plan(tmp_path / "level.uescene", tmp_path)
    plan = replace(plan, provenance=replace(plan.provenance, source_world_path=source_world_path))
    scene_import, _bpy, _logic = _scene_import_module(monkeypatch)

    summary = scene_import.import_uescene_plan(
        plan, types.SimpleNamespace(scale_factor=1.0, target_lod=2), object()
    )

    assert plan.provenance.source_world_path == source_world_path
    assert summary.collection.name == "house_photo001_uescene"
    assert summary.collection["uescene_source_key"] == scene_import._scene_source_key(plan)
    assert source_world_path in summary.collection["uescene_source_key"]


@pytest.mark.parametrize("source_world_path, expected", [
    ("/Game/Maps/House/photo/house_photo001.house_photo002", "house_photo001.house_photo002"),
    ("/Game/Maps/House/photo/house_photo001", "house_photo001"),
    ("house_photo001.house_photo001", "house_photo001.house_photo001"),
    ("/Game//house_photo001.house_photo001", "house_photo001.house_photo001"),
    ("/Game/House/photo/house photo.house photo", "house_photo"),
    ("/Game/House/photo/house:photo.house:photo", "house_photo"),
    ("/Game/House/photo/house.photo.house.photo", "house.photo.house.photo"),
    ("/Game/House.Name/photo/house_photo001.house_photo001", "house_photo001.house_photo001"),
    ("/Game/Maps/M+N.M+N", "M_N"),
])
def test_scene_name_keeps_noncanonical_repeated_tokens_under_existing_sanitization(monkeypatch, source_world_path, expected):
    scene_import, _bpy, _logic = _scene_import_module(monkeypatch)

    assert scene_import._scene_name(source_world_path) == expected


def test_scene_import_preserves_large_uescene_ids_as_decimal_properties(monkeypatch, tmp_path: Path):
    actor_id = 2 ** 31 + 20
    component_id = 2 ** 31 + 30
    asset_id = 2 ** 31 + 10
    instance_set_id = 2 ** 31 + 40
    actor = struct.pack("<QQIIIQQIIIII", actor_id, 0, 3, 8, 9, component_id, 100, 0, 1, 0, 0, 0)
    component = struct.pack(
        "<QQQB3xIIIQQIIQIIII",
        component_id, actor_id, 0, 2, 4, 10, 11, 101, asset_id, 0, 0, instance_set_id, 0, 0, 0, 0,
    )
    asset = struct.pack(
        "<QBBBBIIII32sQIIII", asset_id, 1, 2, 1, 0, 5, 6, 2, 7,
        sha256(b"model").digest(), 5, 0, 0, 0, 1,
    ) + struct.pack("<II32sQ6dfI", 0, 2, sha256(b"model").digest(), 5, 0, 0, 0, 1, 1, 1, float("nan"), 0)
    instance = struct.pack("<II", 1, 1) + struct.pack("<QQIIII", instance_set_id, component_id, 1, 0, 0, 1) + struct.pack("<3f4f3f", 0, 0, 0, 0, 0, 0, 1, 1, 1, 1)
    plan = _module().UEScene.from_bytes(_scene(chunks={
        b"META": _meta(1), b"ASST": struct.pack("<II", 1, 1) + asset,
        b"ACTR": _table(1, actor), b"COMP": _table(1, component), b"INST": instance,
        b"BNDX": _table(1, struct.pack("<BBBBIQI32sQIII", 1, 2, 1, 0, 2, asset_id, 0xffffffff, sha256(b"model").digest(), 5, 7, 0, 0)),
    })).build_import_plan(tmp_path / "level.uescene", tmp_path)
    scene_import, bpy, _logic = _scene_import_module(monkeypatch)

    summary = scene_import.import_uescene_plan(
        plan, types.SimpleNamespace(scale_factor=1.0, target_lod=0), object()
    )

    imported_actor = next(obj for obj in bpy.data.objects if obj.name == "Actor")
    imported_component = next(obj for obj in bpy.data.objects if obj.name == "Component")
    assert imported_actor["uescene_actor_id"] == str(actor_id)
    assert imported_actor["uescene_parent_actor_id"] == "0"
    assert imported_component["uescene_component_id"] == str(component_id)
    assert imported_component["uescene_actor_id"] == str(actor_id)
    assert imported_component["uescene_parent_component_id"] == "0"
    assert imported_component["uescene_asset_id"] == str(asset_id)
    assert imported_component["uescene_instance_set_id"] == str(instance_set_id)
    assert imported_component["uescene_import_state"] == "missing_model"
    assert summary.deferred_instances == 0


def test_scene_import_serializes_material_override_uint64_ids_as_decimal_strings(monkeypatch):
    material_override = _module().MaterialOverride
    scene_import, _bpy, _logic = _scene_import_module(monkeypatch)
    maximum_uint64 = 2 ** 64 - 1

    metadata = json.loads(scene_import._metadata(scene_import._overrides((
        material_override(30, 0, 1, 0, maximum_uint64, b"", 0, 0),
        material_override(30, 1, 1, maximum_uint64, 0, b"", 0, 0),
    ))))

    assert metadata[0]["base_material_asset_id"] == "0"
    assert metadata[0]["override_material_asset_id"] == str(maximum_uint64)
    assert metadata[1]["base_material_asset_id"] == str(maximum_uint64)
    assert metadata[1]["override_material_asset_id"] == "0"


def test_scene_import_materializes_all_native_light_types(monkeypatch, tmp_path: Path):
    lights = b"".join((
        _light(40, 1, 1, source_angle=10, source_soft_angle=2),
        _light(41, 2, 2 | 16, parent=30, intensity=42, attenuation_radius=100, falloff_exponent=8, source_radius=4, soft_source_radius=2, source_length=6),
        _light(42, 3, 2 | 4 | 16, parent=30, intensity=43, attenuation_radius=500, falloff_exponent=8, inner=10, outer=30, source_radius=5),
        _light(43, 4, 2 | 8, parent=30, intensity=44, attenuation_radius=250, width=40, height=20, barn_door_angle=45, barn_door_length=5),
    ))
    plan = _module().UEScene.from_bytes(_scene(optional_features=2, lights=lights)).build_import_plan(
        tmp_path / "level.uescene", tmp_path
    )
    assert plan.light_transform(plan.lights[0]).id == 100
    scene_import, bpy, _logic = _scene_import_module(monkeypatch)

    summary = scene_import.import_uescene_plan(
        plan, types.SimpleNamespace(scale_factor=0.01, target_lod=2), object()
    )

    objects = {obj.name: obj for obj in bpy.data.objects if obj.data is None}
    light_objects = {obj.data.type: obj for obj in bpy.data.objects if obj.data is not None}
    assert summary.imported_lights == 4
    assert summary.collection.name == "Import_uescene"
    assert all(
        summary.models_collection in obj.users_collection
        or summary.lights_collection in obj.users_collection
        for obj in bpy.data.objects
    )
    assert [light.type for light in bpy.data.lights] == ["SUN", "POINT", "SPOT", "AREA"]
    sun, point, spot, rect = (light_objects[kind] for kind in ("SUN", "POINT", "SPOT", "AREA"))
    component = objects["Component"]
    assert sun.parent is objects["Actor"] and point.parent is component and spot.parent is component and rect.parent is component
    assert sun.location == (0.01, -0.02, 0.03) and sun.rotation_quaternion == (-1.0, 0.0, 0.0, 0.0)
    assert sun.data.color == (1.0, 240 / 255, 200 / 255) and sun.data.use_shadow is True
    assert sun.data.angle == pytest.approx(__import__("math").radians(10))
    assert point.data.energy == 42 and point.data.use_custom_distance is True
    assert point.data.cutoff_distance == 1 and point.data.shadow_soft_size == 0.04
    assert spot.data.spot_size == pytest.approx(__import__("math").radians(30)) and spot.data.spot_blend == pytest.approx(2 / 3)
    assert rect.data.shape == "RECTANGLE" and rect.data.size == 0.4 and rect.data.size_y == 0.2 and rect.data.normalize is False
    assert rect.data.use_custom_distance is True and rect.data.cutoff_distance == 2.5
    assert point["uescene_light_id"] == "41" and point["uescene_actor_id"] == "20"
    assert point["uescene_light_name"] == "Actor"
    assert point["uescene_parent_component_id"] == "30" and point["uescene_transform_id"] == "100"
    assert point["uescene_light_color_rgba"] == "[255,240,200,255]"
    assert point["uescene_light_flags"] == 18 and point["uescene_light_units"] == 2
    assert point["uescene_light_intensity"] == 42 and point["uescene_class_path"] == "ActorClass"
    assert point["uescene_source_path"] == "/Game/Actor" and point["uescene_light_energy_policy"] == "raw_intensity"


def test_scene_import_groups_resources_by_map_and_type(monkeypatch, tmp_path: Path):
    lights = _light(40, 1, 1)
    scene = _module().UEScene.from_bytes(_scene(optional_features=2, lights=lights))
    plan = scene.build_import_plan(tmp_path / "level.uescene", tmp_path)
    scene_import, bpy, _logic = _scene_import_module(monkeypatch)

    summary = scene_import.import_uescene_plan(
        plan, types.SimpleNamespace(scale_factor=0.01, target_lod=2), object()
    )

    assert summary.collection.name == "Import_uescene"
    assert summary.models_collection.name == "Import_Models"
    assert summary.lights_collection.name == "Import_Lights"
    children = {child.name: child for child in summary.collection.children}
    assert children == {
        "Import_Models": summary.models_collection,
        "Import_Lights": summary.lights_collection,
        "Instanced Geometry": children["Instanced Geometry"],
        "UEFormat Technical": children["UEFormat Technical"],
    }
    assert [child.name for child in children["UEFormat Technical"].children] == ["Source Templates"]
    assert not any(collection.name == "Import_Probes" for collection in bpy.data.collections)
    actor = next(obj for obj in bpy.data.objects if obj.name == "Actor")
    component = next(obj for obj in bpy.data.objects if obj.name == "Component")
    light = next(obj for obj in bpy.data.objects if obj.type == "LIGHT")
    assert summary.models_collection in actor.users_collection and summary.models_collection in component.users_collection
    assert summary.lights_collection in light.users_collection
