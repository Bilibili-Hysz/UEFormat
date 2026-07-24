import json
from pathlib import Path

import addon_utils
import bpy


REPOSITORY = Path(__file__).parents[1]
FIXTURE = REPOSITORY / "tests" / "fixtures" / "fmodel_v2_deployment.uemodel"
MANAGED_ADDON = Path(
    r"C:\Users\86177\AppData\Roaming\Blender Foundation\Blender\5.2\scripts\addons\io_scene_ueformat"
)


def clear_imported_data():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh)
    for material in list(bpy.data.materials):
        bpy.data.materials.remove(material)
    for image in list(bpy.data.images):
        bpy.data.images.remove(image)


def import_fixture():
    before = set(bpy.data.objects)
    result = bpy.ops.uf.import_uemodel(
        directory=str(FIXTURE.parent),
        files=[{"name": FIXTURE.name}],
    )
    assert result == {"FINISHED"}
    return next(obj for obj in bpy.data.objects if obj not in before and obj.type == "MESH")


def has_link(tree, source, target):
    return any(link.from_node == source and link.to_node == target for link in tree.links)


assert FIXTURE.is_file()
assert MANAGED_ADDON.is_dir()
addon_utils.enable("io_scene_ueformat", default_set=False, persistent=False)
try:
    from io_scene_ueformat.importer import material_builder

    assert Path(material_builder.__file__).parents[1] == MANAGED_ADDON
    assert hasattr(bpy.ops.uf, "import_uemodel")
    settings = bpy.context.scene.uf_settings
    settings.import_fmodel_materials = True
    settings.build_fmodel_material_nodes = True
    settings.load_fmodel_embedded_textures = True
    settings.keep_fmodel_unmapped_parameters = True

    imported = import_fixture()
    metadata = json.loads(imported.data["fmodel_material_links"])
    assert metadata["version"] == 2
    assert metadata["pbr_interfaces"] == [
        {"slot_index": 0, "parameter_index": 0, "role": 1},
        {"slot_index": 0, "parameter_index": 1, "role": 3},
        {"slot_index": 0, "parameter_index": 2, "role": 9},
    ]
    assert metadata["slots"][0]["parameters"][2]["value"]["packing"] == 7

    material = imported.data.materials[0]
    provenance = json.loads(material["fmodel_material_link"])
    assert provenance["version"] == 2
    assert provenance["slot_index"] == 0
    assert provenance["pbr_interfaces"] == metadata["pbr_interfaces"]

    tree = material.node_tree
    principled = next(node for node in tree.nodes if node.type == "BSDF_PRINCIPLED")
    base = next(node for node in tree.nodes if node.label == "BaseColor")
    normal = next(node for node in tree.nodes if node.label == "Normal")
    wetness = next(node for node in tree.nodes if node.label == "WetnessMask")
    normal_map = next(node for node in tree.nodes if node.type == "NORMAL_MAP")
    pbr_frame = next(node for node in tree.nodes if node.label == "PBR Inputs")
    custom_frame = next(node for node in tree.nodes if node.label == "Custom / Unmapped (Preserved)")
    assert base.parent == pbr_frame
    assert normal.parent == pbr_frame
    assert wetness.parent == custom_frame
    assert has_link(tree, base, principled)
    assert has_link(tree, normal, normal_map)
    assert has_link(tree, normal_map, principled)
    assert not any(link.from_node == wetness and link.to_node == principled for link in tree.links)
    assert wetness.image["fmodel_source_type"] == "embedded"
    assert wetness.image["fmodel_source_id"] == 2
    assert wetness.image.packed_file is not None

    clear_imported_data()
    settings.import_fmodel_materials = False
    imported = import_fixture()
    assert json.loads(imported.data["fmodel_material_links"])["version"] == 2
    assert all("fmodel_material_link" not in material for material in imported.data.materials)
finally:
    addon_utils.disable("io_scene_ueformat", default_set=False)

print("DEPLOYED_FMODEL_V2_OPERATOR_OK")
