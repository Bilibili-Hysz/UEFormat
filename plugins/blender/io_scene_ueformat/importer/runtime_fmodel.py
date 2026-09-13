"""FModel material-link resource resolution and Blender metadata helpers.

``FModelResourceResolver.resolve_texture`` is deliberately side-effect free:
it returns a local ``Path`` for relative and bundle references, or raw bytes
for embedded references.  It never loads images or creates Blender nodes.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path, PurePosixPath
from typing import Iterable, Mapping

from .runtime_materials import (
    FModelMaterialLinks,
    FModelMaterialParameter,
    FModelMaterialTextures,
    FModelTextureMapKind,
)
from .runtime_identity import (
    FModelSkeletonIdentity,
    FModelTargetPoseIdentity,
    FModelTargetSkeletonIdentity,
)
from .runtime_protocol import _validate_logical_unreal_path
from .runtime_resources import TextureResource, TextureResourceKind


RELATIVE_LOCATION_MODE = 1
BUNDLE_LOCATION_MODE = 2
EMBEDDED_LOCATION_MODE = 3
EXPORT_ROOT_LOCATION_MODE = 4
MAX_MISSING_ANIMATION_TRACK_RATIO = 0.10


_MATERIAL_TEXTURE_PARAMETER_SHAPES = {
    FModelTextureMapKind.BaseColor: ("FMODEL_TEXTURES.BaseColor", "base_color", 0),
    FModelTextureMapKind.Normal: ("FMODEL_TEXTURES.Normal", "normal", 0),
    FModelTextureMapKind.Orm: ("FMODEL_TEXTURES.Orm", "orm", 1),
}


def apply_material_texture_bindings(
    material_links: FModelMaterialLinks,
    material_textures: FModelMaterialTextures,
) -> None:
    """Project identity-only FMODEL_TEXTURES bindings into material parameters.

    The wire ``MaterialId`` is the corresponding ``FMODEL_MATERIALS``
    ``MaterialName`` (``slot_name`` in this importer). Repeated mesh sections can
    therefore share one binding, and every matching material-link slot receives
    the same synthetic parameter.
    """
    textures_by_id = {texture.texture_id: texture for texture in material_textures.textures}
    slots_by_material_id: dict[str, list] = {}
    for slot in material_links.slots:
        slots_by_material_id.setdefault(slot.slot_name, []).append(slot)

    for binding in material_textures.bindings:
        slots = slots_by_material_id.get(binding.material_id)
        if not slots:
            raise ValueError(
                "Invalid FModel texture binding material_id does not exist in FMODEL_MATERIALS: "
                f"{binding.material_id}"
            )
        material_identities = {slot.unreal_material_path for slot in slots}
        if len(material_identities) > 1:
            raise ValueError(
                "Invalid FModel texture binding material_id is ambiguous across Unreal material identities: "
                f"{binding.material_id}"
            )
        record = textures_by_id[binding.texture_id]
        name, semantic, packing = _MATERIAL_TEXTURE_PARAMETER_SHAPES[binding.map_kind]
        for slot in slots:
            matching = [
                parameter
                for parameter in slot.parameters
                if parameter.semantic.casefold() == semantic
            ]
            if matching:
                if len(matching) == 1 and matching[0].kind == 1:
                    texture = matching[0].texture
                    if (
                        texture.stable_id == record.texture_id
                        and texture.uri == record.logical_resource_uri
                        and texture.expected_byte_length == record.byte_length
                        and texture.expected_content_hash == record.content_hash
                    ):
                        continue
                raise ValueError(
                    "Invalid FModel texture binding conflicts with an existing material parameter: "
                    f"{binding.material_id} {binding.map_kind.name}"
                )

            slot.parameters.append(FModelMaterialParameter(
                name=name,
                kind=1,
                semantic=semantic,
                value=TextureResource.identity(
                    id=0,
                    unreal_object_path="",
                    location_mode=record.location_mode,
                    uri=record.logical_resource_uri,
                    color_space=1 if binding.is_srgb else 0,
                    packing=packing,
                    uv_set=0,
                    stable_id=record.texture_id,
                    expected_byte_length=record.byte_length,
                    expected_content_hash=record.content_hash,
                    identity_embedded=record.is_embedded,
                ),
            ))


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
        if texture.kind is TextureResourceKind.LEGACY_EMBEDDED:
            raise ValueError("Legacy embedded TextureResource cannot be used as a material texture reference")
        if texture.location_mode == RELATIVE_LOCATION_MODE:
            self._validate_relative_uri(texture.uri, "relative", allow_leading_parents=True)
            candidate = self.model_path.parent.joinpath(*PurePosixPath(texture.uri).parts)
            relative_root = self.ue_export_root or self._infer_relative_root(texture.uri)
            candidate = self._resolve_within_root(
                relative_root,
                candidate,
                "relative URI escapes configured or inferred export root",
            )
            return self._validate_external_resource(candidate, texture)
        if texture.location_mode == BUNDLE_LOCATION_MODE:
            if self.bundle_root is None:
                raise ValueError("FModel bundle resource resolution requires bundle_root")
            self._validate_relative_uri(texture.uri, "bundle")
            candidate = self._resolve_within_root(
                self.bundle_root,
                self.bundle_root.joinpath(*PurePosixPath(texture.uri).parts),
                "bundle URI escapes bundle_root",
            )
            return self._validate_external_resource(candidate, texture)
        if texture.location_mode == EMBEDDED_LOCATION_MODE:
            if texture.uri:
                self._validate_relative_uri(texture.uri, "embedded")
            resource = self.fmodel_textures.get(texture.id)
            if resource is None:
                raise ValueError(f"FModel embedded resource has no texture id {texture.id}")
            if resource.kind is not TextureResourceKind.LEGACY_EMBEDDED:
                raise ValueError(f"FModel embedded resource id {texture.id} does not carry embedded bytes")
            return resource.image_bytes
        if texture.location_mode == EXPORT_ROOT_LOCATION_MODE:
            return self._resolve_identity_texture(texture)
        raise ValueError(f"Unsupported FModel texture location mode: {texture.location_mode}")

    def _resolve_identity_texture(self, texture: TextureResource) -> Path:
        self._validate_relative_uri(texture.uri, "export-root")
        if texture.identity_embedded:
            raise ValueError(
                f"FModel identity texture {texture.stable_id!r} is marked embedded, but FMODEL_TEXTURES v1 carries no payload bytes"
            )
        if texture.expected_byte_length is None or len(texture.expected_content_hash) != 32:
            raise ValueError(f"FModel identity texture {texture.stable_id!r} has incomplete integrity metadata")

        roots = self._identity_resource_roots(texture.uri)
        if not roots:
            raise ValueError(
                "FModel export-root texture resolution requires ue_export_root, bundle_root, "
                "or a file-based .uemodel path"
            )

        for root in roots:
            candidate = self._resolve_within_root(
                root,
                root.joinpath(*PurePosixPath(texture.uri).parts),
                "export-root URI escapes configured or inferred root",
            )
            if not candidate.exists():
                continue
            return self._validate_external_resource(candidate, texture)

        raise FileNotFoundError(f"FModel identity texture was not found for export-root URI: {texture.uri}")

    @classmethod
    def _validate_external_resource(cls, path: Path, texture: TextureResource) -> Path:
        """Validate file shape and integrity for identity-backed resources."""
        if texture.kind is not TextureResourceKind.IDENTITY:
            return path
        if texture.identity_embedded:
            raise ValueError(
                f"FModel identity texture {texture.stable_id!r} is marked embedded, "
                "but the V10 identity table carries no payload bytes"
            )
        if texture.expected_byte_length is None or len(texture.expected_content_hash) != 32:
            raise ValueError(f"FModel identity texture {texture.stable_id!r} has incomplete integrity metadata")
        if not path.exists():
            raise FileNotFoundError(f"FModel identity texture was not found: {texture.uri}")
        if not path.is_file():
            raise ValueError(f"FModel identity texture is not a file: {texture.uri}")
        cls._verify_identity_texture(path, texture)
        return path

    def _identity_resource_roots(self, uri: str) -> list[Path]:
        configured: list[Path] = []
        for root in (self.ue_export_root, self.bundle_root):
            if root is None:
                continue
            resolved = root.resolve(strict=False)
            if resolved not in configured:
                configured.append(resolved)
        if configured:
            return configured

        if self.model_path.suffix.casefold() != ".uemodel":
            return []
        inferred = self._infer_export_root_from_uri(uri)
        return [inferred] if inferred is not None else []

    def _infer_relative_root(self, uri: str) -> Path:
        model_parent = self.model_path.resolve(strict=False).parent
        for ancestor in (model_parent, *model_parent.parents):
            if ancestor.name.casefold() == "content":
                return ancestor
        if PurePosixPath(uri).parts[0] == "..":
            raise ValueError(
                "FModel owner-relative URI with leading parents requires ue_export_root "
                "or a model beneath a Content root"
            )
        return model_parent

    def _infer_export_root_from_uri(self, uri: str) -> Path | None:
        parts = PurePosixPath(uri).parts
        if not parts:
            return None
        first_segment = parts[0].casefold()
        model_parent = self.model_path.resolve(strict=False).parent
        for ancestor in (model_parent, *model_parent.parents):
            if ancestor.name.casefold() == first_segment:
                return ancestor.parent
        return None

    @staticmethod
    def _verify_identity_texture(path: Path, texture: TextureResource) -> None:
        actual_length = path.stat().st_size
        if actual_length != texture.expected_byte_length:
            raise ValueError(
                f"FModel identity texture byte length mismatch for {texture.uri}: "
                f"expected {texture.expected_byte_length}, got {actual_length}"
            )

        digest = sha256()
        with open(_filesystem_path(path), "rb") as file:
            for block in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(block)
        if digest.digest() != texture.expected_content_hash:
            raise ValueError(f"FModel identity texture SHA-256 mismatch for {texture.uri}")

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
        if (
            not uri
            or "\\" in uri
            or ":" in uri
            or any(ord(character) <= 0x1F or 0x7F <= ord(character) <= 0x9F for character in uri)
        ):
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


def _filesystem_path(path: Path) -> str:
    value = os.fspath(path)
    if os.name != "nt" or not os.path.isabs(value) or value.startswith("\\\\?\\"):
        return value
    return "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value


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
    identity: FModelSkeletonIdentity | FModelTargetSkeletonIdentity | FModelTargetPoseIdentity,
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
    elif isinstance(identity, FModelTargetPoseIdentity):
        metadata["source_pose_path"] = identity.source_pose_path
    return metadata


def verify_fmodel_target_skeleton(
    target_identity: dict[str, object],
    armature_identity: dict[str, object],
) -> None:
    """Validate two Task 3 provenance records before applying animation."""
    target_path = _strict_identity_path(target_identity, "skeleton_path")
    armature_path = _strict_identity_path(armature_identity, "skeleton_path")
    if target_path != armature_path:
        raise ValueError("FModel target skeleton path mismatch")

    target_guid = target_identity.get("skeleton_guid") if target_identity.get("has_skeleton_guid") else None
    armature_guid = armature_identity.get("skeleton_guid") if armature_identity.get("has_skeleton_guid") else None
    if target_guid and armature_guid and target_guid != armature_guid:
        raise ValueError("FModel target skeleton GUID mismatch")

    if target_identity.get("bone_layout_hash") != armature_identity.get("bone_layout_hash"):
        raise ValueError("FModel target skeleton layout hash mismatch")


def verify_fmodel_target_pose(
    target_identity: dict[str, object],
    armature_identity: dict[str, object],
) -> None:
    """Validate a UEPose target identity against the selected armature."""
    _validate_identity_pair(target_identity, armature_identity, "pose", source_field="source_pose_path")


def pose_identity_issues(
    target_identity: Mapping[str, object] | None,
    armature_identity: Mapping[str, object] | None,
) -> list[str]:
    issues = _identity_issues(target_identity, "target", require_source_path=True, source_field="source_pose_path")
    issues.extend(_identity_issues(armature_identity, "armature", require_source_path=False))
    return issues


def _validate_identity_pair(
    target_identity: dict[str, object],
    armature_identity: dict[str, object],
    kind: str,
    *,
    source_field: str = "source_animation_path",
) -> None:
    target_path = _strict_identity_path(target_identity, "skeleton_path")
    armature_path = _strict_identity_path(armature_identity, "skeleton_path")
    _strict_identity_path(target_identity, source_field)
    if target_path != armature_path:
        raise ValueError(f"FModel target {kind} skeleton path mismatch")
    target_guid = target_identity.get("skeleton_guid") if target_identity.get("has_skeleton_guid") else None
    armature_guid = armature_identity.get("skeleton_guid") if armature_identity.get("has_skeleton_guid") else None
    if target_guid and armature_guid and target_guid != armature_guid:
        raise ValueError(f"FModel target {kind} skeleton GUID mismatch")
    if target_identity.get("bone_layout_hash") != armature_identity.get("bone_layout_hash"):
        raise ValueError(f"FModel target {kind} skeleton layout hash mismatch")


def _strict_identity_path(identity: Mapping[str, object] | None, field_name: str) -> str:
    if identity is None or field_name not in identity:
        raise ValueError(f"FModel {field_name} skeleton identity is malformed")
    value = identity[field_name]
    if not isinstance(value, str):
        raise ValueError(f"FModel {field_name} skeleton identity is malformed")
    try:
        return _validate_logical_unreal_path(value, f"FModel {field_name}")
    except ValueError as exc:
        raise ValueError(f"FModel {field_name} skeleton identity is malformed") from exc


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
    source_field: str = "source_animation_path",
) -> list[str]:
    if identity is None:
        return [f"FModel {label} skeleton identity is missing"]
    required = ("version", "skeleton_path", "has_skeleton_guid", "skeleton_guid", "bone_layout_hash")
    if require_source_path:
        required += (source_field,)
    if any(field not in identity for field in required):
        return [f"FModel {label} skeleton identity is malformed"]
    valid = (
        identity["version"] == 1
        and isinstance(identity["skeleton_path"], str)
        and _is_valid_identity_path(identity["skeleton_path"])
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
            and isinstance(identity[source_field], str)
            and _is_valid_identity_path(identity[source_field])
        )
    return [] if valid else [f"FModel {label} skeleton identity is malformed"]


def _is_valid_identity_path(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        _validate_logical_unreal_path(value, "FModel identity path")
    except ValueError:
        return False
    return True


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
        metadata = {
            "location_mode": texture.location_mode,
            "uri": texture.uri,
            "texture_id": texture.id,
            "unreal_object_path": texture.unreal_object_path,
            "color_space": texture.color_space,
            "packing": texture.packing,
            "uv_set": texture.uv_set,
        }
        if texture.stable_id:
            metadata["stable_id"] = texture.stable_id
        if texture.expected_byte_length is not None:
            metadata["expected_byte_length"] = texture.expected_byte_length
        if texture.expected_content_hash:
            metadata["expected_sha256"] = texture.expected_content_hash.hex()
        if texture.identity_embedded:
            metadata["identity_embedded"] = True
        return metadata
    if parameter.kind == 3:
        return list(parameter.value)
    return parameter.value


