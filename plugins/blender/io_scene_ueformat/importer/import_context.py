from __future__ import annotations

import json
from pathlib import Path

import bpy
from bpy.types import Action, Object

from ..logging import Log
from ..options import UEAnimOptions, UEFormatOptions, UEModelOptions, UEPoseOptions
from .archive.reader import FArchiveReader
from .create.anim import create_anim
from .create.model import create_model
from .create.pose import create_pose
from .create.space import to_blender_space
from .creation_tracker import UEFormatImportCapture
from .dto.anim import AnimDto
from .dto.model import ModelDto
from .dto.pose import PoseDto
from .version import EUEFormatVersion
from .extensions.fmodel import (
    FModelLegacyExtensionSnapshot,
    FModelIdentityRecord,
    FModelMaterialLinksSnapshot,
    FModelPBRInterface,
    FModelTextureSnapshot,
    FModelV10Attribute,
    FModelV10ExtensionSnapshot,
    FModelV10FileKind,
    collect_fmodel_extensions,
    collect_fmodel_legacy_extensions,
    decode_legacy_identity_chunk,
    decode_legacy_material_links,
    decode_legacy_pbr,
    decode_legacy_textures,
    decode_v10_identity_chunk,
    decode_v10_material_links,
    decode_v10_pbr,
    decode_v10_textures,
)
from .fmodel_payload import FModelImportPayload
from .fmodel_v10_adapter import to_runtime_identity, to_runtime_material_links
from .runtime_fmodel import (
    plan_fmodel_animation_binding,
    pose_identity_issues,
    skeleton_identity_metadata,
    verify_fmodel_target_pose,
)
from .runtime_identity import FModelSkeletonIdentity, compute_skeleton_layout_hash
from .utils import get_active_armature
from .wire import UEFormatHeader, parse_body, parse_header


