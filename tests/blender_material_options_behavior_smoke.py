import base64
import json
import sys
import tempfile
from hashlib import sha256
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
from io_scene_ueformat.importer.material_builder import (  # noqa: E402
    FModelMaterialImportOptions,
    build_fmodel_materials,
)
from io_scene_ueformat.importer.material_images import FModelImageRepository  # noqa: E402
from io_scene_ueformat.importer import logic  # noqa: E402
from io_scene_ueformat.options import UEModelOptions  # noqa: E402


def parameter(name, semantic, location_mode):
    return FModelMaterialParameter(
        name=name,
        kind=1,
        semantic=semantic,
        value=TextureResource(id=1, unreal_object_path="", location_mode=location_mode, uri="texture.png"),
    )


def fixture():
    mesh = bpy.data.meshes.new("FModelMaterialOptionsSmoke")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    mesh.uv_layers.new(name="UV0")
    mesh["fmodel_material_links"] = '{"version":1,"raw":"preserved"}'
    material = bpy.data.materials.new("FModelMaterialOptionsSmoke")
    mesh.materials.append(material)
    links = FModelMaterialLinks(slots=[FModelMaterialSlot(
        slot_index=0, slot_name="slot", unreal_material_path="", material_json_uri="",
        parameters=[parameter("Embedded", "base_color", 3), parameter("External", "normal", 1), parameter("WetnessMask", "wetness_mask", 1)],
    )])
    return mesh, material, [Material(material_name=material.name, material_path="", first_index=0, num_faces=1)], links


class Images:
    def __init__(self):
        self.calls = []

    def resource_cache_key(self, parameter, interpretation):
        return (parameter.texture.unreal_object_path, parameter.texture.id, interpretation)

    def get_image(self, item, interpretation):
        self.calls.append(item.name)
        return bpy.data.images.new(item.name, 1, 1)


mesh, material, lod_materials, links = fixture()
sentinel = material.node_tree.nodes.new("ShaderNodeValue")
sentinel.label = "preexisting"
snapshot = [(node.bl_idname, node.name, node.label) for node in material.node_tree.nodes]
build_fmodel_materials(mesh, lod_materials, links, Images(), [].append, FModelMaterialImportOptions(build_nodes=False))
assert [(node.bl_idname, node.name, node.label) for node in material.node_tree.nodes] == snapshot
assert mesh["fmodel_material_links"] == '{"version":1,"raw":"preserved"}'
assert json.loads(json.dumps({"slots": [{"parameters": [{"name": "WetnessMask"}]}]}))["slots"][0]["parameters"][0]["name"] == "WetnessMask"

mesh, material, lod_materials, links = fixture()
warnings, images = [], Images()
build_fmodel_materials(mesh, lod_materials, links, images, warnings.append, FModelMaterialImportOptions(load_embedded=False, load_external=False))
assert images.calls == []
assert any("embedded textures disabled" in warning for warning in warnings)
assert any("external textures disabled" in warning for warning in warnings)

mesh, material, lod_materials, links = fixture()
warnings, images = [], Images()
build_fmodel_materials(mesh, lod_materials, links, images, warnings.append, FModelMaterialImportOptions(keep_unmapped=False))
material = mesh.materials[0]
assert "WetnessMask" in material["fmodel_material_link"]
assert not any(node.type == "FRAME" for node in material.node_tree.nodes)

roots = ("D:/exact ue root", "D:/exact bundle root")
resolver = FModelResourceResolver(model_path="model.ueformat", fmodel_textures=[], ue_export_root=roots[0], bundle_root=roots[1])
assert resolver.ue_export_root == Path(roots[0])
assert resolver.bundle_root == Path(roots[1])


class RecordingImages:
    roots = []

    def __init__(self, resolver, *_args):
        self.resolver = resolver
        self.roots.append((resolver.ue_export_root, resolver.bundle_root))

    def get_image(self, parameter, interpretation):
        self.resolver.resolve_texture(parameter.texture)
        return bpy.data.images.new(parameter.name, 1, 1)

    def resource_cache_key(self, parameter, interpretation):
        resource = self.resolver.resolve_texture(parameter.texture)
        contents = resource if isinstance(resource, bytes) else resource.read_bytes()
        return (parameter.texture.unreal_object_path, sha256(contents).hexdigest(), interpretation)


def import_with_roots(ue_export_root, bundle_root):
    warnings = []
    original_images = logic.FModelImageRepository
    original_warn = logic.Log.warn
    RecordingImages.roots = []
    logic.FModelImageRepository = RecordingImages
    logic.Log.warn = warnings.append
    try:
        imported, _model = logic.UEFormatImport(UEModelOptions(
            fmodel_ue_export_root=ue_export_root,
            fmodel_bundle_root=bundle_root,
        )).import_file(
            REPOSITORY / "tests" / "fixtures" / "fmodel_golden.uemodel"
        )
        return imported, warnings, RecordingImages.roots
    finally:
        logic.FModelImageRepository = original_images
        logic.Log.warn = original_warn


_imported, _warnings, forwarded_roots = import_with_roots("", "")
assert forwarded_roots == [(None, None)]

assert not (REPOSITORY / "tests" / "fixtures" / "texture.png").is_file()
png_bytes = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/"
    "5ncLrgAAAABJRU5ErkJggg=="
)
with tempfile.TemporaryDirectory() as temporary_directory:
    model_path = Path(temporary_directory) / "model.ueformat"
    texture_path = model_path.parent / "texture.png"
    texture_path.write_bytes(png_bytes)
    mesh, material, lod_materials, links = fixture()
    warnings = []
    resolver = FModelResourceResolver(
        model_path=model_path,
        fmodel_textures=[TextureResource(id=1, unreal_object_path="", mime="image/png", data=png_bytes)],
        ue_export_root=None,
        bundle_root=None,
    )
    images = FModelImageRepository(resolver, model_path, None, None)
    build_fmodel_materials(mesh, lod_materials, links, images, warnings.append)
    assert not warnings, warnings
    material = mesh.materials[0]
    assert any(node.label == "Embedded" for node in material.node_tree.nodes)
    assert any(node.label in {"External", "WetnessMask"} for node in material.node_tree.nodes)

roots = ("D:/exact ue root", "D:/exact bundle root")
_imported, _warnings, forwarded_roots = import_with_roots(*roots)
assert forwarded_roots == [(Path(roots[0]), Path(roots[1]))]


class UnexpectedResolver:
    def __init__(self, *args, **kwargs):
        raise AssertionError("material import disabled must not instantiate a resolver")


class UnexpectedImages:
    def __init__(self, *args, **kwargs):
        raise AssertionError("material import disabled must not instantiate an image repository")


original_resolver = logic.FModelResourceResolver
original_images = logic.FModelImageRepository
logic.FModelResourceResolver = UnexpectedResolver
logic.FModelImageRepository = UnexpectedImages
for existing_material in bpy.data.materials:
    for property_name in ("fmodel_material_link", "fmodel_parameter_metadata"):
        if property_name in existing_material:
            del existing_material[property_name]
try:
    imported, _model = logic.UEFormatImport(UEModelOptions(import_fmodel_materials=False)).import_file(
        REPOSITORY / "tests" / "fixtures" / "fmodel_golden.uemodel"
    )
    assert "WetnessMask" in imported.data["fmodel_material_links"]
    assert not any("fmodel_material_link" in material for material in imported.data.materials)
finally:
    logic.FModelResourceResolver = original_resolver
    logic.FModelImageRepository = original_images

print("FMODEL_MATERIAL_OPTIONS_BEHAVIOR_OK")
