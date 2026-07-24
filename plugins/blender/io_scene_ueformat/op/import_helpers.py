import json
from pathlib import Path
from typing import Generic, TypeVar

import bpy
from bpy.props import CollectionProperty, StringProperty
from bpy.types import Operator, OperatorFileListElement, FileHandler
from bpy_extras.io_utils import ImportHelper, poll_file_object_drop

from .panels import UEFORMAT_PT_Layout
from ..importer.logic import UEFormatImport
from ..importer.material_builder import FModelMaterialImportOptions
from ..importer.scene_import import RequiredModelImportError, import_uescene_plan
from ..importer.uescene import RequiredModelError, UEScene, UESceneError, infer_bundle_root
from ..options import UEAnimOptions, UEFormatOptions, UEModelOptions, UEPoseOptions
from ..typing import UFormatContext

T = TypeVar("T", bound=UEFormatOptions)


def _set_last_import_report(settings, summary: str, report: str) -> None:
    settings.last_import_summary = summary
    settings.last_import_report = report


def _set_scene_import_status(settings, outcome: str, failures=()) -> None:
    settings.last_import_status = json.dumps({
        "outcome": outcome,
        "required_model_failures": [{
            "component_id": str(failure.component_id),
            "asset_id": str(failure.asset_id),
            "code": failure.code,
            "uri": failure.uri,
            "path": str(failure.path) if failure.path else None,
            "detail": failure.detail,
        } for failure in failures],
    }, separators=(",", ":"), sort_keys=True)


def _material_options(settings) -> FModelMaterialImportOptions:
    return FModelMaterialImportOptions(
        import_materials=settings.import_fmodel_materials,
        build_nodes=settings.build_fmodel_material_nodes,
        load_embedded=settings.load_fmodel_embedded_textures,
        load_external=settings.load_fmodel_external_textures,
        keep_unmapped=settings.keep_fmodel_unmapped_parameters,
        ue_export_root=settings.fmodel_ue_export_root or None,
        bundle_root=settings.fmodel_bundle_root or None,
    )


_UESCENE_HIERARCHY_PROPERTIES = {
    "uescene_actor_id", "uescene_parent_actor_id", "uescene_component_id",
    "uescene_parent_component_id", "uescene_component_kind", "uescene_source_path",
    "uescene_class_path",
    "uescene_asset_id", "uescene_asset_name", "uescene_asset_path", "uescene_model_path",
    "uescene_material_overrides", "uescene_import_state", "uescene_instance_set_id",
    "uescene_instance_count", "uescene_visibility_flags",
}


def _is_flattenable_uescene_empty(obj) -> bool:
    return (
        obj.type == "EMPTY"
        and obj.data is None
        and ("uescene_actor_id" in obj or "uescene_component_id" in obj)
        and not (obj.constraints or obj.animation_data or obj.instance_collection)
        and set(obj.keys()).issubset(_UESCENE_HIERARCHY_PROPERTIES)
    )


def _flatten_uescene_instances(collection) -> int:
    """Materialize imported collection instances, then remove their source templates."""
    controllers = [
        obj for obj in collection.all_objects
        if obj.get("uescene_instance_representation") == "Collection Instance"
    ]
    removed = 0
    templates = set()
    bpy.context.view_layer.update()
    for controller in controllers:
        for instance in tuple(controller.children):
            template = instance.instance_collection
            if instance.instance_type != "COLLECTION" or template is None:
                continue
            templates.add(template)
            copies = {}
            for source in template.all_objects:
                copy = source.copy()
                if source.data is not None:
                    copy.data = source.data
                collection.objects.link(copy)
                copy.matrix_world = instance.matrix_world @ source.matrix_world
                copies[source.as_pointer()] = copy
            for source in template.all_objects:
                copy = copies[source.as_pointer()]
                if source.parent is not None and source.parent.as_pointer() in copies:
                    copy.parent = copies[source.parent.as_pointer()]
                    copy.matrix_parent_inverse = source.matrix_parent_inverse
            bpy.data.objects.remove(instance, do_unlink=True)
        bpy.data.objects.remove(controller, do_unlink=True)
        removed += 1
    for template in templates:
        for source in tuple(template.all_objects):
            bpy.data.objects.remove(source, do_unlink=True)
        bpy.data.collections.remove(template, do_unlink=True)
    for child in tuple(collection.children):
        if child.name == "Instanced Geometry" and not child.objects and not child.children:
            bpy.data.collections.remove(child, do_unlink=True)
        elif child.name == "UEFormat Technical":
            for technical_child in tuple(child.children):
                if technical_child.name == "Source Templates" and not technical_child.objects and not technical_child.children:
                    bpy.data.collections.remove(technical_child, do_unlink=True)
            if not child.objects and not child.children:
                bpy.data.collections.remove(child, do_unlink=True)
    return removed


