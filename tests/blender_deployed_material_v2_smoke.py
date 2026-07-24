from pathlib import Path

import addon_utils
import bpy


MANAGED_ADDON = Path(
    r"C:\Users\86177\AppData\Roaming\Blender Foundation\Blender\5.2\scripts\addons\io_scene_ueformat"
)


class Images:
    def resource_cache_key(self, parameter, interpretation):
        return (parameter.texture.unreal_object_path, parameter.texture.id, interpretation)

    def get_image(self, parameter, interpretation):
        return bpy.data.images.new(parameter.name, 1, 1)


assert MANAGED_ADDON.is_dir()
addon_utils.enable("io_scene_ueformat", default_set=False, persistent=False)
try:
    from io_scene_ueformat.importer import material_builder
    from io_scene_ueformat.importer.classes import (
        FModelMaterialLinks,
        FModelMaterialParameter,
        FModelMaterialSlot,
        FModelPBRInterface,
        Material,
        TextureResource,
    )

    assert Path(material_builder.__file__).parents[1] == MANAGED_ADDON

    mesh = bpy.data.meshes.new("DeployedV2Smoke")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    mesh.uv_layers.new(name="UV0")
    material = bpy.data.materials.new("Deployed V2 Material")
    mesh.materials.append(material)
    parameters = [
        FModelMaterialParameter(
            name="V2 Base Texture",
            kind=1,
            semantic="custom",
            value=TextureResource(1, "/Game/V2Base.V2Base", 1, "v2-base.png", uv_set=0),
        ),
        FModelMaterialParameter("V2 Factor", 3, "custom", (0.5, 0.5, 0.5, 1.0)),
        FModelMaterialParameter(
            name="V2 Normal Texture",
            kind=1,
            semantic="custom",
            value=TextureResource(2, "/Game/V2Normal.V2Normal", 1, "v2-normal.png", uv_set=0),
        ),
        FModelMaterialParameter(
            name="V2 Preserved Custom Texture",
            kind=1,
            semantic="custom",
            value=TextureResource(3, "/Game/V2Custom.V2Custom", 1, "v2-custom.png", uv_set=0),
        ),
    ]
    links = FModelMaterialLinks(
        slots=[FModelMaterialSlot(0, "V2", "", "", parameters=parameters)],
        pbr_interfaces=[
            FModelPBRInterface(0, 0, 1),
            FModelPBRInterface(0, 1, 2),
            FModelPBRInterface(0, 2, 3),
        ],
    )
    material_builder.build_fmodel_materials(
        mesh,
        [Material("V2", "", 0, 1)],
        links,
        Images(),
        print,
        source_slot_to_blender_index={0: 0},
    )

    built_material = mesh.materials[0]
    assert built_material is not material
    assert "fmodel_material_link" not in material
    tree = built_material.node_tree
    frames = {node.label: node for node in tree.nodes if node.type == "FRAME"}
    assert set(frames) == {"Custom / Unmapped (Preserved)", "PBR Inputs"}
    principled = next(node for node in tree.nodes if node.type == "BSDF_PRINCIPLED")
    assert any(link.to_node == principled and link.to_socket == principled.inputs[0] for link in tree.links)
    custom_frame = frames["Custom / Unmapped (Preserved)"]
    pbr_frame = frames["PBR Inputs"]
    custom_uv = [node for node in tree.nodes if node.type == "UVMAP" and node.parent == custom_frame]
    pbr_uv = [node for node in tree.nodes if node.type == "UVMAP" and node.parent == pbr_frame]
    assert len(custom_uv) == 1 and custom_uv[0].uv_map == "UV0"
    assert len(pbr_uv) == 1 and pbr_uv[0].uv_map == "UV0"
    for uv_node, frame in ((custom_uv[0], custom_frame), (pbr_uv[0], pbr_frame)):
        textures = [node for node in tree.nodes if node.type == "TEX_IMAGE" and node.parent == frame]
        assert textures
        assert all(uv_node.location.x < texture.location.x for texture in textures)
        assert all(texture.location.x - uv_node.location.x <= 300 for texture in textures)
    custom_texture = next(node for node in tree.nodes if node.label == "V2 Preserved Custom Texture")
    pbr_texture = next(node for node in tree.nodes if node.label == "V2 Base Texture")
    assert custom_texture.location.y == 0
    assert pbr_texture.location.y == 0
    pbr_textures = [node for node in tree.nodes if node.type == "TEX_IMAGE" and node.parent == pbr_frame]
    assert any(
        abs(left.location.y - right.location.y) == 320
        for index, left in enumerate(pbr_textures)
        for right in pbr_textures[index + 1:]
    )
    base_texture = next(node for node in tree.nodes if node.label == "V2 Base Texture")
    assert tree.nodes.active == base_texture
    base_processing = next(node for node in tree.nodes if node.label == "Base Factor Multiply")
    assert any(link.from_node == base_texture and link.to_node == base_processing for link in tree.links)
    assert any(link.from_node == base_processing and link.to_node == principled for link in tree.links)
    processing = [
        node for node in tree.nodes
        if node.parent == pbr_frame and node.bl_idname in {"ShaderNodeMix", "ShaderNodeNormalMap"}
    ]
    assert all(
        abs(left.location.y - right.location.y) >= 160
        for index, left in enumerate(processing)
        for right in processing[index + 1:]
    )
finally:
    addon_utils.disable("io_scene_ueformat", default_set=False)

print("DEPLOYED_FMODEL_V2_MATERIAL_OK")
