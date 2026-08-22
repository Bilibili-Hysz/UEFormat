from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bpy.types import Armature

    from io_scene_ueformat.op.settings import UFSettings


@dataclass(slots=True)
class UEFormatOptions:
    link: bool = True
    scale_factor: float = 0.01
    # FModel attributes are additive to the public UEFormat body.  Keeping the
    # gate explicit lets a caller import a strictly standard file without even
    # decoding the optional attributes, while the normal FModel workflow keeps
    # the feature enabled by default.
    enable_fmodel_extensions: bool = True
    # Compatibility spelling for programmatic callers of the migration branch.
    # UI settings only serialize ``enable_fmodel_extensions``.
    enable_fmodel_v10_extensions: bool | None = None

    @classmethod
    def from_settings(cls, settings: UFSettings) -> UEFormatOptions:
        return cls(**{
            k: v for k, v in settings.get_props().items()
            if k in inspect.signature(cls).parameters
        })


@dataclass(slots=True)
class UEModelOptions(UEFormatOptions):
    bone_length: float = 4.0
    reorient_bones: bool = False
    import_collision: bool = False
    import_sockets: bool = True
    import_morph_targets: bool = True
    import_virtual_bones: bool = False
    target_lod: int = 0
    allowed_reorient_children: dict = None
    import_fmodel_materials: bool = True
    build_fmodel_material_nodes: bool = True
    load_fmodel_embedded_textures: bool = True
    load_fmodel_external_textures: bool = True
    keep_fmodel_unmapped_parameters: bool = True
    fmodel_ue_export_root: str = ""
    fmodel_bundle_root: str = ""


@dataclass(slots=True)
class UEAnimOptions(UEFormatOptions):
    rotation_only: bool = False
    import_curves: bool = True
    override_skeleton: Armature | None = None
    strict_fmodel_skeleton: bool = True

@dataclass(slots=True)
class UEPoseOptions(UEFormatOptions):
    root_bone: str = ""
    override_skeleton: Armature | None = None
    strict_fmodel_skeleton: bool = True
