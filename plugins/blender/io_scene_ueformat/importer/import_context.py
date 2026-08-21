from __future__ import annotations

from pathlib import Path

from bpy.types import Action, Object

from ..logging import Log
from ..options import UEAnimOptions, UEFormatOptions, UEModelOptions, UEPoseOptions
from .archive.reader import FArchiveReader
from .create.anim import create_anim
from .create.model import create_model
from .create.pose import create_pose
from .create.space import to_blender_space
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
)
from .wire import UEFormatHeader, parse_body, parse_header


class UEFormatImport:
    def __init__(self, options: UEFormatOptions) -> None:
        self.options = options
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

    def import_file(self, path: str | Path) -> Object | Action | None:
        created, _ = self.import_file_with_dto(path)
        return created

    def import_file_with_dto(
        self, path: str | Path,
    ) -> tuple[Object | Action | None, ModelDto | AnimDto | PoseDto]:
        path = path if isinstance(path, Path) else Path(path)
        Log.time_start(f"Import {path}")
        with path.open("rb") as file:
            result = self.import_data_with_dto(file.read())
        Log.time_end(f"Import {path}")
        return result

    def import_data(self, data: bytes) -> Object | Action | None:
        created, _ = self.import_data_with_dto(data)
        return created

    def import_data_with_dto(
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
        header, body = parse_header(ar)
        self.last_fmodel_identity = None
        self.last_fmodel_material_links = None
        self.last_fmodel_textures = None
        self.last_fmodel_pbr_interfaces = ()
        if header.file_version >= EUEFormatVersion.AttributeFormatRestructure:
            body_bytes = body.read_to_end()
            if self.options.enable_fmodel_v10_extensions:
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
        elif self.options.enable_fmodel_v10_extensions:
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
                identity_attribute = (
                    FModelV10Attribute.SKELETON_IDENTITY
                    if file_kind is FModelV10FileKind.MODEL
                    else FModelV10Attribute.TARGET_SKELETON
                )
                identity_chunk = self.last_fmodel_extensions.get(identity_attribute)
                if identity_chunk is not None:
                    source_kind = (
                        None
                        if file_kind is FModelV10FileKind.MODEL
                        else ("animation" if file_kind is FModelV10FileKind.ANIM else "pose")
                    )
                    self.last_fmodel_identity = decode_legacy_identity_chunk(
                        identity_chunk,
                        source_kind=source_kind,
                    )
                material_chunk = self.last_fmodel_extensions.get(FModelV10Attribute.MATERIALS)
                if material_chunk is not None:
                    self.last_fmodel_material_links = decode_legacy_material_links(material_chunk)
                texture_chunk = self.last_fmodel_extensions.get(FModelV10Attribute.TEXTURES)
                if texture_chunk is not None:
                    self.last_fmodel_textures = decode_legacy_textures(texture_chunk)
                pbr_chunk = self.last_fmodel_extensions.get(FModelV10Attribute.PBR_INTERFACES)
                if pbr_chunk is not None:
                    self.last_fmodel_pbr_interfaces = decode_legacy_pbr(pbr_chunk)
            body = FArchiveReader(body_bytes)
            body.file_version = header.file_version
        else:
            self.last_fmodel_extensions = None
        dto = parse_body(header, body)
        to_blender_space(dto, header.file_version, self.options.scale_factor)

        if isinstance(dto, ModelDto):
            assert isinstance(self.options, UEModelOptions)
            return create_model(dto, self.options, header.object_name), dto
        if isinstance(dto, AnimDto):
            assert isinstance(self.options, UEAnimOptions)
            return create_anim(dto, self.options, header.object_name), dto
        if isinstance(dto, PoseDto):
            assert isinstance(self.options, UEPoseOptions)
            create_pose(dto, self.options, header.object_name)
            return None, dto

        raise ValueError(f"Unsupported DTO: {type(dto)}")
