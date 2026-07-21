"""Blender image loading for FModel material-link texture parameters."""

from __future__ import annotations

import os
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING

import bpy

from .fmodel import FModelResourceResolver

if TYPE_CHECKING:
    from .classes import FModelMaterialParameter, TextureResource


def _canonical_unreal_identity(value: str) -> str:
    parts = [part for part in value.strip().replace("\\", "/").split("/") if part]
    return f"/{'/'.join(parts)}" if parts else ""


def _resource_cache_key(
    texture: TextureResource,
    resource: Path | bytes,
    interpretation: str,
) -> tuple[str, str, str]:
    """Identify decoded images by Unreal identity, verified bytes, and use."""
    contents = resource.read_bytes() if isinstance(resource, Path) else resource
    return (
        _canonical_unreal_identity(texture.unreal_object_path),
        sha256(contents).hexdigest(),
        interpretation,
    )


def _blender_file_path(path: Path) -> str:
    value = os.fspath(path)
    if os.name != "nt" or not os.path.isabs(value) or value.startswith("\\\\?\\"):
        return value
    extended = "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value
    try:
        import ctypes

        buffer = ctypes.create_unicode_buffer(32768)
        length = ctypes.windll.kernel32.GetShortPathNameW(extended, buffer, len(buffer))
        if length and length < len(buffer):
            return buffer.value
    except (AttributeError, OSError):
        pass
    return extended


class FModelImageRepository:
    """Load FModel texture resources once per source and color interpretation."""

    def __init__(
        self,
        resolver: FModelResourceResolver,
        model_path: str | Path,
        ue_export_root: str | Path | None,
        bundle_root: str | Path | None,
    ) -> None:
        self.resolver = resolver
        self.model_path = Path(model_path)
        self.ue_export_root = Path(ue_export_root) if ue_export_root is not None else None
        self.bundle_root = Path(bundle_root) if bundle_root is not None else None
        self._images: dict[tuple[str, str, str], bpy.types.Image] = {}

    def resource_cache_key(
        self,
        parameter: FModelMaterialParameter,
        color_interpretation: str,
    ) -> tuple[str, str, str]:
        interpretation = color_interpretation.upper()
        if interpretation not in {"COLOR", "DATA"}:
            raise ValueError(f"Unsupported FModel image color interpretation: {color_interpretation}")
        resource = self.resolver.resolve_texture(parameter.texture)
        return _resource_cache_key(parameter.texture, resource, interpretation)

    def get_image(self, parameter: FModelMaterialParameter, color_interpretation: str) -> bpy.types.Image:
        """Return a cached Blender image for an FModel texture parameter."""
        interpretation = color_interpretation.upper()
        if interpretation not in {"COLOR", "DATA"}:
            raise ValueError(f"Unsupported FModel image color interpretation: {color_interpretation}")

        texture = parameter.texture
        resource = self.resolver.resolve_texture(texture)
        cache_key = _resource_cache_key(texture, resource, interpretation)
        image = self._images.get(cache_key)
        if image is not None:
            return image

        if isinstance(resource, Path):
            image = bpy.data.images.load(_blender_file_path(resource), check_existing=True)
            # A Blender lookup is not proof of local ownership. Only this repository's
            # cache may be mutated, regardless of global FModel provenance tags.
            if image not in self._images.values():
                image = image.copy()
            image["fmodel_source_type"] = "external"
            image["fmodel_source_path"] = str(resource)
        else:
            image = bpy.data.images.new(f"FModelEmbedded_{texture.id}", 8, 8)
            image.pack(data=resource, data_len=len(resource))
            image.source = "FILE"
            image.alpha_mode = "CHANNEL_PACKED"
            image["fmodel_source_type"] = "embedded"
            image["fmodel_source_id"] = texture.id

        image["fmodel_color_interpretation"] = interpretation
        image.colorspace_settings.name = "sRGB" if interpretation == "COLOR" else "Non-Color"
        self._images[cache_key] = image
        return image
