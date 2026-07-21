import json
import struct
import sys
import tempfile
import zlib
import os
from hashlib import sha256
from pathlib import Path

import bpy


PLUGIN_ROOT = Path(os.environ.get(
    "UESCENE_ADDON_ROOT", Path(__file__).resolve().parents[1] / "plugins" / "blender"
))
sys.path.insert(0, str(PLUGIN_ROOT))

import io_scene_ueformat  # noqa: E402
from io_scene_ueformat import op  # noqa: E402

assert Path(io_scene_ueformat.__file__).parent.parent == PLUGIN_ROOT


def _strings(values):
    return struct.pack("<I", len(values)) + b"".join(
        struct.pack("<I", len(value)) + value for value in values
    )


def _table(count, records=b""):
    return struct.pack("<I", count) + records


def _light(ident, light_type, flags, **values):
    fields = {
        "intensity": 1.0, "attenuation_radius": 0.0, "falloff_exponent": 0.0,
        "inner": 0.0, "outer": 0.0, "width": 0.0, "height": 0.0,
        "source_angle": 0.0, "source_soft_angle": 0.0, "source_radius": 0.0,
        "soft_source_radius": 0.0, "source_length": 0.0, "barn_door_angle": 0.0,
        "barn_door_length": 0.0,
    }
    fields.update(values)
    return struct.pack(
        "<QQQQIIIBBBB4sI7fI7f", ident, 2 ** 31 + 20, 0, 100, 3, 8, 9,
        light_type, 1, 2, 0, bytes((255, 240, 200, 255)), flags,
        fields["intensity"], fields["attenuation_radius"], fields["falloff_exponent"],
        fields["inner"], fields["outer"], fields["width"], fields["height"], 0,
        fields["source_angle"], fields["source_soft_angle"], fields["source_radius"],
        fields["soft_source_radius"], fields["source_length"], fields["barn_door_angle"],
        fields["barn_door_length"],
    )

def write_scene(path, uri=b"Models/present.uemodel", optional_features=0):
    asset_id = 2 ** 31 + 10
    actor_id = 2 ** 31 + 20
    component_id = 2 ** 31 + 30
    strings = _strings([
        b"uescene", uri, b"Actor", b"Component",
        b"/Game/Cube", b"Cube", b"model/uemodel", b"ActorClass",
        b"/Game/Actor", b"ComponentClass", b"/Game/Component",
    ])
    meta = struct.pack("<I8IQ4BIQQ", 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 0, 0, optional_features)
    model_hash = sha256(b"present").digest()
    chunks = {
        b"META": meta,
        b"STRS": strings,
        b"ASST": struct.pack("<II", 1, 1) + struct.pack(
            "<QBBBBIIII32sQIIII", asset_id, 1, 2, 1, 0, 5, 6, 2, 7, model_hash, 7, 0, 0, 0, 1
        ) + struct.pack("<II32sQ6dfI", 0, 2, model_hash, 7, 0, 0, 0, 1, 1, 1, float("nan"), 0),
        b"ACTR": _table(1, struct.pack("<QQIIIQQIIIII", actor_id, 0, 3, 8, 9, component_id, 100, 0, 1, 0, 0, 0)),
        b"COMP": _table(1, struct.pack("<QQQB3xIIIQQIIQIIII", component_id, actor_id, 0, 1, 4, 10, 11, 101, asset_id, 0, 0, 0, 0, 0, 0, 0)),
        b"XFRM": _table(2, struct.pack("<Q3d4f3fI", 100, 1, 2, 3, 0, 0, 0, 1, 1, 1, 1, 0) + struct.pack("<Q3d4f3fI", 101, 4, 5, 6, 0, 0, 0, 1, 1, 1, 1, 0)),
        b"MATO": struct.pack("<II", 0, 0),
        b"DIAG": struct.pack("<II", 0, 0),
        b"BNDX": _table(1, struct.pack("<BBBBIQI32sQIII", 1, 2, 1, 0, 2, asset_id, 0xffffffff, model_hash, 7, 7, 0, 0)),
    }
    if optional_features & 2:
        chunks[b"LITE"] = _table(4, b"".join((
            _light(2 ** 31 + 40, 1, 1, intensity=10, source_angle=5),
            _light(2 ** 31 + 41, 2, 2 | 16, intensity=20, attenuation_radius=100, source_radius=4),
            _light(2 ** 31 + 42, 3, 2 | 4 | 16, intensity=30, attenuation_radius=500, inner=10, outer=30, source_radius=5),
            _light(2 ** 31 + 43, 4, 2 | 8, intensity=40, attenuation_radius=250, width=40, height=20),
        )))
    directory_size = 40 * len(chunks)
    offset = (44 + directory_size + 7) & ~7
    entries = []
    body = bytearray()
    for name, payload in chunks.items():
        offset = (offset + 7) & ~7
        body.extend(b"\0" * (offset - (44 + directory_size) - len(body)))
        entries.append((name, offset, payload))
        body.extend(payload)
        offset += len(payload)
    size = 44 + directory_size + len(body)
    prefix = struct.pack("<8sHHIIQIQ", b"UESCENE\0", 1, 0, 0, 44, 44, len(entries), size)
    header = prefix + struct.pack("<I", zlib.crc32(prefix) & 0xffffffff)
    directory = b"".join(
        struct.pack("<4sHHQQQII", name, 1, 0, at, len(payload), len(payload), zlib.crc32(payload) & 0xffffffff, struct.unpack_from("<I", payload)[0])
        for name, at, payload in entries
    )
    path.write_bytes(header + directory + body)


