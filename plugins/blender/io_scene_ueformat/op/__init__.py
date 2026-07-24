import bpy
from bpy.props import PointerProperty
from bpy.types import Context, Menu, Scene

from .import_helpers import (
    IO_FH_ueanim,
    IO_FH_uemodel,
    IO_FH_uepose,
    IO_FH_uescene,
    UFImportUEAnim,
    UFCopyLastImportReport,
    UFFlattenUESceneHierarchy,
    UFImportUEModel,
    UFImportUEPose,
    UFImportUEScene,
)
from .panels import UEFORMAT_PT_Animation, UEFORMAT_PT_Model, UEFORMAT_PT_Scene
from .settings import UFSettings

operators = [
    UEFORMAT_PT_Scene,
    UEFORMAT_PT_Model,
    UEFORMAT_PT_Animation,
    UFImportUEModel,
    UFImportUEAnim,
    UFImportUEPose,
    UFImportUEScene,
    UFCopyLastImportReport,
    UFFlattenUESceneHierarchy,
    UFSettings,
    IO_FH_uemodel,
    IO_FH_ueanim,
    IO_FH_uepose,
    IO_FH_uescene,
]


def draw_import_menu(self: Menu, context: Context) -> None:  # noqa: ARG001
    self.layout.operator(UFImportUEModel.bl_idname, text="Unreal Model (.uemodel)")
    self.layout.operator(UFImportUEAnim.bl_idname, text="Unreal Animation (.ueanim)")
    self.layout.operator(UFImportUEPose.bl_idname, text="Unreal Pose Asset (.uepose)")
    self.layout.operator(UFImportUEScene.bl_idname, text="Unreal Scene (.uescene)")


def register() -> None:
    for operator in operators:
        bpy.utils.register_class(operator)

    Scene.uf_settings = PointerProperty(type=UFSettings)  # type: ignore[reportAttributeAccessIssue]
    bpy.types.TOPBAR_MT_file_import.append(draw_import_menu)


def unregister() -> None:
    bpy.types.TOPBAR_MT_file_import.remove(draw_import_menu)
    del Scene.uf_settings  # type: ignore[reportAttributeAccessIssue]

    for operator in reversed(operators):
        bpy.utils.unregister_class(operator)
