import base64
import sys
import tempfile
import types
from pathlib import Path

import bpy


repository = Path(__file__).parents[1]
plugin_root = repository / "plugins" / "blender"
package = types.ModuleType("io_scene_ueformat")
package.__path__ = [str(plugin_root / "io_scene_ueformat")]
sys.modules["io_scene_ueformat"] = package

from io_scene_ueformat.importer.classes import FModelMaterialParameter, TextureResource  # noqa: E402
from io_scene_ueformat.importer.fmodel import FModelResourceResolver  # noqa: E402
from io_scene_ueformat.importer.material_images import FModelImageRepository  # noqa: E402


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUB"
    "AScY42YAAAAASUVORK5CYII="
)


def texture_parameter(texture: TextureResource) -> FModelMaterialParameter:
    return FModelMaterialParameter(name="Texture", kind=1, semantic="", value=texture)


with tempfile.TemporaryDirectory() as temporary_directory:
    root = Path(temporary_directory)
    model_path = root / "models" / "hero.ueformat"
    model_path.parent.mkdir()
    external_path = root / "textures" / "body.png"
    foreign_path = root / "textures" / "foreign.png"
    external_path.parent.mkdir()
    external_path.write_bytes(PNG_BYTES)
    foreign_path.write_bytes(PNG_BYTES)

    external = TextureResource(
        id=1,
        unreal_object_path="/Game/Textures/T_Body.T_Body",
        location_mode=1,
        uri="../textures/body.png",
    )
    foreign = TextureResource(
        id=3,
        unreal_object_path="/Game/Textures/T_Foreign.T_Foreign",
        location_mode=1,
        uri="../textures/foreign.png",
    )
    embedded = TextureResource(
        id=2,
        unreal_object_path="/Game/Textures/T_Embedded.T_Embedded",
        location_mode=3,
    )
    resolver = FModelResourceResolver(
        model_path=model_path,
        fmodel_textures={2: TextureResource(id=2, unreal_object_path="", data=PNG_BYTES)},
        ue_export_root=root,
        bundle_root=root / "bundle",
    )
    images = FModelImageRepository(resolver, model_path, root, root / "bundle")

    files_before_embedded = set(root.rglob("*"))
    preexisting = bpy.data.images.load(str(external_path), check_existing=True)
    preexisting.colorspace_settings.name = "Non-Color"
    external_color = images.get_image(texture_parameter(external), "COLOR")
    assert external_color.filepath == str(external_path)
    assert external_color is not preexisting
    assert preexisting.colorspace_settings.name == "Non-Color"
    assert external_color["fmodel_source_type"] == "external"
    assert external_color["fmodel_source_path"] == str(external_path)
    assert images.get_image(texture_parameter(external), "COLOR") is external_color

    external_data = images.get_image(texture_parameter(external), "DATA")
    assert external_data is not external_color
    assert external_color.colorspace_settings.name == "sRGB"
    assert external_data.colorspace_settings.name == "Non-Color"

    # Another repository's FModel-tagged image is still external to this cache.
    # Remove its loader source so check_existing can return the tagged foreign copy.
    foreign_images = FModelImageRepository(resolver, model_path, root, root / "bundle")
    foreign_loader_image = bpy.data.images.load(str(foreign_path), check_existing=True)
    foreign_color = foreign_images.get_image(texture_parameter(foreign), "COLOR")
    bpy.data.images.remove(foreign_loader_image)
    foreign_color["foreign_repository_sentinel"] = "preserve"
    foreign_colorspace = foreign_color.colorspace_settings.name
    assert bpy.data.images.load(str(foreign_path), check_existing=True) is foreign_color

    # A fresh repository must copy before changing any colorspace or provenance.
    fresh_images = FModelImageRepository(resolver, model_path, root, root / "bundle")
    fresh_data = fresh_images.get_image(texture_parameter(foreign), "DATA")
    assert fresh_data is not foreign_color
    assert foreign_color.colorspace_settings.name == foreign_colorspace == "sRGB"
    assert foreign_color["fmodel_source_type"] == "external"
    assert foreign_color["fmodel_source_path"] == str(foreign_path)
    assert foreign_color["fmodel_color_interpretation"] == "COLOR"
    assert foreign_color["foreign_repository_sentinel"] == "preserve"
    assert fresh_data.colorspace_settings.name == "Non-Color"
    assert fresh_data["fmodel_source_type"] == "external"
    assert fresh_data["fmodel_source_path"] == str(foreign_path)
    assert fresh_data["fmodel_color_interpretation"] == "DATA"
    assert fresh_images.get_image(texture_parameter(foreign), "DATA") is fresh_data

    embedded_color = images.get_image(texture_parameter(embedded), "COLOR")
    assert embedded_color.packed_file is not None
    assert embedded_color.source == "FILE"
    assert embedded_color.alpha_mode == "CHANNEL_PACKED"
    assert tuple(embedded_color.size) == (1, 1)
    assert tuple(embedded_color.pixels[:4]) == (0.0, 0.0, 0.0, 1.0)
    assert embedded_color["fmodel_source_type"] == "embedded"
    assert embedded_color["fmodel_source_id"] == 2
    assert images.get_image(texture_parameter(embedded), "COLOR") is embedded_color

    embedded_data = images.get_image(texture_parameter(embedded), "DATA")
    assert embedded_data is not embedded_color
    assert embedded_data.colorspace_settings.name == "Non-Color"
    assert set(root.rglob("*")) == files_before_embedded

print("FModel material image repository smoke passed")