def flatten_uescene_hierarchy(collection, flatten_instances: bool = False) -> int:
    """Remove imported hierarchy empties without changing child world transforms."""
    removed_instances = _flatten_uescene_instances(collection) if flatten_instances else 0
    bpy.context.view_layer.update()
    candidates = [obj for obj in collection.all_objects if _is_flattenable_uescene_empty(obj)]
    world_matrices = {
        obj.as_pointer(): obj.matrix_world.copy()
        for obj in collection.all_objects
    }

    def depth(obj) -> int:
        value = 0
        while obj.parent is not None:
            value += 1
            obj = obj.parent
        return value

    candidates.sort(key=depth, reverse=True)
    removed = 0
    for obj in candidates:
        if obj.name not in bpy.data.objects or not _is_flattenable_uescene_empty(obj):
            continue
        for child in tuple(obj.children):
            child.parent = obj.parent
        bpy.data.objects.remove(obj, do_unlink=True)
        removed += 1
    bpy.context.view_layer.update()
    for obj in collection.all_objects:
        matrix = world_matrices.get(obj.as_pointer())
        if matrix is not None:
            obj.matrix_world = matrix
    return removed + removed_instances


class UFImportBase(Operator, ImportHelper, Generic[T]):
    bl_context = "scene"
    files: CollectionProperty(
        type=OperatorFileListElement,
        options={"HIDDEN", "SKIP_SAVE"},
    )
    directory: StringProperty(subtype="DIR_PATH")

    options_class: type[T]

    def execute(self, context: UFormatContext) -> set[str]:
        settings = context.scene.uf_settings
        options = self.options_class.from_settings(settings)
        material_options = None
        if isinstance(options, UEModelOptions):
            material_options = _material_options(settings)

        directory = Path(self.directory)
        paths = []
        for file in self.files:
            file: OperatorFileListElement
            path = directory / file.name
            try:
                UEFormatImport(options, material_options).import_file(path)
            except Exception as error:
                kind = {
                    UEModelOptions: "Model",
                    UEAnimOptions: "Animation",
                    UEPoseOptions: "Pose",
                }[self.options_class]
                _set_last_import_report(
                    settings,
                    f"{kind} import failed",
                    "\n".join((
                        "UE Format Import Report",
                        f"Kind: {kind}",
                        "Status: Failed",
                        f"File: {path}",
                        f"Error: {error}",
                    )),
                )
                raise
            paths.append(str(path))

        kind = {
            UEModelOptions: "Model",
            UEAnimOptions: "Animation",
            UEPoseOptions: "Pose",
        }[self.options_class]
        summary = f"{kind} import: {len(paths)} file(s)"
        _set_last_import_report(
            settings,
            summary,
            "\n".join((
                "UE Format Import Report",
                f"Kind: {kind}",
                "Status: Success",
                "Files:",
                *(f"- {path}" for path in paths),
            )),
        )

        return {"FINISHED"}

    def invoke(self, context, event):
        return ImportHelper.invoke_popup(self, context)

class UFImportUEModel(UFImportBase):
    bl_idname = "uf.import_uemodel"
    bl_label = "Import Model"
    bl_description = "Import Model"

    filename_ext = ".uemodel"
    filter_glob: StringProperty(default="*.uemodel", options={"HIDDEN"}, maxlen=255)

    options_class = UEModelOptions

    def draw(self, context: UFormatContext) -> None:
        UEFORMAT_PT_Layout.draw_general_options(self, context.scene.uf_settings)
        UEFORMAT_PT_Layout.draw_model_options(
            self,
            context.scene.uf_settings,
            import_menu=True,
        )

