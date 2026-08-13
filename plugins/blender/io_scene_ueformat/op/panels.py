from typing import cast

import bpy
from bpy.types import Context, Operator, Panel

from ..typing import UFormatContext, UFSettings


class UEFORMAT_PT_Layout:  # noqa: N801
    @staticmethod
    def draw_general_options(obj: Panel | Operator, settings: UFSettings) -> None:
        box = obj.layout.box()
        box.label(text="General", icon="SETTINGS")
        box.row().prop(settings, "scale_factor")

    @staticmethod
    def draw_model_options(
        obj: Panel | Operator,
        settings: UFSettings,
        *,
        import_menu: bool = False,
    ) -> None:
        box = obj.layout.box()
        box.label(text="Model Import", icon="OUTLINER_OB_MESH")

        geometry = box.box()
        geometry.label(text="Geometry", icon="MESH_DATA")
        geometry.row().prop(settings, "target_lod")
        geometry.row().prop(settings, "import_collision")
        geometry.row().prop(settings, "import_morph_targets")
        geometry.row().prop(settings, "import_sockets")

        skeleton = box.box()
        skeleton.label(text="Skeleton", icon="ARMATURE_DATA")
        skeleton.row().prop(settings, "reorient_bones")
        skeleton.row().prop(settings, "bone_length")
        advanced = skeleton.box()
        advanced.label(text="Advanced", icon="SETTINGS")
        advanced.row().prop(settings, "import_virtual_bones")

        materials = box.box()
        materials.label(text="Materials", icon="MATERIAL")
        materials.row().prop(settings, "import_fmodel_materials")
        material_options = materials.column()
        material_options.enabled = settings.import_fmodel_materials
        material_options.row().prop(settings, "build_fmodel_material_nodes")
        material_options.row().prop(settings, "load_fmodel_embedded_textures")
        material_options.row().prop(settings, "keep_fmodel_unmapped_parameters")
        material_options.row().prop(settings, "load_fmodel_external_textures")
        external_texture_options = material_options.column()
        external_texture_options.enabled = (
            settings.import_fmodel_materials and settings.load_fmodel_external_textures
        )
        external_texture_options.row().prop(settings, "fmodel_ue_export_root")
        external_texture_options.row().prop(settings, "fmodel_bundle_root")

        if not import_menu:
            box.row().operator("uf.import_uemodel", text="Import UE Model", icon="MESH_DATA")

    @staticmethod
    def draw_anim_options(
        obj: Panel | Operator,
        settings: UFSettings,
        *,
        import_menu: bool = False,
    ) -> None:
        box = obj.layout.box()
        box.label(text="Animation Import", icon="ACTION")
        box.row().prop(settings, "rotation_only")
        box.row().prop(settings, "import_curves")
        box.row().prop(settings, "strict_fmodel_skeleton")

        if not import_menu:
            box.row().operator("uf.import_ueanim", text="Import UE Animation", icon="ANIM")

    @staticmethod
    def draw_pose_options(
        obj: Panel | Operator,
        settings: UFSettings,
        *,
        import_menu: bool = False,
    ) -> None:
        box = obj.layout.box()
        box.label(text="Pose Import", icon="OUTLINER_OB_ARMATURE")

        if not import_menu:
            box.row().operator("uf.import_uepose", text="Import UE Pose", icon="ARMATURE_DATA")


class UEFORMAT_PT_Model(Panel):  # noqa: N801
    bl_category = "UE Format"
    bl_label = "UE Format: Model"
    bl_order = 1
    bl_region_type = "UI"
    bl_space_type = "VIEW_3D"

    def draw(self, context: Context | UFormatContext) -> None:
        assert hasattr(context.scene, "uf_settings")  # noqa: S101

        context = cast(UFormatContext, context)
        UEFORMAT_PT_Layout.draw_model_options(self, context.scene.uf_settings)


class UEFORMAT_PT_Animation(Panel):  # noqa: N801
    bl_category = "UE Format"
    bl_label = "UE Format: Animation"
    bl_order = 2
    bl_region_type = "UI"
    bl_space_type = "VIEW_3D"

    def draw(self, context: Context | UFormatContext) -> None:
        assert hasattr(context.scene, "uf_settings")  # noqa: S101

        context = cast(UFormatContext, context)
        UEFORMAT_PT_Layout.draw_anim_options(self, context.scene.uf_settings)
        UEFORMAT_PT_Layout.draw_pose_options(self, context.scene.uf_settings)
