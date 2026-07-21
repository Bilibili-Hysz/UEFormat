"""FModel material-link resource resolution and Blender metadata helpers.

``FModelResourceResolver.resolve_texture`` is deliberately side-effect free:
it returns a local ``Path`` for relative and bundle references, or raw bytes
for embedded references.  It never loads images or creates Blender nodes.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Mapping

from .classes import FModelMaterialLinks, FModelSkeletonIdentity, FModelTargetSkeletonIdentity, TextureResource


RELATIVE_LOCATION_MODE = 1
BUNDLE_LOCATION_MODE = 2
EMBEDDED_LOCATION_MODE = 3


class FModelResourceResolver:
    """Resolve FModel texture references, optionally constrained by roots.

    Location modes are ``1`` (relative to ``model_path.parent``), ``2``
    (bundle-relative), and ``3`` (embedded resource by texture id).

    Existing symlinks and Windows junctions are followed by ``Path.resolve``;
    a resolved target outside the configured root is rejected. Missing suffixes
    are allowed only when their nearest existing ancestor remains in the root.
    """

    def __init__(
        self,
        *,
        model_path: str | Path,
        fmodel_textures: Mapping[int, TextureResource] | list[TextureResource],
        ue_export_root: str | Path | None,
        bundle_root: str | Path | None,
    ) -> None:
        self.model_path = Path(model_path)
        self.fmodel_textures = (
            fmodel_textures
            if isinstance(fmodel_textures, Mapping)
            else {texture.id: texture for texture in fmodel_textures}
        )
        self.ue_export_root = Path(ue_export_root) if ue_export_root else None
        self.bundle_root = Path(bundle_root) if bundle_root is not None else None

    def resolve_texture(self, texture: TextureResource) -> Path | bytes:
        """Return a safe resource location or embedded bytes for ``texture``."""
        if texture.location_mode == RELATIVE_LOCATION_MODE:
            self._validate_relative_uri(texture.uri, "relative")
            candidate = self.model_path.parent.joinpath(*PurePosixPath(texture.uri).parts)
            if self.ue_export_root is None:
                return candidate.resolve(strict=False)
            return self._resolve_within_root(
                self.ue_export_root,
                candidate,
                "relative URI escapes ue_export_root",
            )
        if texture.location_mode == BUNDLE_LOCATION_MODE:
            if self.bundle_root is None:
                raise ValueError("FModel bundle resource resolution requires bundle_root")
            self._validate_relative_uri(texture.uri, "bundle")
            return self._resolve_within_root(
                self.bundle_root,
                self.bundle_root.joinpath(*PurePosixPath(texture.uri).parts),
                "bundle URI escapes bundle_root",
            )
        if texture.location_mode == EMBEDDED_LOCATION_MODE:
            if texture.uri:
                self._validate_relative_uri(texture.uri, "embedded")
            resource = self.fmodel_textures.get(texture.id)
            if resource is None:
                raise ValueError(f"FModel embedded resource has no texture id {texture.id}")
            return resource.image_bytes
        raise ValueError(f"Unsupported FModel texture location mode: {texture.location_mode}")

    @staticmethod
    def _resolve_within_root(root: Path, candidate: Path, error: str) -> Path:
        root = root.resolve(strict=False)
        candidate = candidate.resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError(error) from exc
        return candidate

    @staticmethod
    def _validate_relative_uri(uri: str, mode: str) -> None:
        if "\0" in uri or "\\" in uri or not uri or ":" in uri:
            raise ValueError(f"Invalid FModel {mode} URI")
        path = PurePosixPath(uri)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in uri.split("/")):
            raise ValueError(f"Invalid FModel {mode} URI")


def material_links_metadata(material_links: FModelMaterialLinks) -> dict[str, object]:
    """Return versioned, JSON-serializable raw FModel material-link records."""
    metadata = {
        "version": 2 if material_links.pbr_interfaces else 1,
        "slots": [
            {
                "slot_index": slot.slot_index,
                "slot_name": slot.slot_name,
                "unreal_material_path": slot.unreal_material_path,
                "material_json_uri": slot.material_json_uri,
                "parameters": [
                    {
                        "name": parameter.name,
                        "kind": parameter.kind,
                        "semantic": parameter.semantic,
                        "value": _parameter_value_metadata(parameter),
                    }
                    for parameter in slot.parameters
                ],
            }
            for slot in material_links.slots
        ],
    }
    if material_links.pbr_interfaces:
        metadata["pbr_interfaces"] = [
            {
                "slot_index": interface.slot_index,
                "parameter_index": interface.parameter_index,
                "role": interface.role,
            }
            for interface in material_links.pbr_interfaces
        ]
    return metadata


def skeleton_identity_metadata(
    identity: FModelSkeletonIdentity | FModelTargetSkeletonIdentity,
) -> dict[str, object]:
    """Return a lossless JSON-safe FModel skeleton identity record."""
    metadata: dict[str, object] = {
        "version": identity.version,
        "skeleton_path": identity.skeleton_path,
        "has_skeleton_guid": identity.skeleton_guid is not None,
        "skeleton_guid": identity.skeleton_guid.hex() if identity.skeleton_guid is not None else None,
        "bone_layout_hash": identity.bone_layout_hash.hex(),
    }
    if isinstance(identity, FModelTargetSkeletonIdentity):
        metadata["source_animation_path"] = identity.source_animation_path
    return metadata


def verify_fmodel_target_skeleton(
    target_identity: dict[str, object],
    armature_identity: dict[str, object],
) -> None:
    """Validate two Task 3 provenance records before applying animation."""
    target_path = str(target_identity.get("skeleton_path", "")).strip().replace("\\", "/")
    armature_path = str(armature_identity.get("skeleton_path", "")).strip().replace("\\", "/")
    if target_path != armature_path:
        raise ValueError("FModel target skeleton path mismatch")

    target_guid = target_identity.get("skeleton_guid") if target_identity.get("has_skeleton_guid") else None
    armature_guid = armature_identity.get("skeleton_guid") if armature_identity.get("has_skeleton_guid") else None
    if target_guid and armature_guid and target_guid != armature_guid:
        raise ValueError("FModel target skeleton GUID mismatch")

    if target_identity.get("bone_layout_hash") != armature_identity.get("bone_layout_hash"):
        raise ValueError("FModel target skeleton layout hash mismatch")


def _parameter_value_metadata(parameter) -> object:
    if parameter.kind == 1:
        texture = parameter.texture
        return {
            "location_mode": texture.location_mode,
            "uri": texture.uri,
            "texture_id": texture.id,
            "unreal_object_path": texture.unreal_object_path,
            "color_space": texture.color_space,
            "packing": texture.packing,
            "uv_set": texture.uv_set,
        }
    if parameter.kind == 3:
        return list(parameter.value)
    return parameter.value
