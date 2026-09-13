from pathlib import Path
from typing import Generic, TypeVar

from bpy.props import CollectionProperty, StringProperty
from bpy.types import Operator, OperatorFileListElement, FileHandler
from bpy_extras.io_utils import ImportHelper, poll_file_object_drop

from .panels import UEFORMAT_PT_Panel
from ..importer.import_context import UEFormatImport
from ..importer.creation_tracker import UEFormatImportCapture
from ..options import UEAnimOptions, UEFormatOptions, UEModelOptions, UEPoseOptions
from ..typing import UFormatContext

T = TypeVar("T", bound=UEFormatOptions)


def _set_last_import_report(settings, summary: str, report: str, *, status: str) -> None:
    settings.last_import_summary = summary
    settings.last_import_report = report
    settings.last_import_status = status


class UFImportBase(Operator, ImportHelper, Generic[T]):
    bl_context = "scene"
    files: CollectionProperty(
        type=OperatorFileListElement,
        options={"HIDDEN", "SKIP_SAVE"},
    )
    directory: StringProperty(subtype="DIR_PATH")

    options_class: type[T]

    def execute(self, context: UFormatContext) -> set[str]:
        options = self.options_class.from_settings(context.scene.uf_settings)

        directory = Path(self.directory)
        paths: list[Path] = []
        capture = UEFormatImportCapture()
        for file in self.files:
            file: OperatorFileListElement
            path = directory / file.name
            try:
                UEFormatImport(options, creation_tracker=capture).import_file(path)
            except Exception as error:
                capture.rollback()
                kind = {
                    UEModelOptions: "Model",
                    UEAnimOptions: "Animation",
                    UEPoseOptions: "Pose",
                }[self.options_class]
                _set_last_import_report(
                    context.scene.uf_settings,
                    f"{kind} import failed",
                    "\n".join((
                        "UE Format Import Report",
                        f"Kind: {kind}",
                        "Status: Failed",
                        f"File: {path}",
                        f"Error: {error}",
                    )),
                    status="FAILED",
                )
                raise
            paths.append(path)

        capture.clear()

        kind = {
            UEModelOptions: "Model",
            UEAnimOptions: "Animation",
            UEPoseOptions: "Pose",
        }[self.options_class]
        _set_last_import_report(
            context.scene.uf_settings,
            f"{kind} import: {len(paths)} file(s)",
            "\n".join((
                "UE Format Import Report",
                f"Kind: {kind}",
                "Status: Success",
                "Files:",
                *(f"- {path}" for path in paths),
            )),
            status="SUCCESS",
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
        UEFORMAT_PT_Panel.draw_general_options(self, context.scene.uf_settings)
        UEFORMAT_PT_Panel.draw_model_options(
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
        UEFORMAT_PT_Panel.draw_general_options(self, context.scene.uf_settings)
        UEFORMAT_PT_Panel.draw_anim_options(
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
        UEFORMAT_PT_Panel.draw_general_options(self, context.scene.uf_settings)
        UEFORMAT_PT_Panel.draw_pose_options(
            self,
            context.scene.uf_settings,
            import_menu=True,
        )


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
