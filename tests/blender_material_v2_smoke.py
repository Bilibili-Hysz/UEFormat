import json
import sys
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
    FModelPBRInterface,
    Material,
    TextureResource,
)
from io_scene_ueformat.importer.material_builder import build_fmodel_materials  # noqa: E402
from io_scene_ueformat.importer.material_builder import FModelMaterialImportOptions  # noqa: E402


def texture(name, texture_id):
    return FModelMaterialParameter(
        name=name,
        kind=1,
        semantic="deliberately_wrong_semantic",
        value=TextureResource(
            id=texture_id,
            unreal_object_path=f"/Game/Textures/{name}.{name}",
            location_mode=1,
            uri=f"{texture_id}.png",
            uv_set=0,
        ),
    )


def scalar(name, value):
    return FModelMaterialParameter(name=name, kind=2, semantic="wrong", value=value)


def vector(name, value):
    return FModelMaterialParameter(name=name, kind=3, semantic="wrong", value=value)


class Images:
    def __init__(self):
        self.calls = []

    def resource_cache_key(self, parameter, interpretation):
        return (parameter.texture.unreal_object_path, parameter.texture.id, interpretation)

    def get_image(self, parameter, interpretation):
        self.calls.append((parameter.name, interpretation))
        return bpy.data.images.new(f"{parameter.name} {interpretation}", 1, 1)


mesh = bpy.data.meshes.new("FModelV2GraphSmoke")
mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
mesh.uv_layers.new(name="UV0")
for index in range(4):
    mesh.materials.append(bpy.data.materials.new(f"Unused V2 Slot {index}"))
material = bpy.data.materials.new("V2 Direct Principled")
mesh.materials.append(material)
parameters = [
    texture("Odd_Base_Texture", 1),
    vector("Odd_Base_Factor", (0.5, 0.6, 0.7, 1.0)),
    texture("Odd_Normal", 2),
    texture("Odd_Emissive", 3),
    vector("Odd_Emissive_Factor", (1.0, 0.25, 0.5, 1.0)),
    vector("Odd_Emissive_Strength", (2.5, 0.0, 0.0, 0.0)),
    texture("Odd_AO", 4),
    vector("Odd_AO_Boost", (0.8, 0.9, 1.0, 1.0)),
    scalar("Odd_AO_Strength", 0.75),
    scalar("Odd_Rough_Min", 0.2),
    scalar("Odd_Rough_Max", 0.6),
    texture("Preserved_Custom_Texture", 5),
    scalar("Visible_Untextured_Base", 0.4),
]
role_records = [
    (0, 0, 1), (0, 1, 2), (0, 2, 3), (0, 3, 4), (0, 4, 5), (0, 5, 6),
    (0, 6, 10), (0, 7, 11), (0, 8, 12), (0, 9, 7), (0, 10, 8),
    (0, 12, 2),
]
links = FModelMaterialLinks(
    slots=[FModelMaterialSlot(4, "V2", "", "", parameters)],
    pbr_interfaces=[FModelPBRInterface(4, record[1], record[2]) for record in role_records],
)
build_fmodel_materials(
    mesh,
    [Material(f"LOD Slot {index}", "", 0, 1) for index in range(5)],
    links,
    Images(),
    print,
)
built_material = mesh.materials[4]

# import_materials=False is a hard builder boundary: no provenance or mutation.
disabled_mesh = bpy.data.meshes.new("FModelImportMaterialsDisabled")
disabled_material = bpy.data.materials.new("Import Materials Disabled")
disabled_mesh.materials.append(disabled_material)
disabled_images = Images()
build_fmodel_materials(
    disabled_mesh,
    [Material("Disabled", "", 0, 1)],
    FModelMaterialLinks(slots=[FModelMaterialSlot(0, "Disabled", "", "", [scalar("Sentinel", 0.5)])]),
    disabled_images,
    lambda message: (_ for _ in ()).throw(AssertionError(message)),
    FModelMaterialImportOptions(import_materials=False),
)
assert list(disabled_mesh.materials) == [disabled_material]
assert "fmodel_material_link" not in disabled_material
assert "fmodel_parameter_metadata" not in disabled_material
assert disabled_images.calls == []

