"""FModel material-link resource resolution and Blender metadata helpers.

``FModelResourceResolver.resolve_texture`` is deliberately side-effect free:
it returns a local ``Path`` for relative and bundle references, or raw bytes
for embedded references.  It never loads images or creates Blender nodes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Mapping

from .classes import FModelMaterialLinks, FModelSkeletonIdentity, FModelTargetSkeletonIdentity, TextureResource


RELATIVE_LOCATION_MODE = 1
BUNDLE_LOCATION_MODE = 2
EMBEDDED_LOCATION_MODE = 3
MAX_MISSING_ANIMATION_TRACK_RATIO = 0.10


@dataclass(frozen=True, slots=True)
class FModelAnimationBindingPlan:
    bound_bone_names: tuple[str | None, ...]
    bound_track_count: int
    skipped_track_count: int
    total_track_count: int
    provenance_expected: bool
    source_identity: dict[str, object] | None
    issues: tuple[str, ...]


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
            self._validate_relative_uri(texture.uri, "relative", allow_leading_parents=True)
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
    def _validate_relative_uri(uri: str, mode: str, *, allow_leading_parents: bool = False) -> None:
        if "\0" in uri or "\\" in uri or not uri or ":" in uri:
            raise ValueError(f"Invalid FModel {mode} URI")
        path = PurePosixPath(uri)
        parts = uri.split("/")
        if path.is_absolute() or any(part in {"", "."} for part in parts):
            raise ValueError(f"Invalid FModel {mode} URI")
        if allow_leading_parents:
            while parts and parts[0] == "..":
                parts = parts[1:]
        if any(part == ".." for part in parts):
            raise ValueError(f"Invalid FModel {mode} URI")


def material_links_metadata(material_links: FModelMaterialLinks) -> dict[str, object]:
    """Return versioned, JSON-serializable raw FModel material-link records."""
    metadata = {
        "version": 2 if material_links.source_material_mapping_present or material_links.pbr_interfaces else 1,
        "slots": [
            {
                "slot_index": slot.slot_index,
                "source_material_index": slot.source_material_index,
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


def plan_fmodel_animation_binding(
    target_identity: Mapping[str, object] | None,
    armature_identity: Mapping[str, object] | None,
    track_names: Iterable[str],
    bone_names: Iterable[str],
    *,
    strict: bool = True,
) -> FModelAnimationBindingPlan:
    """Validate provenance and resolve animation tracks without Blender mutations."""
    target = dict(target_identity) if target_identity is not None else None
    armature = dict(armature_identity) if armature_identity is not None else None
    provenance_expected = target is not None or armature is not None
    issues: list[str] = []

    if provenance_expected:
        issues.extend(_identity_issues(target, "target", require_source_path=True))
        issues.extend(_identity_issues(armature, "armature", require_source_path=False))
        if not issues:
            try:
                verify_fmodel_target_skeleton(target, armature)  # type: ignore[arg-type]
            except ValueError as exc:
                issues.append(str(exc))

    folded_bones: dict[str, list[str]] = {}
    for bone_name in bone_names:
        folded_bones.setdefault(bone_name.casefold(), []).append(bone_name)

    bindings: list[str | None] = []
    for track_name in track_names:
        matches = folded_bones.get(track_name.casefold(), [])
        if len(matches) > 1:
            if provenance_expected:
                issues.append(f"FModel animation bone match is ambiguous for track {track_name!r}")
                bindings.append(None)
            else:
                bindings.append(matches[0])
        else:
            bindings.append(matches[0] if matches else None)

    total = len(bindings)
    bound = sum(binding is not None for binding in bindings)
    skipped = total - bound
    if provenance_expected and total and skipped / total > MAX_MISSING_ANIMATION_TRACK_RATIO:
        issues.append(
            f"FModel animation has {skipped} of {total} tracks missing or ambiguous "
            f"(maximum {MAX_MISSING_ANIMATION_TRACK_RATIO:.0%})"
        )

    if strict and provenance_expected and issues:
        raise ValueError("; ".join(issues))

    return FModelAnimationBindingPlan(
        bound_bone_names=tuple(bindings),
        bound_track_count=bound,
        skipped_track_count=skipped,
        total_track_count=total,
        provenance_expected=provenance_expected,
        source_identity=target,
        issues=tuple(issues),
    )


def _identity_issues(
    identity: Mapping[str, object] | None,
    label: str,
    *,
    require_source_path: bool,
) -> list[str]:
    if identity is None:
        return [f"FModel {label} skeleton identity is missing"]
    required = ("version", "skeleton_path", "has_skeleton_guid", "skeleton_guid", "bone_layout_hash")
    if require_source_path:
        required += ("source_animation_path",)
    if any(field not in identity for field in required):
        return [f"FModel {label} skeleton identity is malformed"]
    valid = (
        identity["version"] == 1
        and isinstance(identity["skeleton_path"], str)
        and bool(identity["skeleton_path"].strip())
        and isinstance(identity["has_skeleton_guid"], bool)
        and _is_hex_bytes(identity["bone_layout_hash"], 32)
    )
    if identity["has_skeleton_guid"]:
        valid = valid and _is_hex_bytes(identity["skeleton_guid"], 16)
    else:
        valid = valid and identity["skeleton_guid"] is None
    if require_source_path:
        valid = (
            valid
            and isinstance(identity["source_animation_path"], str)
            and bool(identity["source_animation_path"].strip())
        )
    return [] if valid else [f"FModel {label} skeleton identity is malformed"]


def _is_hex_bytes(value: object, byte_count: int) -> bool:
    if not isinstance(value, str) or len(value) != byte_count * 2:
        return False
    try:
        bytes.fromhex(value)
    except ValueError:
        return False
    return True


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
