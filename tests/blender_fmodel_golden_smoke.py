import json
import base64
from pathlib import Path

import addon_utils
import bpy


repository = Path(__file__).parents[1]
fixture = repository / "tests" / "fixtures" / "fmodel_golden.uemodel"
managed_addon = Path(
    r"C:\Users\86177\AppData\Roaming\Blender Foundation\Blender\5.2\scripts\addons\io_scene_ueformat"
)


def has_link(tree, from_node, from_socket, to_node, to_socket):
    return any(
        link.from_node == from_node
        and link.from_socket == from_socket
        and link.to_node == to_node
        and link.to_socket == to_socket
        for link in tree.links
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


def import_through_operator():
    before = set(bpy.data.objects)
    result = bpy.ops.uf.import_uemodel(
        directory=str(fixture.parent),
        files=[{"name": fixture.name}],
    )
    assert result == {"FINISHED"}
    imported = next(obj for obj in bpy.data.objects if obj not in before and obj.type == "MESH")
    return imported

assert fixture.is_file()
addon_utils.enable("io_scene_ueformat", default_set=False, persistent=False)
try:
    _enabled_by_default, enabled = addon_utils.check("io_scene_ueformat")
    assert enabled
    assert hasattr(bpy.ops.uf, "import_uemodel")
    import io_scene_ueformat.importer.fmodel as fmodel
    assert Path(fmodel.__file__).parents[1] == managed_addon

    settings = bpy.context.scene.uf_settings
    assert settings.import_fmodel_materials is True
    assert settings.build_fmodel_material_nodes is True
    assert settings.load_fmodel_embedded_textures is True
    assert settings.load_fmodel_external_textures is True
    assert settings.keep_fmodel_unmapped_parameters is True
    assert settings.fmodel_ue_export_root == ""
    assert settings.fmodel_bundle_root == ""

    imported = import_through_operator()
    metadata = json.loads(imported.data["fmodel_material_links"])
    assert [slot["slot_index"] for slot in metadata["slots"]] == [0, 1]
    assert [parameter["name"] for parameter in metadata["slots"][0]["parameters"]] == [
        "BaseColor", "Normal", "WetnessMask", "Roughness", "Tint",
    ]
    assert metadata["slots"][1]["parameters"][0]["value"]["texture_id"] == 1
    assert len(imported.data.vertices) == 3
    assert len(imported.data.materials) == 2

    material = imported.data.materials[0]
    provenance = json.loads(material["fmodel_material_link"])
    assert provenance["slot_index"] == 0
    assert [parameter["name"] for parameter in provenance["parameters"]] == [
        "BaseColor", "Normal", "WetnessMask", "Roughness", "Tint",
    ]

    tree = material.node_tree
    principled = next(node for node in tree.nodes if node.type == "BSDF_PRINCIPLED")
    output = next(node for node in tree.nodes if node.type == "OUTPUT_MATERIAL")
    assert any(node.label == "BaseColor" for node in tree.nodes)
    normal = next(node for node in tree.nodes if node.label == "Normal")
    wetness_mask = next(node for node in tree.nodes if node.label == "WetnessMask")
    normal_map = next(node for node in tree.nodes if node.type == "NORMAL_MAP")
    unmapped = next(node for node in tree.nodes if node.label == "FModel Unmapped Parameters")
    assert has_link(tree, principled, principled.outputs[0], output, output.inputs[0])
    assert has_link(tree, normal, normal.outputs[0], normal_map, normal_map.inputs[1])
    assert has_link(tree, normal_map, normal_map.outputs[0], principled, principled.inputs[6])
    assert wetness_mask.parent is not None
    assert wetness_mask.parent.name == unmapped.name
    assert not any(
        link.from_node.name == wetness_mask.name and link.to_node.name == principled.name
        for link in tree.links
    )

    duplicate_import = import_through_operator()
    assert duplicate_import.data.materials[0] is material
    assert duplicate_import.data.materials[1] is imported.data.materials[1]
    assert len([item for item in bpy.data.materials if item.name.startswith("M_GoldenBody")]) == 1
    assert len([item for item in bpy.data.materials if item.name.startswith("M_GoldenOverlay")]) == 1

    clear_imported_data()
    settings.import_fmodel_materials = False
    imported = import_through_operator()
    assert "WetnessMask" in imported.data["fmodel_material_links"]
    assert len(imported.data.materials) == 2
    assert all("fmodel_material_link" not in material for material in imported.data.materials)

    clear_imported_data()
    settings.import_fmodel_materials = True
    settings.load_fmodel_external_textures = False
    from io_scene_ueformat.importer import logic
    from io_scene_ueformat.importer.classes import FModelMaterialParameter, TextureResource

    warnings = []
    original_warn = logic.Log.warn
    original_import_uemodel_data = logic.UEFormatImport.import_uemodel_data

    def inject_external_parameter(self, ar, name, material_options=None):
        original_build = logic.build_fmodel_materials

        def build_with_external_fixture(
            mesh,
            lod_materials,
            material_links,
            images,
            warn,
            options,
            source_slot_to_blender_index,
        ):
            material_links.slots[0].parameters.append(FModelMaterialParameter(
                name="ExternalFixture",
                kind=1,
                semantic="base_color",
                value=TextureResource(
                    id=999,
                    unreal_object_path="",
                    location_mode=1,
                    uri="textures/external-fixture.png",
                ),
            ))
            return original_build(
                mesh,
                lod_materials,
                material_links,
                images,
                warn,
                options,
                source_slot_to_blender_index,
            )

        logic.build_fmodel_materials = build_with_external_fixture
        try:
            return original_import_uemodel_data(self, ar, name, material_options)
        finally:
            logic.build_fmodel_materials = original_build

    logic.Log.warn = warnings.append
    logic.UEFormatImport.import_uemodel_data = inject_external_parameter
    try:
        imported = import_through_operator()
    finally:
        logic.Log.warn = original_warn
        logic.UEFormatImport.import_uemodel_data = original_import_uemodel_data
    assert "WetnessMask" in imported.data["fmodel_material_links"]
    assert any("external textures disabled" in warning for warning in warnings), warnings

    clear_imported_data()
    settings.load_fmodel_external_textures = True
    relative_texture = fixture.parent / "relative-rootless-smoke.png"
    relative_texture.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/"
        "5ncLrgAAAABJRU5ErkJggg=="
    ))
    warnings = []
    original_warn = logic.Log.warn
    original_import_uemodel_data = logic.UEFormatImport.import_uemodel_data

    def inject_rootless_parameter(self, ar, name, material_options=None):
        original_build = logic.build_fmodel_materials

        def build_with_rootless_fixture(
            mesh,
            lod_materials,
            material_links,
            images,
            warn,
            options,
            source_slot_to_blender_index,
        ):
            material_links.slots[0].parameters.append(FModelMaterialParameter(
                name="RootlessRelativeFixture",
                kind=1,
                semantic="base_color",
                value=TextureResource(
                    id=-1,
                    unreal_object_path="",
                    location_mode=1,
                    uri=relative_texture.name,
                ),
            ))
            return original_build(
                mesh,
                lod_materials,
                material_links,
                images,
                warn,
                options,
                source_slot_to_blender_index,
            )

        logic.build_fmodel_materials = build_with_rootless_fixture
        try:
            return original_import_uemodel_data(self, ar, name, material_options)
        finally:
            logic.build_fmodel_materials = original_build

    logic.Log.warn = warnings.append
    logic.UEFormatImport.import_uemodel_data = inject_rootless_parameter
    try:
        imported = import_through_operator()
    finally:
        logic.Log.warn = original_warn
        logic.UEFormatImport.import_uemodel_data = original_import_uemodel_data
        relative_texture.unlink(missing_ok=True)
    assert not [warning for warning in warnings if "FUTURE_GOLDEN" not in warning], warnings
    rootless_material = imported.data.materials[0]
    assert any(node.label == "RootlessRelativeFixture" for node in rootless_material.node_tree.nodes)
finally:
    addon_utils.disable("io_scene_ueformat", default_set=False)
    _enabled_by_default, enabled = addon_utils.check("io_scene_ueformat")
    assert not enabled
    assert not hasattr(bpy.types, "UFImportUEModel")

print("FModel golden Blender smoke passed")
