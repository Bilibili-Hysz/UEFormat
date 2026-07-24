import json
from pathlib import Path

import addon_utils
import bpy


REPOSITORY = Path(__file__).parents[1]
MODEL = REPOSITORY / "tests" / "fixtures" / "fmodel_skeleton_identity.uemodel"
ANIMATION = REPOSITORY / "tests" / "fixtures" / "fmodel_target_skeleton.ueanim"
MANAGED_ADDON = Path(r"C:\Users\86177\AppData\Roaming\Blender Foundation\Blender\5.2\scripts\addons\io_scene_ueformat")


assert MANAGED_ADDON.is_dir()
assert MODEL.is_file() and ANIMATION.is_file()
addon_utils.enable("io_scene_ueformat", default_set=False, persistent=False)
try:
    assert hasattr(bpy.ops.uf, "import_uemodel")
    assert hasattr(bpy.ops.uf, "import_ueanim")
    result = bpy.ops.uf.import_uemodel(directory=str(MODEL.parent), files=[{"name": MODEL.name}])
    assert result == {"FINISHED"}
    armature = next(obj for obj in bpy.data.objects if obj.type == "ARMATURE")
    assert "fmodel_skeleton_identity" in armature
    model_identity = json.loads(armature["fmodel_skeleton_identity"])
    assert model_identity["skeleton_path"].endswith("SK_Hero_Skeleton.SK_Hero_Skeleton")

    bpy.context.view_layer.objects.active = armature
    armature.select_set(True)
    result = bpy.ops.uf.import_ueanim(directory=str(ANIMATION.parent), files=[{"name": ANIMATION.name}])
    assert result == {"FINISHED"}
    action = armature.animation_data.action
    assert action is not None
    animation_identity = json.loads(action["fmodel_target_skeleton_identity"])
    assert animation_identity["skeleton_path"] == model_identity["skeleton_path"]
    assert animation_identity["skeleton_guid"] == model_identity["skeleton_guid"]
    assert animation_identity["bone_layout_hash"] == model_identity["bone_layout_hash"]
    assert animation_identity["source_animation_path"].endswith("A_Run.A_Run")
finally:
    addon_utils.disable("io_scene_ueformat", default_set=False)

print("DEPLOYED_FMODEL_SKELETON_IDENTITY_OK")
