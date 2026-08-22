from bpy.props import BoolProperty, FloatProperty, IntProperty, StringProperty
from bpy.types import PropertyGroup
from typing import Any


class UFSettings(PropertyGroup):
    scale_factor: FloatProperty(name="Scale", default=0.01, min=0.01)
    
    bone_length: FloatProperty(name="Bone Length", default=4.0, min=0.1)
    reorient_bones: BoolProperty(name="Reorient Bones", default=False)
    target_lod: IntProperty(name="Level of Detail", default=0, min=0)
    import_collision: BoolProperty(name="Import Collision", default=False)
    import_morph_targets: BoolProperty(name="Import Morph Targets", default=True)
    import_sockets: BoolProperty(name="Import Sockets", default=True)
    import_virtual_bones: BoolProperty(name="Import Virtual Bones", default=False)
    enable_fmodel_extensions: BoolProperty(name="Read FModel Extensions", default=True)
    import_fmodel_materials: BoolProperty(name="Import FModel Materials", default=True)
    build_fmodel_material_nodes: BoolProperty(name="Build Material Nodes", default=True)
    load_fmodel_embedded_textures: BoolProperty(name="Load Embedded Textures", default=True)
    load_fmodel_external_textures: BoolProperty(name="Load External Textures", default=True)
    keep_fmodel_unmapped_parameters: BoolProperty(name="Keep Unmapped Parameters", default=True)
    fmodel_ue_export_root: StringProperty(name="UE Export Root", default="", subtype="DIR_PATH")
    fmodel_bundle_root: StringProperty(name="Bundle Root", default="", subtype="DIR_PATH")

    rotation_only: BoolProperty(name="Rotation Only", default=False)
    import_curves: BoolProperty(name="Import Curves", default=True)
    strict_fmodel_skeleton: BoolProperty(name="Strict FModel Skeleton", default=True)
    last_import_summary: StringProperty(default="", options={"HIDDEN"})
    last_import_report: StringProperty(default="", options={"HIDDEN"})
    last_import_status: StringProperty(default="", options={"HIDDEN"})

    def get_props(self) -> dict[str, Any]:
        props = {}

        for key in getattr(type(self), '__annotations__', {}).keys():
            props[key] = getattr(self, key)

        return props