# Provenance must not depend on node construction.
metadata_mesh = bpy.data.meshes.new("FModelMetadataOnly")
metadata_material = bpy.data.materials.new("Metadata Only")
metadata_mesh.materials.append(metadata_material)
build_fmodel_materials(
    metadata_mesh,
    [Material("Metadata", "", 0, 1)],
    FModelMaterialLinks(slots=[FModelMaterialSlot(0, "Metadata", "", "", [scalar("Value", 0.25)])]),
    Images(),
    print,
    FModelMaterialImportOptions(build_nodes=False),
)
assert json.loads(metadata_material["fmodel_material_link"])["slot_index"] == 0
assert json.loads(metadata_material["fmodel_parameter_metadata"])[0]["name"] == "Value"

# A graph owned by the user must remain untouched when importer construction fails.
protected_mesh = bpy.data.meshes.new("FModelProtectedGraph")
protected_mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
protected_mesh.uv_layers.new(name="UV0")
protected_material = bpy.data.materials.new("Protected User Graph")
protected_material.use_nodes = True
protected_node = protected_material.node_tree.nodes.new("ShaderNodeValue")
protected_node.label = "User sentinel"
protected_material["user_custom_property"] = "must remain unchanged"
protected_mesh.materials.append(protected_material)
build_fmodel_materials(
    protected_mesh,
    [Material("Protected", "", 0, 1)],
    FModelMaterialLinks(slots=[FModelMaterialSlot(0, "Protected", "", "", [texture("Broken Protected", 98)])]),
    type("BrokenImages", (), {
        "resource_cache_key": lambda *_args: (_ for _ in ()).throw(OSError("broken")),
        "get_image": lambda *_args: (_ for _ in ()).throw(OSError("broken")),
    })(),
    print,
)
assert protected_mesh.materials[0] is protected_material
assert protected_material.node_tree.nodes.get(protected_node.name) == protected_node
assert protected_material["user_custom_property"] == "must remain unchanged"
assert "fmodel_material_link" not in protected_material
assert "fmodel_parameter_metadata" not in protected_material

# Node builds isolate user-owned datablocks before attaching importer provenance.
owned_mesh = bpy.data.meshes.new("FModelProvenanceOwnership")
owned_mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
owned_mesh.uv_layers.new(name="UV0")
owned_material = bpy.data.materials.new("User Owned Provenance")
owned_material.use_nodes = True
owned_sentinel = owned_material.node_tree.nodes.new("ShaderNodeValue")
owned_sentinel.label = "User provenance sentinel"
owned_material["user_custom_property"] = "preserve this"
owned_mesh.materials.append(owned_material)
build_fmodel_materials(
    owned_mesh,
    [Material("Owned", "", 0, 1)],
    FModelMaterialLinks(slots=[FModelMaterialSlot(0, "Owned", "", "", [scalar("Value", 0.5)])]),
    Images(),
    print,
)
assert owned_mesh.materials[0] is not owned_material
assert owned_material.node_tree.nodes.get(owned_sentinel.name) == owned_sentinel
assert owned_material["user_custom_property"] == "preserve this"
assert "fmodel_material_link" not in owned_material
assert "fmodel_parameter_metadata" not in owned_material
assert json.loads(owned_mesh.materials[0]["fmodel_material_link"])["slot_index"] == 0

# A pristine default graph is still user-owned until the importer created it.
default_mesh = bpy.data.meshes.new("FModelDefaultGraphOwnership")
default_mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
default_mesh.uv_layers.new(name="UV0")
default_material = bpy.data.materials.new("User Default Graph")
default_material.use_nodes = True
default_material["user_custom_property"] = "preserve default graph"
default_snapshot = [
    (node.bl_idname, node.name, node.label, tuple(node.location))
    for node in default_material.node_tree.nodes
]
default_mesh.materials.append(default_material)
build_fmodel_materials(
    default_mesh,
    [Material("Default", "", 0, 1)],
    FModelMaterialLinks(slots=[FModelMaterialSlot(0, "Default", "", "", [scalar("Value", 0.5)])]),
    Images(),
    print,
)
assert default_mesh.materials[0] is not default_material
assert [
    (node.bl_idname, node.name, node.label, tuple(node.location))
    for node in default_material.node_tree.nodes
] == default_snapshot
assert default_material["user_custom_property"] == "preserve default graph"
assert "fmodel_material_link" not in default_material
assert "fmodel_parameter_metadata" not in default_material
assert default_mesh.materials[0]["fmodel_importer_owned"] is True

