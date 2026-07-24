import json
import sys
import types
from pathlib import Path

import bpy

PLUGIN_ROOT = Path(__file__).parents[1] / "plugins" / "blender"
package = types.ModuleType("io_scene_ueformat")
package.__path__ = [str(PLUGIN_ROOT / "io_scene_ueformat")]
sys.modules["io_scene_ueformat"] = package

from io_scene_ueformat.importer.classes import (  # noqa: E402
    FModelMaterialLinks,
    FModelMaterialParameter,
    FModelMaterialSlot,
    TextureResource,
)
from io_scene_ueformat.importer.fmodel import material_links_metadata  # noqa: E402

image_count = len(bpy.data.images)
material_count = len(bpy.data.materials)
mesh = bpy.data.meshes.new("FModelMetadataSmoke")
links = FModelMaterialLinks(slots=[FModelMaterialSlot(
    slot_index=0,
    slot_name="Body",
    unreal_material_path="/Game/Materials/M_Body.M_Body",
    material_json_uri="materials/body.json",
    parameters=[FModelMaterialParameter(
        name="BaseColor",
        kind=1,
        semantic="base_color",
        value=TextureResource(id=7, unreal_object_path="/Game/Textures/T_Body_BC.T_Body_BC", location_mode=2),
    )],
)])
mesh["fmodel_material_links"] = json.dumps(material_links_metadata(links))
assert json.loads(mesh["fmodel_material_links"])["slots"][0]["parameters"][0]["value"]["texture_id"] == 7
assert len(bpy.data.images) == image_count
assert len(bpy.data.materials) == material_count
bpy.data.meshes.remove(mesh)
print("FModel metadata smoke passed")