class UEFormatImport:
    def __init__(
        self,
        options: UEFormatOptions,
        *,
        creation_tracker: UEFormatImportCapture | None = None,
    ) -> None:
        self.options = options
        self.creation_tracker = creation_tracker
        # Populated only when the explicit migration gate is enabled. The
        # standard DTO remains independent from both V9 and V10 FModel data.
        self.last_fmodel_extensions: (
            FModelLegacyExtensionSnapshot | FModelV10ExtensionSnapshot | None
        ) = None
        # V9 identity is decoded only when the explicit migration gate is on.
        # V10 identity remains disabled until its producer contract is frozen.
        self.last_fmodel_identity: FModelIdentityRecord | None = None
        self.last_fmodel_material_links: FModelMaterialLinksSnapshot | None = None
        self.last_fmodel_textures: FModelTextureSnapshot | None = None
        self.last_fmodel_pbr_interfaces: tuple[FModelPBRInterface, ...] = ()

    def _run_transaction(self, operation):
        owns_tracker = self.creation_tracker is None
        tracker = (
            UEFormatImportCapture()
            if self.creation_tracker is None
            else self.creation_tracker
        )
        if owns_tracker:
            self.creation_tracker = tracker
        try:
            return operation()
        except Exception:
            if owns_tracker:
                tracker.rollback()
            raise
        finally:
            if owns_tracker:
                self.creation_tracker = None
                tracker.clear()

    def _extensions_enabled(self) -> bool:
        legacy_override = self.options.enable_fmodel_v10_extensions
        return (
            self.options.enable_fmodel_extensions
            if legacy_override is None
            else legacy_override
        )

    def _decode_fmodel_payload(
        self,
        file_kind: FModelV10FileKind,
    ) -> FModelImportPayload | None:
        """Decode the explicit additive payload after the standard DTO boundary.

        This function is deliberately called only with the user-visible
        extension gate enabled.  A standard-only UEFormat import neither
        constructs a resource resolver nor touches FModel material state.
        """

        snapshot = self.last_fmodel_extensions
        if snapshot is None or not snapshot.present:
            return None

        source_kind = (
            None if file_kind is FModelV10FileKind.MODEL
            else "animation" if file_kind is FModelV10FileKind.ANIM
            else "pose"
        )
        identity = None
        materials = None
        textures = None
        pbr: tuple[FModelPBRInterface, ...] = ()
        identity_attribute = (
            FModelV10Attribute.SKELETON_IDENTITY
            if file_kind is FModelV10FileKind.MODEL
            else FModelV10Attribute.TARGET_SKELETON
        )
        if isinstance(snapshot, FModelV10ExtensionSnapshot):
            identity_payload = snapshot.get(identity_attribute)
            if identity_payload is not None:
                identity = decode_v10_identity_chunk(identity_payload, source_kind=source_kind)
            materials_payload = snapshot.get(FModelV10Attribute.MATERIALS)
            if materials_payload is not None:
                materials = decode_v10_material_links(materials_payload)
            textures_payload = snapshot.get(FModelV10Attribute.TEXTURES)
            if textures_payload is not None:
                textures = decode_v10_textures(textures_payload)
            pbr_payload = snapshot.get(FModelV10Attribute.PBR_INTERFACES)
            if pbr_payload is not None:
                pbr = decode_v10_pbr(pbr_payload)
        else:
            identity_chunk = snapshot.get(identity_attribute)
            if identity_chunk is not None:
                identity = decode_legacy_identity_chunk(identity_chunk, source_kind=source_kind)
            material_chunk = snapshot.get(FModelV10Attribute.MATERIALS)
            if material_chunk is not None:
                materials = decode_legacy_material_links(material_chunk)
            texture_chunk = snapshot.get(FModelV10Attribute.TEXTURES)
            if texture_chunk is not None:
                textures = decode_legacy_textures(texture_chunk)
            pbr_chunk = snapshot.get(FModelV10Attribute.PBR_INTERFACES)
            if pbr_chunk is not None:
                pbr = decode_legacy_pbr(pbr_chunk)

        self.last_fmodel_identity = identity
        self.last_fmodel_material_links = materials
        self.last_fmodel_textures = textures
        self.last_fmodel_pbr_interfaces = pbr
        runtime_identity = to_runtime_identity(identity, source_kind=source_kind)
        payload = FModelImportPayload(
            model_path=getattr(self, "_source_path", None),
            use_v10_section_mapping=isinstance(snapshot, FModelV10ExtensionSnapshot),
        )
        if file_kind is FModelV10FileKind.MODEL:
            payload.skeleton_identity = runtime_identity
            if runtime_identity is not None:
                payload.skeleton_identity_source = "fmodel_extension"
            if materials is not None:
                payload.material_links, payload.embedded_resources = to_runtime_material_links(
                    materials,
                    textures,
                    pbr,
                )
        elif file_kind is FModelV10FileKind.ANIM:
            payload.target_skeleton_identity = runtime_identity
        else:
            payload.target_pose_identity = runtime_identity
        return payload

    @staticmethod
    def _standard_model_identity(dto: ModelDto) -> FModelSkeletonIdentity | None:
        skeleton = dto.skeleton
        if skeleton is None or not skeleton.bones or not skeleton.skeleton_path:
            return None
        try:
            return FModelSkeletonIdentity(
                version=1,
                skeleton_path=skeleton.skeleton_path,
                skeleton_guid=None,
                bone_layout_hash=compute_skeleton_layout_hash(skeleton.bones),
            )
        except (TypeError, ValueError):
            # Official files may omit or use a non-logical metadata path.  Do
            # not make standard UEFormat import fail merely because the
            # optional FModel identity cannot be derived.
            return None

    @staticmethod
    def _armature_identity(armature: Object) -> dict[str, object] | None:
        encoded = armature.get("fmodel_skeleton_identity")
        if encoded is None:
            return None
        try:
            decoded = json.loads(encoded)
        except (TypeError, ValueError) as error:
            raise ValueError("FModel armature skeleton identity is malformed") from error
        if not isinstance(decoded, dict):
            raise ValueError("FModel armature skeleton identity is malformed")
        return decoded

    def import_file(self, path: str | Path) -> Object | Action | None:
        created, _ = self.import_file_with_dto(path)
        return created

    def import_file_with_dto(
        self, path: str | Path,
    ) -> tuple[Object | Action | None, ModelDto | AnimDto | PoseDto]:
        path = path if isinstance(path, Path) else Path(path)
        def import_path():
            Log.time_start(f"Import {path}")
            self._source_path = path
            try:
                with path.open("rb") as file:
                    result = self.import_data_with_dto(file.read())
            finally:
                del self._source_path
            Log.time_end(f"Import {path}")
            return result

        return self._run_transaction(import_path)

    def import_data(self, data: bytes) -> Object | Action | None:
        created, _ = self.import_data_with_dto(data)
        return created

    def import_data_with_dto(
        self, data: bytes,
    ) -> tuple[Object | Action | None, ModelDto | AnimDto | PoseDto]:
        return self._run_transaction(
            lambda: self._import_data_with_dto(data),
        )

    def _import_data_with_dto(
        self, data: bytes,
    ) -> tuple[Object | Action | None, ModelDto | AnimDto | PoseDto]:
        with FArchiveReader(data) as ar:
            return self.import_data_by_reader_with_dto(ar)

    def import_data_by_reader(self, ar: FArchiveReader) -> Object | Action | None:
        created, _ = self.import_data_by_reader_with_dto(ar)
        return created

    def import_data_by_reader_with_dto(
        self, ar: FArchiveReader,
    ) -> tuple[Object | Action | None, ModelDto | AnimDto | PoseDto]:
        return self._run_transaction(
            lambda: self._import_data_by_reader_with_dto(ar),
        )

    def _import_data_by_reader_with_dto(
        self, ar: FArchiveReader,
    ) -> tuple[Object | Action | None, ModelDto | AnimDto | PoseDto]:
        header, body = parse_header(ar)
        self.last_fmodel_identity = None
        self.last_fmodel_material_links = None
        self.last_fmodel_textures = None
        self.last_fmodel_pbr_interfaces = ()
        extensions_enabled = self._extensions_enabled()
        if header.file_version >= EUEFormatVersion.AttributeFormatRestructure:
            body_bytes = body.read_to_end()
            if extensions_enabled:
                try:
                    file_kind = FModelV10FileKind(header.identifier)
                except ValueError:
                    self.last_fmodel_extensions = None
                else:
                    standard_attributes = {
                        FModelV10FileKind.MODEL: ("LODS", "SKELETON", "COLLISION"),
                        FModelV10FileKind.ANIM: ("METADATA", "TRACKS", "CURVES"),
                        FModelV10FileKind.POSE: ("POSES", "CURVES"),
                    }[file_kind]
                    self.last_fmodel_extensions = collect_fmodel_extensions(
                        file_kind,
                        body_bytes,
                        known_standard_attributes=standard_attributes,
                    )
            else:
                self.last_fmodel_extensions = None
            body = FArchiveReader(body_bytes)
            body.file_version = header.file_version
        elif extensions_enabled:
            body_bytes = body.read_to_end()
            try:
                file_kind = FModelV10FileKind(header.identifier)
            except ValueError:
                self.last_fmodel_extensions = None
            else:
                self.last_fmodel_extensions = collect_fmodel_legacy_extensions(
                    file_kind,
                    body_bytes,
                )
            body = FArchiveReader(body_bytes)
            body.file_version = header.file_version
        else:
            self.last_fmodel_extensions = None
        dto = parse_body(header, body)
        to_blender_space(dto, header.file_version, self.options.scale_factor)

        if isinstance(dto, ModelDto):
            assert isinstance(self.options, UEModelOptions)
            payload = self._decode_fmodel_payload(FModelV10FileKind.MODEL) if extensions_enabled else None
            if payload is None:
                payload = FModelImportPayload(model_path=getattr(self, "_source_path", None))
            if payload.skeleton_identity is None:
                payload.skeleton_identity = self._standard_model_identity(dto)
                if payload.skeleton_identity is not None:
                    payload.skeleton_identity_source = "standard_metadata"
            return create_model(
                dto,
                self.options,
                header.object_name,
                payload,
                creation_tracker=self.creation_tracker,
            ), dto
        if isinstance(dto, AnimDto):
            assert isinstance(self.options, UEAnimOptions)
            payload = self._decode_fmodel_payload(FModelV10FileKind.ANIM) if extensions_enabled else None
            target_identity = payload.target_skeleton_identity if payload is not None else None
            armature = self.options.override_skeleton or get_active_armature()
            binding_plan = None
            if target_identity is not None:
                if not isinstance(armature, bpy.types.Object):
                    raise ValueError("FModel animation requires an active armature")
                binding_plan = plan_fmodel_animation_binding(
                    skeleton_identity_metadata(target_identity),
                    self._armature_identity(armature),
                    (track.name for track in dto.tracks),
                    (bone.name for bone in armature.pose.bones),
                    strict=self.options.strict_fmodel_skeleton,
                )
            return create_anim(
                dto,
                self.options,
                header.object_name,
                binding_plan=binding_plan,
                target_identity=target_identity,
                creation_tracker=self.creation_tracker,
            ), dto
        if isinstance(dto, PoseDto):
            assert isinstance(self.options, UEPoseOptions)
            payload = self._decode_fmodel_payload(FModelV10FileKind.POSE) if extensions_enabled else None
            target_identity = payload.target_pose_identity if payload is not None else None
            if target_identity is not None:
                armature = self.options.override_skeleton or get_active_armature()
                if not isinstance(armature, bpy.types.Object):
                    raise ValueError("FModel pose requires an active armature")
                issues = pose_identity_issues(
                    skeleton_identity_metadata(target_identity),
                    self._armature_identity(armature),
                )
                if not issues:
                    try:
                        verify_fmodel_target_pose(
                            skeleton_identity_metadata(target_identity),
                            self._armature_identity(armature),
                        )
                    except ValueError as error:
                        issues.append(str(error))
                if issues and self.options.strict_fmodel_skeleton:
                    raise ValueError("; ".join(issues))
                for issue in issues:
                    Log.warn(f"FModel pose compatibility is permissive: {issue}")
            create_pose(
                dto,
                self.options,
                header.object_name,
                target_identity=target_identity,
                creation_tracker=self.creation_tracker,
            )
            return None, dto

        raise ValueError(f"Unsupported DTO: {type(dto)}")