assert not hasattr(bpy.types.Scene, "uf_settings")
io_scene_ueformat.register()
try:
    assert hasattr(bpy.ops.uf, "import_uescene")
    assert bpy.types.FileHandler.bl_rna_get_subclass_py("IO_FH_uescene") is not None
    with tempfile.TemporaryDirectory() as temporary_directory:
        scene_path = Path(temporary_directory) / "Exports" / "Game" / "Content" / "Maps" / "City" / "City.umap.uescene"
        invalid_path = Path(temporary_directory) / "invalid.uescene"
        model_path = Path(temporary_directory) / "Exports" / "Game" / "Content" / "Meshes" / "present.uemodel"
        model_path.parent.mkdir(parents=True)
        scene_path.parent.mkdir(parents=True)
        model_path.write_bytes(b"present")
        write_scene(scene_path, b"Content/Meshes/present.uemodel", 3)
        invalid_path.write_bytes(b"invalid")
        settings = bpy.context.scene.uf_settings
        settings.fmodel_bundle_root = ""
        settings.strict_uescene_models = False

        before = set(bpy.data.collections)
        result = bpy.ops.uf.import_uescene(filepath=str(scene_path))
        assert result == {"FINISHED"}
        report = settings.last_import_report
        assert report.startswith("UE Format Import Report\nKind: Scene\nStatus: Partial")
        status = json.loads(settings.last_import_status)
        assert status["outcome"] == "partial"
        assert status["required_model_failures"][0]["code"] == "malformed"
        assert f"File: {scene_path}" in report
        assert "Imported 1 actor(s), 1 component(s), 0 static mesh(es), and 4 light(s)" in report
        assert settings.last_import_summary.startswith("Imported 1 actor(s)")
        imported = bpy.data.collections["Import_uescene"]
        assert imported.name == "Import_uescene"
        models = imported.children["Import_Models"]
        lights_collection = imported.children["Import_Lights"]
        assert not [obj for obj in imported.all_objects if obj.type == "EMPTY"]
        lights = [obj for obj in lights_collection.objects if obj.type == "LIGHT"]
        assert [obj.data.type for obj in lights] == ["SUN", "POINT", "SPOT", "AREA"]
        assert all(obj.parent is None for obj in lights)
        assert [obj["uescene_light_id"] for obj in lights] == [str(2 ** 31 + ident) for ident in (40, 41, 42, 43)]
        assert lights[1].data.use_custom_distance and lights[1].data.cutoff_distance == 1.0
        assert lights[2].data.spot_size > 0 and lights[3].data.shape == "RECTANGLE"
        assert not [obj for obj in imported.all_objects if obj.type == "CAMERA"]
        settings.strict_uescene_models = True
        try:
            strict_result = bpy.ops.uf.import_uescene(filepath=str(scene_path))
        except RuntimeError:
            pass
        else:
            assert strict_result == {"CANCELLED"}
        strict_status = json.loads(settings.last_import_status)
        assert strict_status["outcome"] == "failed"
        assert strict_status["required_model_failures"][0]["code"] == "malformed"
        before_invalid = set(bpy.data.collections)
        try:
            result = bpy.ops.uf.import_uescene(filepath=str(invalid_path))
        except RuntimeError:
            pass
        else:
            assert result == {"CANCELLED"}
        assert json.loads(settings.last_import_status) == {
            "outcome": "failed", "required_model_failures": [],
        }
        assert set(bpy.data.collections) == before_invalid
finally:
    expected_unregistrations = iter(reversed(op.operators))
    unregister_class = bpy.utils.unregister_class
    remove_menu_callback = bpy.types.TOPBAR_MT_file_import.remove

    def checked_unregister_class(operator):
        assert operator is next(expected_unregistrations)
        if operator is op.UFSettings:
            assert not hasattr(bpy.types.Scene, "uf_settings")
        unregister_class(operator)

    def checked_remove_menu_callback(callback):
        assert callback is op.draw_import_menu
        remove_menu_callback(callback)

    bpy.utils.unregister_class = checked_unregister_class
    bpy.types.TOPBAR_MT_file_import.remove = checked_remove_menu_callback
    try:
        io_scene_ueformat.unregister()
    finally:
        bpy.utils.unregister_class = unregister_class
        bpy.types.TOPBAR_MT_file_import.remove = remove_menu_callback
    assert not hasattr(bpy.types.Scene, "uf_settings")

print("UEScene operator smoke passed")
