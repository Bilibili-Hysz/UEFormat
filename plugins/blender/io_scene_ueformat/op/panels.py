from typing import cast

from bpy.types import Context, Operator, Panel

from ..typing import UFormatContext, UFSettings


class UEFORMAT_PT_Panel(Panel):  # noqa: N801
    bl_category = "UE Format"
    bl_label = "UE Format"
    bl_region_type = "UI"
    bl_space_type = "VIEW_3D"

    def draw(self, context: Context | UFormatContext) -> None:
        assert hasattr(context.scene, "uf_settings")  # noqa: S101

        context = cast(UFormatContext, context)
        uf_settings = context.scene.uf_settings

        self.draw_general_options(self, uf_settings)
        self.draw_model_options(self, uf_settings)
        self.draw_anim_options(self, uf_settings)
        self.draw_pose_options(self, uf_settings)

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
        box.label(text="Model", icon="OUTLINER_OB_MESH")
        box.row().prop(settings, "target_lod")
        box.row().prop(settings, "import_collision")
        box.row().prop(settings, "import_morph_targets")
        box.row().prop(settings, "import_sockets")
        box.row().prop(settings, "import_virtual_bones")
        box.row().prop(settings, "reorient_bones")
        box.row().prop(settings, "bone_length")

        materials = box.box()
        materials.label(text="FModel Materials", icon="MATERIAL")
        materials.row().prop(settings, "enable_fmodel_extensions")
        material_options = materials.column()
        material_options.enabled = settings.enable_fmodel_extensions
        material_options.row().prop(settings, "import_fmodel_materials")
        material_build = material_options.column()
        material_build.enabled = settings.import_fmodel_materials
        material_build.row().prop(settings, "build_fmodel_material_nodes")
        material_build.row().prop(settings, "load_fmodel_embedded_textures")
        material_build.row().prop(settings, "load_fmodel_external_textures")
        material_build.row().prop(settings, "keep_fmodel_unmapped_parameters")
        external_roots = material_build.column()
        external_roots.enabled = settings.load_fmodel_external_textures
        external_roots.row().prop(settings, "fmodel_ue_export_root")
        external_roots.row().prop(settings, "fmodel_bundle_root")

        if not import_menu:
            box.row().operator("uf.import_uemodel", icon="MESH_DATA")
            report = box.row()
            report.enabled = bool(settings.last_import_report)
            report.operator("uf.copy_last_import_report", text="Copy Last Import Report", icon="COPYDOWN")

    @staticmethod
    def draw_anim_options(
        obj: Panel | Operator,
        settings: UFSettings,
        *,
        import_menu: bool = False,
    ) -> None:
        box = obj.layout.box()
        box.label(text="Animation", icon="ACTION")
        box.row().prop(settings, "rotation_only")
        box.row().prop(settings, "import_curves")

        if not import_menu:
            box.row().operator("uf.import_ueanim", icon="ANIM")

    @staticmethod
    def draw_pose_options(
            obj: Panel | Operator,
            settings: UFSettings,
            *,
            import_menu: bool = False,
    ) -> None:
        box = obj.layout.box()
        box.label(text="Pose", icon="OUTLINER_OB_ARMATURE")

        if not import_menu:
            box.row().operator("uf.import_uepose", icon="ARMATURE_DATA")
