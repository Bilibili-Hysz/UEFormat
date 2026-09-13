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
        assert check_existing is True
        for image in self:
            if image.filepath == filepath:
                return image
        image = _FakeImage(self, filepath)
        self.append(image)
        if getattr(self, "fail_copy", False):
            image.copy = lambda: (_ for _ in ()).throw(RuntimeError("copy failed"))
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
    )
    return types.SimpleNamespace(texture=texture)


def test_newly_loaded_external_image_source_is_removed_after_copy(monkeypatch, tmp_path: Path):
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


def test_preexisting_external_image_is_preserved_while_copy_is_isolated(monkeypatch, tmp_path: Path):
    module, images = _load_image_repository(monkeypatch)
    resource = tmp_path / "body.png"
    resource.write_bytes(b"texture")
    preexisting = images.load(str(resource), check_existing=True)

    repository = module.FModelImageRepository(
        _FakeResolver(resource), tmp_path / "model.uemodel", None, None
    )
    copied = repository.get_image(_parameter(), "COLOR")

    assert copied is not preexisting
    assert len(images) == 2
    assert preexisting in images


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


def test_failed_external_image_copy_removes_newly_loaded_source(monkeypatch, tmp_path: Path):
    module, images = _load_image_repository(monkeypatch)
    resource = tmp_path / "body.png"
    resource.write_bytes(b"texture")
    images.fail_copy = True
    repository = module.FModelImageRepository(
        _FakeResolver(resource), tmp_path / "model.uemodel", None, None
    )

    with pytest.raises(RuntimeError, match="copy failed"):
        repository.get_image(_parameter(), "COLOR")

    assert images == []
