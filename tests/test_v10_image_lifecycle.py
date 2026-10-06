from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest


class _FakeImage(dict):
    _next_pointer = 1

    def __init__(self, owner, filepath: str) -> None:
        super().__init__()
        self._owner = owner
        self.filepath = filepath
        self.name = Path(filepath).name
        self.decoded_bytes = Path(filepath).read_bytes()
        self.colorspace_settings = types.SimpleNamespace(name="sRGB")
        self._pointer = _FakeImage._next_pointer
        _FakeImage._next_pointer += 1

    def as_pointer(self) -> int:
        return self._pointer

    def copy(self):
        copied = type(self)(self._owner, self.filepath)
        self._owner.append(copied)
        return copied


class _FakeImages(list):
    def load(self, filepath: str, *, check_existing: bool):
        if check_existing:
            for image in self:
                if image.filepath == filepath:
                    return image
        image = _FakeImage(self, filepath)
        self.append(image)
        return image

    def remove(self, image) -> None:
        super().remove(image)


class _FakeResolver:
    def __init__(self, resource: Path) -> None:
        self.resource = resource

    def resolve_texture(self, texture):
        return self.resource


def _load_image_repository(monkeypatch):
    fake_images = _FakeImages()
    fake_bpy = types.ModuleType("bpy")
    fake_bpy.data = types.SimpleNamespace(images=fake_images)
    fake_bpy.types = types.SimpleNamespace(Image=object)
    monkeypatch.setitem(sys.modules, "bpy", fake_bpy)
    sys.modules.pop("io_scene_ueformat.importer.runtime_material_images", None)
    module = importlib.import_module("io_scene_ueformat.importer.runtime_material_images")
    return module, fake_images


def _parameter():
    texture = types.SimpleNamespace(
        id=1,
        unreal_object_path="/Game/Textures/T_Body.T_Body",
        stable_id="texture.body",
        color_space=1,
        uri="body.png",
    )
    return types.SimpleNamespace(texture=texture)


def test_freshly_loaded_external_image_is_owned_without_intermediate_copy(monkeypatch, tmp_path: Path):
    module, images = _load_image_repository(monkeypatch)
    resource = tmp_path / "body.png"
    resource.write_bytes(b"texture")

    repository = module.FModelImageRepository(
        _FakeResolver(resource), tmp_path / "model.uemodel", None, None
    )
    copied = repository.get_image(_parameter(), "COLOR")

    assert len(images) == 1
    assert images[0] is copied
    assert copied.filepath == module._blender_file_path(resource)


def test_preexisting_packed_image_is_preserved_and_new_file_is_decoded(monkeypatch, tmp_path: Path):
    module, images = _load_image_repository(monkeypatch)
    resource = tmp_path / "body.png"
    resource.write_bytes(b"texture")
    preexisting = images.load(str(resource), check_existing=True)
    resource.write_bytes(b"updated texture")

    repository = module.FModelImageRepository(
        _FakeResolver(resource), tmp_path / "model.uemodel", None, None
    )
    copied = repository.get_image(_parameter(), "COLOR")

    assert copied is not preexisting
    assert len(images) == 2
    assert preexisting in images
    assert preexisting.decoded_bytes == b"texture"
    assert copied.decoded_bytes == b"updated texture"


def test_external_color_and_data_images_are_isolated(monkeypatch, tmp_path: Path):
    module, images = _load_image_repository(monkeypatch)
    resource = tmp_path / "body.png"
    resource.write_bytes(b"texture")
    repository = module.FModelImageRepository(
        _FakeResolver(resource), tmp_path / "model.uemodel", None, None
    )

    color = repository.get_image(_parameter(), "COLOR")
    data = repository.get_image(_parameter(), "DATA")

    assert color is not data
    assert len(images) == 2


def test_failed_external_image_setup_removes_owned_image(monkeypatch, tmp_path: Path):
    module, images = _load_image_repository(monkeypatch)
    resource = tmp_path / "body.png"
    resource.write_bytes(b"texture")
    monkeypatch.setattr(module, "_set_image_colorspace", lambda *args: (_ for _ in ()).throw(ValueError("unsupported space")))
    repository = module.FModelImageRepository(_FakeResolver(resource), tmp_path / "model.uemodel", None, None)
    with pytest.raises(ValueError, match="unsupported space"):
        repository.get_image(_parameter(), "COLOR")
    assert images == []


def test_color_transfer_and_data_usage_have_distinct_keys(monkeypatch, tmp_path: Path):
    module, images = _load_image_repository(monkeypatch)
    resource = tmp_path / "body.png"
    resource.write_bytes(b"texture")
    repository = module.FModelImageRepository(_FakeResolver(resource), tmp_path / "model.uemodel", None, None)
    srgb = _parameter()
    linear = _parameter()
    linear.texture.color_space = 0
    srgb_image = repository.get_image(srgb, "COLOR")
    linear_image = repository.get_image(linear, "COLOR")
    data_image = repository.get_image(srgb, "DATA")
    assert len(images) == 3
    assert srgb_image.colorspace_settings.name == "sRGB"
    assert linear_image.colorspace_settings.name == "Linear Rec.709"
    assert data_image.colorspace_settings.name == "Non-Color"
    assert repository.get_image(linear, "DATA") is data_image
    assert len({repository.resource_cache_key(srgb, "COLOR"), repository.resource_cache_key(linear, "COLOR"), repository.resource_cache_key(srgb, "DATA")}) == 3


def test_display_name_comes_from_original_unicode_path(monkeypatch, tmp_path: Path):
    module, images = _load_image_repository(monkeypatch)
    resource = tmp_path / "角色服装贴图_Very_Long_Original_Texture_Name.png"
    resource.write_bytes(b"texture")
    repository = module.FModelImageRepository(_FakeResolver(resource), tmp_path / "model.uemodel", None, None)
    image = repository.get_image(_parameter(), "COLOR")
    assert image.name == resource.name
    assert image["fmodel_texture_id"] == "texture.body"
    assert image["fmodel_source_path"] == str(resource)
