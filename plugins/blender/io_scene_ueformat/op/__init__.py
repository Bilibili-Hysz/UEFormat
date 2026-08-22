import bpy
from bpy.props import PointerProperty
from bpy.types import Context, Menu, Scene

from .import_helpers import (
    UFImportUEAnim,
    UFImportUEModel,
    UFImportUEPose,
    UFCopyLastImportReport,
    IO_FH_uemodel,
    IO_FH_ueanim,
    IO_FH_uepose,
)
from .panels import UEFORMAT_PT_Panel
from .settings import UFSettings

operators = [
    UEFORMAT_PT_Panel,
    UFImportUEModel,
    UFImportUEAnim,
    UFImportUEPose,
    UFCopyLastImportReport,
    UFSettings,
    IO_FH_uemodel,
    IO_FH_ueanim,
    IO_FH_uepose,
]

_PANEL_CLASSES = {UEFORMAT_PT_Panel}
_REGISTERED_CLASSES = []
_MENU_REGISTERED = False


def draw_import_menu(self: Menu, context: Context) -> None:  # noqa: ARG001
    self.layout.operator(UFImportUEModel.bl_idname, text="Unreal Model (.uemodel)")
    self.layout.operator(UFImportUEAnim.bl_idname, text="Unreal Animation (.ueanim)")
    self.layout.operator(UFImportUEPose.bl_idname, text="Unreal Pose Asset (.uepose)")


def register(*, headless: bool = False) -> None:
    global _REGISTERED_CLASSES, _MENU_REGISTERED
    if _REGISTERED_CLASSES:
        return
    classes = [operator for operator in operators if not headless or operator not in _PANEL_CLASSES]
    for operator in classes:
        bpy.utils.register_class(operator)

    Scene.uf_settings = PointerProperty(type=UFSettings)  # type: ignore[reportAttributeAccessIssue]
    if not headless:
        bpy.types.TOPBAR_MT_file_import.append(draw_import_menu)
        _MENU_REGISTERED = True
    _REGISTERED_CLASSES = classes


def register_headless() -> None:
    register(headless=True)


def unregister() -> None:
    global _REGISTERED_CLASSES, _MENU_REGISTERED
    if _MENU_REGISTERED:
        bpy.types.TOPBAR_MT_file_import.remove(draw_import_menu)
        _MENU_REGISTERED = False
    if hasattr(Scene, "uf_settings"):
        del Scene.uf_settings  # type: ignore[reportAttributeAccessIssue]
    for operator in reversed(_REGISTERED_CLASSES or operators):
        bpy.utils.unregister_class(operator)
    _REGISTERED_CLASSES = []
