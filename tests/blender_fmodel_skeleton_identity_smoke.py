import json
import struct
import sys
import types
from pathlib import Path

import bpy
from bpy_extras import anim_utils


REPOSITORY = Path(__file__).parents[1]
PLUGIN_ROOT = REPOSITORY / "plugins" / "blender"
package = types.ModuleType("io_scene_ueformat")
package.__path__ = [str(PLUGIN_ROOT / "io_scene_ueformat")]
sys.modules["io_scene_ueformat"] = package

from io_scene_ueformat.importer.logic import UEFormatImport  # noqa: E402
from io_scene_ueformat.options import UEAnimOptions, UEModelOptions  # noqa: E402
from io_scene_ueformat.importer.classes import EUEFormatVersion  # noqa: E402
from io_scene_ueformat.logging import Log  # noqa: E402


def fstring(value):
    encoded = value.encode("utf-8") + b"\0"
    return struct.pack("<i", len(encoded)) + encoded


def chunk(name, count, payload):
    return fstring(name) + struct.pack("<ii", count, len(payload)) + payload


def identity_payload():
    return (
        b"\x01"
        + fstring("/Game/Hero/SK_Hero_Skeleton.SK_Hero_Skeleton")
        + b"\x01"
        + bytes(range(16))
        + bytes(range(32))
    )


def track_payload():
    return (
        fstring("root")
        + struct.pack("<i", 1) + struct.pack("<i3f", 1, 0.0, 0.0, 0.0)
        + struct.pack("<i", 1) + struct.pack("<i4f", 1, 0.0, 0.0, 0.0, 1.0)
        + struct.pack("<i", 1) + struct.pack("<i3f", 1, 1.0, 1.0, 1.0)
    )


def action_fcurve_count(action):
    if hasattr(action, "fcurves"):
        return len(action.fcurves)
    return sum(len(anim_utils.action_ensure_channelbag_for_slot(action, slot).fcurves) for slot in action.slots)


model_payload = chunk("SKELETON", 1, chunk("BONES", 1, fstring("root") + struct.pack("<i", -1) + struct.pack("<fff", 0, 0, 0) + struct.pack("<ffff", 0, 0, 0, 1))) + chunk(
    "FMODEL_SKELETON_IDENTITY", 1, identity_payload(),
)
model_import = UEFormatImport(UEModelOptions(link=True))
model_archive = __import__("io_scene_ueformat.importer.reader", fromlist=["FArchiveReader"]).FArchiveReader(model_payload)
model_archive.file_version = EUEFormatVersion.LevelOfDetailFormatRestructure
model_archive.metadata["scale"] = 0.01
armature, model = model_import.import_uemodel_data(
    model_archive,
    "IdentityModel",
)

model_identity = json.loads(armature["fmodel_skeleton_identity"])
assert model_identity == {
    "bone_layout_hash": bytes(range(32)).hex(),
    "has_skeleton_guid": True,
    "skeleton_guid": bytes(range(16)).hex(),
    "skeleton_path": "/Game/Hero/SK_Hero_Skeleton.SK_Hero_Skeleton",
    "version": 1,
}
assert model.skeleton_identity is not None

animation_payload = chunk("TRACKS", 1, track_payload()) + chunk(
    "FMODEL_TARGET_SKELETON", 1, b"\x01" + fstring("/Game/Hero/A_Run.A_Run") + identity_payload()[1:],
)
animation_archive = __import__("io_scene_ueformat.importer.reader", fromlist=["FArchiveReader"]).FArchiveReader(animation_payload)
animation_archive.file_version = EUEFormatVersion.SerializeAssetMetadata
animation_archive.metadata["scale"] = 0.01
action, animation = UEFormatImport(UEAnimOptions(link=True, override_skeleton=armature)).import_ueanim_data(
    animation_archive,
    "IdentityAnimation",
)

action_identity = json.loads(action["fmodel_target_skeleton_identity"])
assert action_identity == {
    "bone_layout_hash": bytes(range(32)).hex(),
    "has_skeleton_guid": True,
    "skeleton_guid": bytes(range(16)).hex(),
    "skeleton_path": "/Game/Hero/SK_Hero_Skeleton.SK_Hero_Skeleton",
    "source_animation_path": "/Game/Hero/A_Run.A_Run",
    "version": 1,
}
assert animation.target_skeleton_identity is not None
assert armature.animation_data.action is action
assert action_fcurve_count(action) > 0