# Materials without node graphs are also user-owned and must be rebound first.
no_nodes_mesh = bpy.data.meshes.new("FModelNoNodesOwnership")
no_nodes_mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
no_nodes_mesh.uv_layers.new(name="UV0")
no_nodes_material = bpy.data.materials.new("User No Nodes")
# Blender 5.2 keeps a node tree even after this deprecated legacy assignment.
no_nodes_material.use_nodes = False
no_nodes_material["user_custom_property"] = "preserve no-node material"
no_nodes_snapshot = [
    (node.bl_idname, node.name, node.label, tuple(node.location))
    for node in no_nodes_material.node_tree.nodes
]
no_nodes_mesh.materials.append(no_nodes_material)
build_fmodel_materials(
    no_nodes_mesh,
    [Material("No Nodes", "", 0, 1)],
    FModelMaterialLinks(slots=[FModelMaterialSlot(0, "No Nodes", "", "", [scalar("Value", 0.5)])]),
    Images(),
    print,
)
assert no_nodes_mesh.materials[0] is not no_nodes_material
assert [
    (node.bl_idname, node.name, node.label, tuple(node.location))
    for node in no_nodes_material.node_tree.nodes
] == no_nodes_snapshot
assert no_nodes_material["user_custom_property"] == "preserve no-node material"
assert "fmodel_material_link" not in no_nodes_material
assert "fmodel_parameter_metadata" not in no_nodes_material
assert no_nodes_mesh.materials[0]["fmodel_importer_owned"] is True

tree = built_material.node_tree
principled = next(node for node in tree.nodes if node.type == "BSDF_PRINCIPLED")
frames = [node for node in tree.nodes if node.type == "FRAME"]
assert len(frames) == 2
custom_frame = next(node for node in frames if node.label == "Custom / Unmapped (Preserved)")
pbr_frame = next(node for node in frames if node.label == "PBR Inputs")
assert custom_frame.location.x < pbr_frame.location.x < principled.location.x
assert all(node.shrink for node in frames)
assert custom_frame.use_custom_color and pbr_frame.use_custom_color

custom = next(node for node in tree.nodes if node.label == "Preserved_Custom_Texture")
assert custom.parent == custom_frame
assert not any(link.from_node == custom for link in tree.links)
custom_uv_nodes = [node for node in tree.nodes if node.type == "UVMAP" and node.parent == custom_frame]
pbr_uv_nodes = [node for node in tree.nodes if node.type == "UVMAP" and node.parent == pbr_frame]
assert len(custom_uv_nodes) == 1
assert len(pbr_uv_nodes) == 1
assert custom_uv_nodes[0].uv_map == "UV0"
assert pbr_uv_nodes[0].uv_map == "UV0"
assert custom.location.y == 0
pbr_source_textures = [
    node for node in tree.nodes if node.type == "TEX_IMAGE" and node.parent == pbr_frame
]
assert pbr_source_textures[0].location.y == 0
assert any(
    abs(left.location.y - right.location.y) == 320
    for index, left in enumerate(pbr_source_textures)
    for right in pbr_source_textures[index + 1:]
)
assert all(
    any(link.from_node == pbr_uv_nodes[0] and link.to_node == node for link in tree.links)
    for node in tree.nodes
    if node.parent == pbr_frame and node.type == "TEX_IMAGE"
)
assert any(
    link.from_node == custom_uv_nodes[0] and link.to_node == custom
    for link in tree.links
)
for name in (
    "Odd_Base_Texture", "Odd_Base_Factor", "Odd_Normal", "Odd_Emissive",
    "Odd_Emissive_Factor", "Odd_Emissive_Strength", "Odd_AO", "Odd_AO_Boost",
    "Odd_AO_Strength", "Odd_Rough_Min", "Odd_Rough_Max", "Visible_Untextured_Base",
):
    assert next(node for node in tree.nodes if node.label == name).parent == pbr_frame