class UFImportUEAnim(UFImportBase):
    bl_idname = "uf.import_ueanim"
    bl_label = "Import Animation"
    bl_description = "Import Animation"

    filename_ext = ".ueanim"
    filter_glob: StringProperty(default="*.ueanim", options={"HIDDEN"}, maxlen=255)

    options_class = UEAnimOptions

    def draw(self, context: UFormatContext) -> None:
        UEFORMAT_PT_Layout.draw_general_options(self, context.scene.uf_settings)
        UEFORMAT_PT_Layout.draw_anim_options(
            self,
            context.scene.uf_settings,
            import_menu=True,
        )

class UFImportUEPose(UFImportBase):
    bl_idname = "uf.import_uepose"
    bl_label = "Import Pose"
    bl_description = "Import Pose"

    filename_ext = ".uepose"
    filter_glob: StringProperty(default="*.uepose", options={"HIDDEN"}, maxlen=255)

    options_class = UEPoseOptions

    def draw(self, context: UFormatContext) -> None:
        UEFORMAT_PT_Layout.draw_general_options(self, context.scene.uf_settings)
        UEFORMAT_PT_Layout.draw_pose_options(
            self,
            context.scene.uf_settings,
            import_menu=True,
        )


class UFImportUEScene(Operator, ImportHelper):
    bl_idname = "uf.import_uescene"
    bl_label = "Import UE Scene"
    bl_description = "Import UE Scene"

    filename_ext = ".uescene"
    filter_glob: StringProperty(default="*.uescene", options={"HIDDEN"}, maxlen=255)

    def draw(self, context: UFormatContext) -> None:
        UEFORMAT_PT_Layout.draw_scene_options(
            self,
            context.scene.uf_settings,
            import_menu=True,
        )
        UEFORMAT_PT_Layout.draw_general_options(self, context.scene.uf_settings)
        UEFORMAT_PT_Layout.draw_model_options(
            self,
            context.scene.uf_settings,
            import_menu=True,
        )

    def execute(self, context: UFormatContext) -> set[str]:
        settings = context.scene.uf_settings
        scene_path = Path(self.filepath)
        try:
            scene = UEScene.from_file(scene_path)
            plan = scene.build_import_plan(
                scene_path,
                infer_bundle_root(scene_path, settings.fmodel_bundle_root, scene.metadata.optional_features),
                strict_required_models=settings.strict_uescene_models,
            )
        except RequiredModelError as error:
            _set_scene_import_status(settings, "failed", error.failures)
            _set_last_import_report(
                settings,
                "UE Scene import failed",
                "\n".join((
                    "UE Format Import Report",
                    "Kind: Scene",
                    "Status: Failed",
                    f"File: {scene_path}",
                    f"Error: {error}",
                )),
            )
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        except UESceneError as error:
            _set_scene_import_status(settings, "failed")
            _set_last_import_report(
                settings,
                "UE Scene import failed",
                "\n".join((
                    "UE Format Import Report",
                    "Kind: Scene",
                    "Status: Failed",
                    f"File: {scene_path}",
                    f"Error: {error}",
                )),
            )
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}

        options = UEModelOptions.from_settings(settings)
        try:
            summary = import_uescene_plan(
                plan,
                options,
                _material_options(settings),
                strict_required_models=settings.strict_uescene_models,
            )
        except RequiredModelImportError as error:
            _set_scene_import_status(settings, "failed", error.failures)
            _set_last_import_report(
                settings,
                "UE Scene import failed",
                "\n".join((
                    "UE Format Import Report",
                    "Kind: Scene",
                    "Status: Failed",
                    f"File: {scene_path}",
                    f"Error: {error}",
                )),
            )
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        _set_scene_import_status(settings, summary.outcome, summary.required_model_failures)
        flattened = flatten_uescene_hierarchy(
            summary.collection, settings.flatten_uescene_instances,
        ) if (settings.flatten_uescene_hierarchy or settings.flatten_uescene_instances) else 0
        message = (
            f"Imported {summary.imported_actors} actor(s), {summary.imported_components} component(s), "
            f"{summary.imported_static_meshes} static mesh(es), and {summary.imported_lights} light(s); {summary.unresolved_models} unresolved, "
            f"{summary.failed_models} failed, {summary.deferred_instances} deferred"
        )
        if settings.flatten_uescene_hierarchy or settings.flatten_uescene_instances:
            message += f", {flattened} hierarchy node(s) flattened"
        if summary.warnings or plan.diagnostics:
            self.report({"WARNING"}, message)
            for warning in summary.warnings:
                self.report({"WARNING"}, warning)
            for diagnostic in plan.diagnostics:
                self.report({"WARNING"}, f"scene diagnostic {diagnostic.code}: {diagnostic.message}")
        else:
            self.report({"INFO"}, message)
        report = [
            "UE Format Import Report",
            "Kind: Scene",
            f"Status: {'Partial' if summary.outcome == 'partial' else 'Success'}",
            f"File: {scene_path}",
            f"Bundle Root: {plan.bundle_root or '<none>'}",
            message,
        ]
        if summary.warnings:
            report.extend(("Warnings:", *(f"- {warning}" for warning in summary.warnings)))
        if plan.diagnostics:
            report.extend(("Scene Diagnostics:", *(f"- [{diagnostic.code}] {diagnostic.message}" for diagnostic in plan.diagnostics)))
        _set_last_import_report(settings, message, "\n".join(report))
        return {"FINISHED"}