def import_identity_animation(target_armature, *, with_identity=True, strict=True):
    payload = chunk("TRACKS", 1, track_payload())
    if with_identity:
        payload += chunk(
            "FMODEL_TARGET_SKELETON", 1,
            b"\x01" + fstring("/Game/Hero/A_Run.A_Run") + identity_payload()[1:],
        )
    archive = __import__("io_scene_ueformat.importer.reader", fromlist=["FArchiveReader"]).FArchiveReader(payload)
    archive.file_version = EUEFormatVersion.SerializeAssetMetadata
    archive.metadata["scale"] = 0.01
    return UEFormatImport(UEAnimOptions(
        link=True,
        override_skeleton=target_armature,
        strict_fmodel_skeleton=strict,
    )).import_ueanim_data(
        archive,
        "SmokeAnimation",
    )


def assert_import_rejected_for(field):
    identity = json.loads(armature["fmodel_skeleton_identity"])
    identity[field] = {
        "skeleton_path": "/Game/Other/SK_Other.SK_Other",
        "skeleton_guid": "ff" * 16,
        "bone_layout_hash": "ff" * 32,
    }[field]
    armature["fmodel_skeleton_identity"] = json.dumps(identity)
    existing_action = bpy.data.actions.new(name=f"Existing_{field}")
    armature.animation_data_create()
    armature.animation_data.action = existing_action
    if hasattr(existing_action, "fcurves"):
        existing_curve = existing_action.fcurves.new(armature.pose.bones[0].path_from_id("location"), index=0)
    else:
        existing_slot = existing_action.slots.new(id_type="OBJECT", name=f"Slot_{armature.name}")
        existing_curve = anim_utils.action_ensure_channelbag_for_slot(existing_action, existing_slot).fcurves.new(
            armature.pose.bones[0].path_from_id("location"), index=0
        )
    existing_curve.keyframe_points.add(2)
    existing_curve.keyframe_points[0].co = 1, 2.5
    existing_curve.keyframe_points[1].co = 10, 7.5
    before_actions = list(bpy.data.actions)
    before_action = armature.animation_data.action
    before_keys = [(point.co.x, point.co.y) for point in existing_curve.keyframe_points]
    try:
        import_identity_animation(armature)
    except ValueError as error:
        assert field.replace("bone_layout_hash", "layout hash").replace("skeleton_path", "path").replace("skeleton_guid", "GUID") in str(error)
    else:
        raise AssertionError(f"expected {field} mismatch to reject")
    assert list(bpy.data.actions) == before_actions
    assert armature.animation_data.action is before_action
    assert [(point.co.x, point.co.y) for point in existing_curve.keyframe_points] == before_keys
    armature["fmodel_skeleton_identity"] = json.dumps(model_identity)


for mismatch_field in ("skeleton_path", "skeleton_guid", "bone_layout_hash"):
    assert_import_rejected_for(mismatch_field)

# Missing incoming identity rejects before action creation when armature provenance exists.
before_actions = list(bpy.data.actions)
try:
    import_identity_animation(armature, with_identity=False)
except ValueError as error:
    assert "target skeleton identity is missing" in str(error)
else:
    raise AssertionError("expected missing target identity to reject")
assert list(bpy.data.actions) == before_actions

# Missing armature identity also rejects before action creation.
armature_identity_json = armature["fmodel_skeleton_identity"]
del armature["fmodel_skeleton_identity"]
before_actions = list(bpy.data.actions)
try:
    import_identity_animation(armature)
except ValueError as error:
    assert "armature skeleton identity is missing" in str(error)
else:
    raise AssertionError("expected missing armature identity to reject")
assert list(bpy.data.actions) == before_actions

# Explicit permissive mode imports and records exact compatibility counts and source identity.
warnings = []
original_warn = Log.__dict__["warn"]
Log.warn = classmethod(lambda cls, message: warnings.append(message))
try:
    permissive_action, permissive_animation = import_identity_animation(armature, strict=False)
finally:
    Log.warn = original_warn
assert len(warnings) == 1
assert "bound=1, skipped=0, total=1" in warnings[0]
assert json.loads(permissive_action["fmodel_animation_binding"]) == {
    "bound_track_count": 1,
    "skipped_track_count": 0,
    "strict": False,
    "total_track_count": 1,
}
assert json.loads(permissive_action["fmodel_target_skeleton_identity"])["source_animation_path"] == "/Game/Hero/A_Run.A_Run"
assert permissive_animation.target_skeleton_identity is not None

# A legacy animation and armature without provenance remain manually importable.
legacy_archive = __import__("io_scene_ueformat.importer.reader", fromlist=["FArchiveReader"]).FArchiveReader(b"")
legacy_archive.file_version = EUEFormatVersion.SerializeAssetMetadata
legacy_action, legacy_animation = UEFormatImport(UEAnimOptions(link=False, override_skeleton=armature)).import_ueanim_data(
    legacy_archive,
    "LegacyAnimation",
)
assert legacy_action.name == "LegacyAnimation"
assert legacy_animation.target_skeleton_identity is None
armature["fmodel_skeleton_identity"] = armature_identity_json
print("FMODEL_SKELETON_IDENTITY_OK")