incoming = {link.to_socket: link.from_node for link in tree.links if link.to_node == principled}
assert principled.inputs[0] in incoming
assert principled.inputs[2] in incoming
assert principled.inputs[6] in incoming
assert principled.inputs[28] in incoming
assert principled.inputs[29] in incoming
assert incoming[principled.inputs[6]].type == "NORMAL_MAP"
assert incoming[principled.inputs[2]].type == "VALUE"
assert abs(incoming[principled.inputs[2]].outputs[0].default_value - 0.4) < 1e-6
assert incoming[principled.inputs[29]].outputs[0].default_value == 2.5
role_y = {
    name: next(node for node in tree.nodes if node.label == name).location.y
    for name in ("Odd_Base_Texture", "Odd_Normal", "Odd_AO", "Odd_Emissive", "Odd_Rough_Min")
}
assert role_y["Odd_Base_Texture"] > role_y["Odd_Normal"] > role_y["Odd_AO"] > role_y["Odd_Emissive"] > role_y["Odd_Rough_Min"]
processing = [node for node in tree.nodes if node.parent == pbr_frame and node.bl_idname in {"ShaderNodeMix", "ShaderNodeNormalMap"}]
processing.extend(node for node in tree.nodes if node.parent == pbr_frame and node.label == "Roughness Midpoint")
assert processing
for label in ("Base Factor Multiply", "AO Factor Multiply", "AO Multiply", "Emissive Factor Multiply", "Normal Map", "Roughness Midpoint"):
    assert next(node for node in processing if node.label == label)
assert all(node.location.x > 0 for node in processing)
assert all(node.location.x < principled.location.x for node in processing)
assert len({node.location.y for node in processing}) == len(processing)
assert all(abs(left.location.y - right.location.y) >= 160 for index, left in enumerate(processing) for right in processing[index + 1:])
assert tree.nodes.active.label == "Odd_Base_Texture"
assert all(node.location.x == 260 for node in processing)
assert all(abs(node.location.x - pbr_uv_nodes[0].location.x) <= 300 for node in pbr_uv_nodes)
for label in ("Odd_AO_Strength", "Odd_Emissive_Strength"):
    helper = next(node for node in tree.nodes if node.label == label and node.type == "VALUE")
    assert helper.parent == pbr_frame
    assert any(link.from_node == helper for link in tree.links)
    assert any(link.from_node == helper for link in tree.links)

mix_nodes = [node for node in tree.nodes if node.bl_idname == "ShaderNodeMix"]
assert len(mix_nodes) >= 4
assert all(node.blend_type == "MULTIPLY" for node in mix_nodes)
assert any(
    link.from_node.label == "Odd_AO" and link.to_node in mix_nodes
    for link in tree.links
)
assert all(parameter.name in {node.label for node in tree.nodes} for parameter in parameters)

# Serialized material ordinals are sparse and must bind to their original mesh slots.
sparse_mesh = bpy.data.meshes.new("FModelSparseOrdinalSmoke")
sparse_mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
sparse_mesh.uv_layers.new(name="UV0")
sparse_materials = [bpy.data.materials.new(f"Sparse {index}") for index in range(3)]
for sparse_material in sparse_materials:
    sparse_mesh.materials.append(sparse_material)
sparse_middle = sparse_materials[1]
sparse_middle.use_nodes = True
sparse_middle_node = sparse_middle.node_tree.nodes.new("ShaderNodeValue")
sparse_middle_node.label = "Sparse middle sentinel"
sparse_middle_node.outputs[0].default_value = 0.625
sparse_middle_snapshot = [
    (node.bl_idname, node.name, node.label, tuple(node.location))
    for node in sparse_middle.node_tree.nodes
]
sparse_slots = [
    FModelMaterialSlot(0, "Zero", "", "", [scalar("Slot Zero", 0.0)]),
    FModelMaterialSlot(2, "Two", "", "", [scalar("Slot Two", 1.0)]),
]
build_fmodel_materials(
    sparse_mesh,
    [Material("LOD 0", "", 0, 1), Material("LOD 1", "", 0, 1), Material("LOD 2", "", 0, 1)],
    FModelMaterialLinks(slots=sparse_slots),
    Images(),
    print,
    source_slot_to_blender_index={0: 0, 2: 2},
)
assert sparse_materials[0].use_nodes
assert sparse_middle.use_nodes
assert [
    (node.bl_idname, node.name, node.label, tuple(node.location))
    for node in sparse_middle.node_tree.nodes
] == sparse_middle_snapshot
assert sparse_middle_node.outputs[0].default_value == 0.625
sparse_built = sparse_mesh.materials[2]
assert sparse_built is not sparse_materials[2]
assert sparse_built.use_nodes
assert json.loads(sparse_built["fmodel_material_link"])["slot_index"] == 2

ao_mix = next(node for node in mix_nodes if node.label == "AO Multiply")
ao_strength = next(node for node in tree.nodes if node.label == "Odd_AO_Strength")
assert any(
    link.from_node == ao_strength and link.to_node == ao_mix and link.to_socket == ao_mix.inputs[0]
    for link in tree.links
)
assert ao_mix.inputs[0].default_value == 0.0
for value in (0.0, 0.5, 1.0):
    ao_mix.inputs[0].default_value = value
    assert ao_mix.inputs[0].default_value == value

