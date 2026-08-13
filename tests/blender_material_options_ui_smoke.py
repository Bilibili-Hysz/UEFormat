import sys
import types
from pathlib import Path

import bpy


REPOSITORY = Path(__file__).parents[1]
PLUGIN_ROOT = REPOSITORY / "plugins" / "blender"
package = types.ModuleType("io_scene_ueformat")
package.__path__ = [str(PLUGIN_ROOT / "io_scene_ueformat")]
sys.modules["io_scene_ueformat"] = package

from io_scene_ueformat.op.panels import (  # noqa: E402
    UEFORMAT_PT_Animation,
    UEFORMAT_PT_Layout,
    UEFORMAT_PT_Model,
)
from io_scene_ueformat import op  # noqa: E402
from io_scene_ueformat.op.settings import UFSettings  # noqa: E402


class LayoutRecorder:
    def __init__(self, calls=None, enabled=True):
        self.calls = [] if calls is None else calls
        self.enabled = enabled

    def box(self):
        return LayoutRecorder(self.calls, self.enabled)

    def row(self):
        return LayoutRecorder(self.calls, self.enabled)

    def column(self):
        return LayoutRecorder(self.calls, self.enabled)

    def label(self, **kwargs):
        self.calls.append(("label", kwargs, self.enabled))

    def prop(self, _settings, name):
        self.calls.append(("prop", name, self.enabled))

    def operator(self, identifier, **kwargs):
        self.calls.append(("operator", identifier, kwargs, self.enabled))


class DrawTarget:
    def __init__(self):
        self.layout = LayoutRecorder()


def calls_for(target, kind):
    return [call[1] for call in target.layout.calls if call[0] == kind]


op.register()
try:
    panels = (UEFORMAT_PT_Model, UEFORMAT_PT_Animation)
    assert all(hasattr(bpy.types, panel.__name__) for panel in panels)
    assert not hasattr(bpy.types, "UEFORMAT_PT_Scene")
finally:
    op.unregister()

bpy.utils.register_class(UFSettings)
bpy.types.Scene.uf_settings = bpy.props.PointerProperty(type=UFSettings)
try:
    settings = bpy.context.scene.uf_settings
    assert not hasattr(settings, "uescene_import_policy")
    assert not hasattr(settings, "flatten_uescene_hierarchy")
    assert not hasattr(settings, "flatten_uescene_instances")

    context = type("Context", (), {"scene": type("Scene", (), {"uf_settings": settings})()})()
    model = DrawTarget()
    UEFORMAT_PT_Model.draw(model, context)
    assert calls_for(model, "operator") == ["uf.import_uemodel"]

    animation = DrawTarget()
    UEFORMAT_PT_Animation.draw(animation, context)
    assert calls_for(animation, "operator") == ["uf.import_ueanim", "uf.import_uepose"]

    for draw_options in (
        UEFORMAT_PT_Layout.draw_model_options,
        UEFORMAT_PT_Layout.draw_anim_options,
        UEFORMAT_PT_Layout.draw_pose_options,
    ):
        dialog = DrawTarget()
        draw_options(dialog, settings, import_menu=True)
        assert not calls_for(dialog, "operator")
finally:
    del bpy.types.Scene.uf_settings
    bpy.utils.unregister_class(UFSettings)

print("FModel material options UI smoke passed")
