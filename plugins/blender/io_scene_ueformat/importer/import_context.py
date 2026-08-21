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
from .extensions.fmodel import FModelV10ExtensionSnapshot, FModelV10FileKind, collect_fmodel_extensions
from .wire import parse_body, parse_header


class UEFormatImport:
    def __init__(self, options: UEFormatOptions) -> None:
        self.options = options
        # Populated for V10 files only.  The standard DTO remains independent
        # until the feature-specific FModel decoder is enabled in a later
        # migration phase.
        self.last_fmodel_extensions: FModelV10ExtensionSnapshot | None = None

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
        if header.file_version >= EUEFormatVersion.AttributeFormatRestructure:
            body_bytes = body.read_to_end()
            if self.options.enable_fmodel_v10_extensions:
                file_kind = FModelV10FileKind(header.identifier)
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