# AO modifiers are inert without a resolvable explicit AO source (role 10).
no_ao_mesh = bpy.data.meshes.new("FModelNoAoSourceSmoke")
no_ao_mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
no_ao_mesh.uv_layers.new(name="UV0")
no_ao_material = bpy.data.materials.new("V2 No AO Source")
no_ao_mesh.materials.append(no_ao_material)
no_ao_parameters = [
    texture("No_AO_Base_Texture", 21),
    vector("No_AO_Base_Factor", (0.4, 0.5, 0.6, 1.0)),
    vector("No_AO_Boost", (0.8, 0.9, 1.0, 1.0)),
    scalar("No_AO_Strength", 0.75),
]
build_fmodel_materials(
    no_ao_mesh,
    [Material("No AO LOD", "", 0, 1)],
    FModelMaterialLinks(
        slots=[FModelMaterialSlot(0, "No AO", "", "", no_ao_parameters)],
        pbr_interfaces=[
            FModelPBRInterface(0, 0, 1),
            FModelPBRInterface(0, 1, 2),
            FModelPBRInterface(0, 2, 11),
            FModelPBRInterface(0, 3, 12),
        ],
    ),
    Images(),
    print,
)
no_ao_tree = no_ao_mesh.materials[0].node_tree
no_ao_principled = next(node for node in no_ao_tree.nodes if node.type == "BSDF_PRINCIPLED")
no_ao_mix_nodes = [node for node in no_ao_tree.nodes if node.bl_idname == "ShaderNodeMix"]
no_ao_base_mix = next(node for node in no_ao_mix_nodes if node.label == "Base Factor Multiply")
no_ao_strength = next(node for node in no_ao_tree.nodes if node.label == "No_AO_Strength")
assert not any(node.label == "AO Multiply" for node in no_ao_mix_nodes)
assert not any(link.from_node == no_ao_strength for link in no_ao_tree.links)
assert any(
    link.from_node == no_ao_base_mix and link.to_node == no_ao_principled and link.to_socket == no_ao_principled.inputs[0]
    for link in no_ao_tree.links
)


def build_fixture(name, parameters, interfaces, uv=True, image_failure=False, warning_messages=None):
    fixture_mesh = bpy.data.meshes.new(f"{name} Mesh")
    fixture_mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    if uv:
        fixture_mesh.uv_layers.new(name="UV0")
    fixture_material = bpy.data.materials.new(name)
    fixture_mesh.materials.append(fixture_material)

    class FixtureImages(Images):
        def get_image(self, parameter, interpretation):
            if image_failure:
                raise OSError("fixture image failure")
            return super().get_image(parameter, interpretation)

    build_fmodel_materials(
        fixture_mesh,
        [Material(name, "", 0, 1)],
        FModelMaterialLinks(
            slots=[FModelMaterialSlot(0, name, "", "", parameters)],
            pbr_interfaces=[FModelPBRInterface(0, index, role) for index, role in interfaces],
        ),
        FixtureImages(),
        (warning_messages if warning_messages is not None else []).append,
    )
    return fixture_mesh.materials[0].node_tree


# Texture-backed scalar roles stay Custom even when packing is nonzero: no producer
# packing/channel contract exists, so routing must not infer one.
scalar_role_tree = build_fixture(
    "V2 Unknown Scalar Packing",
    [texture("Unknown Roughness", 99)],
    [(0, 9)],
)
unknown_roughness = next(node for node in scalar_role_tree.nodes if node.label == "Unknown Roughness")
unknown_principled = next(node for node in scalar_role_tree.nodes if node.type == "BSDF_PRINCIPLED")
assert not any(link.from_node == unknown_roughness and link.to_node == unknown_principled for link in scalar_role_tree.links)

packed_parameter = texture("Packed But Unroutable", 100)
packed_parameter.texture.packing = 7
packed_warnings = []
packed_tree = build_fixture("V2 Nonzero Packing", [packed_parameter], [(0, 9)], warning_messages=packed_warnings)
packed_node = next(node for node in packed_tree.nodes if node.label == "Packed But Unroutable")
packed_custom = next(node for node in packed_tree.nodes if node.label == "Custom / Unmapped (Preserved)")
assert packed_node.parent == packed_custom
assert not any(link.from_node == packed_node for link in packed_tree.links)
assert any("role 9" in warning for warning in packed_warnings)

