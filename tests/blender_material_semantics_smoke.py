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
CUSTOM_TEXTURES = ("PM_SpecularMasks", "MaterialMap", "IDMap", "Ramp", "LightingCurves")


def texture(name, semantic, texture_id):
    return FModelMaterialParameter(
        name=name,
        kind=1,
        semantic=semantic,
        value=TextureResource(
            id=texture_id,
            unreal_object_path=f"/Game/Textures/T_{name}.T_{name}",
            location_mode=1,
            uri=f"textures/{texture_id}.png",
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
    for texture_id in range(1, 9):
        (root / "textures" / f"{texture_id}.png").write_bytes(PNG_BYTES)
    model_path = root / "semantic-materials.uemodel"
    resolver = FModelResourceResolver(
        model_path=model_path,
        fmodel_textures=[],
        ue_export_root=root,
        bundle_root=root,
    )
    images = FModelImageRepository(resolver, model_path, root, root)
    mesh = bpy.data.meshes.new("FModelMaterialSemanticsSmoke")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    mesh.uv_layers.new(name="UV0")
    material = bpy.data.materials.new("SemanticMaterial")
    mesh.materials.append(material)
    parameters = [
        texture("AlbedoInput", "base_color", 1),
        texture("NormalInput", "normal", 2),
        texture("GlowInput", "emissive", 3),
        *(texture(name, "custom_parameter", texture_id) for texture_id, name in enumerate(CUSTOM_TEXTURES, 4)),
        FModelMaterialParameter(name="SpecularScale", kind=2, semantic="specular", value=0.4),
        FModelMaterialParameter(name="Tint", kind=3, semantic="tint", value=(1.0, 0.5, 0.0, 1.0)),
    ]
    links = FModelMaterialLinks(slots=[FModelMaterialSlot(
        slot_index=0,
        slot_name="SemanticMaterial",
        unreal_material_path="/Game/Materials/M_Semantic.M_Semantic",
        material_json_uri="materials/semantic.json",
        parameters=parameters,
    )])
    build_fmodel_materials(mesh, [Material("SemanticMaterial", "", 0, 1)], links, images, print)

    material = mesh.materials[0]
    tree = material.node_tree
    principled = next(node for node in tree.nodes if node.type == "BSDF_PRINCIPLED")
    base_color = next(node for node in tree.nodes if node.label == "AlbedoInput")
    normal = next(node for node in tree.nodes if node.label == "NormalInput")
    emissive = next(node for node in tree.nodes if node.label == "GlowInput")
    normal_map = next(node for node in tree.nodes if node.type == "NORMAL_MAP")
    frame = next(node for node in tree.nodes if node.type == "FRAME")
    assert frame.label == "FModel Unmapped Parameters"
    assert [node for node in tree.nodes if node.type == "FRAME"] == [frame]
    assert has_link(tree, base_color, base_color.outputs[0], principled, principled.inputs[0])
    assert has_link(tree, normal, normal.outputs[0], normal_map, normal_map.inputs[1])
    assert has_link(tree, normal_map, normal_map.outputs[0], principled, principled.inputs[6])
    assert has_link(tree, emissive, emissive.outputs[0], principled, principled.inputs[28])

    custom_nodes = [next(node for node in tree.nodes if node.label == name) for name in CUSTOM_TEXTURES]
    assert all(node.type == "TEX_IMAGE" and node.parent == frame for node in custom_nodes)
    assert all(node.image is not None for node in custom_nodes)
    assert {node.label for node in custom_nodes} == set(CUSTOM_TEXTURES)
    assert all(not any(link.from_node == node for link in tree.links) for node in custom_nodes)
    assert not any(
        link.from_node == node and link.to_node == principled
        for node in custom_nodes
        for link in tree.links
    )
    assert [parameter["name"] for parameter in json.loads(material["fmodel_parameter_metadata"])] == [
        "SpecularScale", "Tint"
    ]
    assert not any(node.label in {"SpecularScale", "Tint"} for node in tree.nodes)

print("FModel semantic material Blender smoke passed")