class UFCopyLastImportReport(Operator):
    bl_idname = "uf.copy_last_import_report"
    bl_label = "Copy Last Import Report"
    bl_description = "Copy the last UE Format import report to the clipboard"

    @classmethod
    def poll(cls, context) -> bool:
        return bool(getattr(context.scene.uf_settings, "last_import_report", ""))

    def execute(self, context: UFormatContext) -> set[str]:
        context.window_manager.clipboard = context.scene.uf_settings.last_import_report
        self.report({"INFO"}, "Copied last import report to clipboard")
        return {"FINISHED"}


class UFFlattenUESceneHierarchy(Operator):
    bl_idname = "uf.flatten_uescene_hierarchy"
    bl_label = "Flatten UEScene Hierarchy"
    bl_description = "Remove safe imported Empty hierarchy nodes while preserving child world transforms"

    @classmethod
    def poll(cls, context) -> bool:
        collection = getattr(context, "collection", None)
        return collection is not None and collection.name.endswith("_uescene")

    def execute(self, context: UFormatContext) -> set[str]:
        collection = context.collection
        removed = flatten_uescene_hierarchy(collection, flatten_instances=True)
        settings = context.scene.uf_settings
        if settings.last_import_report:
            settings.last_import_summary = f"Flattened {removed} UEScene hierarchy node(s)"
            settings.last_import_report += f"\nPost-Import Actions:\n- Flattened {removed} UEScene hierarchy node(s)"
        self.report({"INFO"}, f"Flattened {removed} UEScene hierarchy node(s)")
        return {"FINISHED"}


# drag and drop handler
class IO_FH_ueformatBase(FileHandler):
    @classmethod
    def poll_drop(cls, context):
        return poll_file_object_drop(context)

class IO_FH_uemodel(IO_FH_ueformatBase):
    bl_idname = "IO_FH_uemodel"
    bl_label = "UEFormat Model"
    bl_import_operator = UFImportUEModel.bl_idname
    bl_file_extensions = ".uemodel"

class IO_FH_ueanim(IO_FH_ueformatBase):
    bl_idname = "IO_FH_ueanim"
    bl_label = "UEFormat Animation"
    bl_import_operator = UFImportUEAnim.bl_idname
    bl_file_extensions = ".ueanim"

class IO_FH_uepose(IO_FH_ueformatBase):
    bl_idname = "IO_FH_uepose"
    bl_label = "UEFormat Pose"
    bl_import_operator = UFImportUEPose.bl_idname
    bl_file_extensions = ".uepose"


class IO_FH_uescene(IO_FH_ueformatBase):
    bl_idname = "IO_FH_uescene"
    bl_label = "UE Scene"
    bl_import_operator = UFImportUEScene.bl_idname
    bl_file_extensions = ".uescene"