# Reject incompatible role-kind pairs before any Principled input is wired,
# while retaining the original parameter nodes and serialized provenance.
invalid_warnings = []
invalid_tree = build_fixture(
    "V2 Invalid Role Kinds",
    [
        scalar("Invalid Scalar BaseColor", 0.25),
        scalar("Invalid Scalar Normal", 0.5),
        vector("Invalid Vector Normal", (0.1, 0.2, 0.3, 1.0)),
        texture("Invalid Texture Base Factor", 102),
        texture("Invalid Texture Roughness", 103),
    ],
    [(0, 1), (1, 3), (2, 3), (3, 2), (4, 9)],
    warning_messages=invalid_warnings,
)
invalid_principled = next(node for node in invalid_tree.nodes if node.type == "BSDF_PRINCIPLED")
invalid_custom = next(node for node in invalid_tree.nodes if node.label == "Custom / Unmapped (Preserved)")
assert not any(link.to_node == invalid_principled for link in invalid_tree.links)
for name in (
    "Invalid Scalar BaseColor", "Invalid Scalar Normal", "Invalid Vector Normal",
    "Invalid Texture Base Factor", "Invalid Texture Roughness",
):
    node = next(node for node in invalid_tree.nodes if node.label == name)
    assert node.parent == invalid_custom
    assert not any(link.from_node == node and link.to_node == invalid_principled for link in invalid_tree.links)
assert all(any(name in warning and f"role {role}" in warning for warning in invalid_warnings) for name, role in (
    ("Invalid Scalar BaseColor", 1), ("Invalid Scalar Normal", 3),
    ("Invalid Vector Normal", 3), ("Invalid Texture Base Factor", 2),
    ("Invalid Texture Roughness", 9),
))

# A parameter carrying both color and data roles gets distinct source nodes.
dual_tree = build_fixture(
    "V2 Dual Interpretation",
    [texture("BaseColor Normal", 101)],
    [(0, 1), (0, 3)],
)
dual_nodes = [node for node in dual_tree.nodes if node.type == "TEX_IMAGE"]
assert len(dual_nodes) == 2
assert {node.image.name.rsplit(" ", 1)[-1].split(".", 1)[0] for node in dual_nodes} == {"COLOR", "DATA"}
assert len({node.image.name for node in dual_nodes}) == 2


fallback_tree = build_fixture(
    "V2 PBR Fallback",
    [texture("Fallback_Normal", 31), texture("Fallback_Emissive", 32)],
    [(0, 3), (1, 4)],
)
assert fallback_tree.nodes.active.label == "Fallback_Normal"

unchanged_material = bpy.data.materials.new("V2 Active Unchanged")
unchanged_material.use_nodes = True
original_active = unchanged_material.node_tree.nodes.new("ShaderNodeValue")
original_active.label = "Original active node"
unchanged_material.node_tree.nodes.active = original_active
unchanged_mesh = bpy.data.meshes.new("V2 Active Unchanged Mesh")
unchanged_mesh.materials.append(unchanged_material)
build_fmodel_materials(
    unchanged_mesh,
    [Material("V2 Active Unchanged", "", 0, 1)],
    FModelMaterialLinks(
        slots=[FModelMaterialSlot(0, "unchanged", "", "", [texture("Broken", 33)])],
        pbr_interfaces=[FModelPBRInterface(0, 0, 3)],
    ),
    type("BrokenImages", (), {
        "resource_cache_key": lambda *_args: (_ for _ in ()).throw(OSError("broken")),
        "get_image": lambda *_args: (_ for _ in ()).throw(OSError("broken")),
    })(),
    print,
)
assert unchanged_mesh.materials[0] is unchanged_material
assert unchanged_material.node_tree.nodes.active == original_active
assert "fmodel_material_link" not in unchanged_material
assert "fmodel_parameter_metadata" not in unchanged_material

for fixture_name, kwargs in (("V2 Missing UV", {"uv": False}), ("V2 Image Failure", {"image_failure": True})):
    failed_tree = build_fixture(
        fixture_name,
        [texture(f"{fixture_name} Texture", 34)],
        [(0, 1)],
        **kwargs,
    )
    assert not any(node.type == "UVMAP" for node in failed_tree.nodes)

print("FMODEL_V2_DIRECT_PRINCIPLED_OK")
