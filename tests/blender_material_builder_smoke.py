import base64
import json
import sys
import tempfile
import types
from pathlib import Path

import bpy


REPOSITORY = Path(__file__).parents[1]
PLUGIN_ROOT = REPOSITORY / "plugins" / "blender"
package = types.ModuleType("io_scene_ueformat")
package.__path__ = [str(PLUGIN_ROOT / "io_scene_ueformat")]
sys.modules["io_scene_ueformat"] = package

from io_scene_ueformat.importer.classes import (  # noqa: E402
    FModelMaterialLinks,
    FModelMaterialParameter,
    FModelMaterialSlot,
    Material,
    TextureResource,
)
from io_scene_ueformat.importer.fmodel import FModelResourceResolver  # noqa: E402
from io_scene_ueformat.importer.material_builder import build_fmodel_materials  # noqa: E402
from io_scene_ueformat.importer.material_images import FModelImageRepository  # noqa: E402


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUB"
    "AScY42YAAAAASUVORK5CYII="
)


def texture(name, semantic, texture_id, uv_set=0):
    return FModelMaterialParameter(
        name=name,
        kind=1,
        semantic=semantic,
        value=TextureResource(
            id=texture_id,
            unreal_object_path=f"/Game/Textures/T_{name}.T_{name}",
            location_mode=1,
            uri=f"textures/{texture_id}.png",
            uv_set=uv_set,
        ),
    )


def has_link(tree, from_node, from_socket, to_node, to_socket):
    return any(
        link.from_node == from_node
        and link.from_socket == from_socket
        and link.to_node == to_node
        and link.to_socket == to_socket
        for link in tree.links
    )


with tempfile.TemporaryDirectory() as temporary_directory:
    root = Path(temporary_directory)
    (root / "textures").mkdir()
    for texture_id in range(1, 6):
        (root / "textures" / f"{texture_id}.png").write_bytes(PNG_BYTES)
    model_path = root / "hero.ueformat"
    resolver = FModelResourceResolver(
        model_path=model_path,
        fmodel_textures=[],
        ue_export_root=root,
        bundle_root=root,
    )
    images = FModelImageRepository(resolver, model_path, root, root)
    mesh = bpy.data.meshes.new("FModelMaterialBuilderSmoke")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    mesh.uv_layers.new(name="UV0")
    mesh.uv_layers.new(name="UV1")
    lod_materials = [
        Material(material_name="IntentionallyDifferentA", material_path="", first_index=0, num_faces=1),
        Material(material_name="IntentionallyDifferentB", material_path="", first_index=0, num_faces=0),
    ]
    for lod_material in lod_materials:
        mesh.materials.append(bpy.data.materials.new(lod_material.material_name))
    mesh.materials[1]["fmodel_importer_owned"] = True
    untouched = mesh.materials[0]
    untouched.use_nodes = True
    untouched_node = untouched.node_tree.nodes.new("ShaderNodeValue")
    untouched_node.label = "Untouched node"
    untouched_node.outputs[0].default_value = 0.75
    untouched_snapshot = [
        (node.bl_idname, node.name, node.label, tuple(node.location))
        for node in untouched.node_tree.nodes
    ]
    raw_metadata = '{"version":1,"raw":"preserved"}'
    mesh["fmodel_material_links"] = raw_metadata
    links = FModelMaterialLinks(slots=[
        FModelMaterialSlot(
            slot_index=1,
            slot_name="DoesNotMatchMaterialName",
            unreal_material_path="/Game/Materials/M_Body.M_Body",
            material_json_uri="materials/body.json",
            parameters=[
                texture("Base", "base_color", 1, 0),
                texture("Normal", "normal", 2, 1),
                texture("Rough", "roughness", 3, 0),
                texture("Unknown", "wetness_mask", 4, 1),
                texture("MissingUV", "metallic", 5, 2),
                FModelMaterialParameter(name="Tint", kind=3, semantic="tint", value=(1.0, 0.5, 0.0, 1.0)),
            ],
        ),
    ])
    warnings = []
    build_fmodel_materials(
        mesh,
        lod_materials,
        links,
        images,
        warnings.append,
        source_slot_to_blender_index={1: 1},
    )

    assert mesh["fmodel_material_links"] == raw_metadata
    assert [
        (node.bl_idname, node.name, node.label, tuple(node.location))
        for node in untouched.node_tree.nodes
    ] == untouched_snapshot
    assert untouched_node.outputs[0].default_value == 0.75
    material = mesh.materials[1]
    assert material.use_nodes
    assert material.name == "M_Body (FModel)"
    provenance = json.loads(material["fmodel_material_link"])
    assert provenance == {
        "version": 1,
        "slot_index": 1,
        "source_material_index": None,
        "slot_name": "DoesNotMatchMaterialName",
        "unreal_material_path": "/Game/Materials/M_Body.M_Body",
        "material_json_uri": "materials/body.json",
        "parameters": provenance["parameters"],
    }
    assert [parameter["name"] for parameter in provenance["parameters"]] == [
        "Base", "Normal", "Rough", "Unknown", "MissingUV", "Tint"
    ]
    assert json.loads(material["fmodel_parameter_metadata"])[-1]["name"] == "Tint"
    assert any("MissingUV" in warning and "UV2" in warning for warning in warnings)

    tree = material.node_tree
    principled = next(node for node in tree.nodes if node.type == "BSDF_PRINCIPLED")
    output = next(node for node in tree.nodes if node.type == "OUTPUT_MATERIAL")
    base = next(node for node in tree.nodes if node.label == "Base")
    normal = next(node for node in tree.nodes if node.label == "Normal")
    rough = next(node for node in tree.nodes if node.label == "Rough")
    unknown = next(node for node in tree.nodes if node.label == "Unknown")
    normal_map = next(node for node in tree.nodes if node.type == "NORMAL_MAP")
    frame = next(node for node in tree.nodes if node.type == "FRAME")
    uv_nodes = {node.uv_map for node in tree.nodes if node.type == "UVMAP"}
    assert has_link(tree, principled, principled.outputs[0], output, output.inputs[0])
    assert has_link(tree, base, base.outputs[0], principled, principled.inputs[0])
    assert has_link(tree, normal, normal.outputs[0], normal_map, normal_map.inputs[1])
    assert has_link(tree, normal_map, normal_map.outputs[0], principled, principled.inputs[6])
    assert has_link(tree, rough, rough.outputs[0], principled, principled.inputs[2])
    assert unknown.parent.name == frame.name
    assert frame.label == "FModel Unmapped Parameters"
    assert not any(link.from_node == unknown and link.to_node == principled for link in tree.links)
    assert not any(node.label == "MissingUV" for node in tree.nodes)
    assert uv_nodes == {"UV0", "UV1"}

print("FModel material builder smoke passed")
