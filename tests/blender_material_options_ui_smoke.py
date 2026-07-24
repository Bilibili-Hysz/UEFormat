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
    UEFORMAT_PT_Scene,
)
from io_scene_ueformat import op  # noqa: E402
from io_scene_ueformat.op.import_helpers import (  # noqa: E402
    UFCopyLastImportReport,
    UFImportUEScene,
)
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

    def prop(self, settings, name):
        self.calls.append(("prop", name, self.enabled))

    def operator(self, identifier, **kwargs):
        self.calls.append(("operator", identifier, kwargs, self.enabled))


class DrawTarget:
    def __init__(self):
        self.layout = LayoutRecorder()


def calls_for(target, kind):
    return [call[1] for call in target.layout.calls if call[0] == kind]


def label_texts(target):
    return [kwargs["text"] for kwargs in calls_for(target, "label")]


def operator_texts(target):
    return [call[2]["text"] for call in target.layout.calls if call[0] == "operator"]


DrawTarget.draw_general_options = staticmethod(UEFORMAT_PT_Layout.draw_general_options)
DrawTarget.draw_scene_options = staticmethod(UEFORMAT_PT_Layout.draw_scene_options)
DrawTarget.draw_model_options = staticmethod(UEFORMAT_PT_Layout.draw_model_options)
DrawTarget.draw_anim_options = staticmethod(UEFORMAT_PT_Layout.draw_anim_options)
DrawTarget.draw_pose_options = staticmethod(UEFORMAT_PT_Layout.draw_pose_options)
DrawTarget.draw = UFImportUEScene.draw

op.register()
try:
    assert all(hasattr(bpy.types, panel.__name__) for panel in (UEFORMAT_PT_Scene, UEFORMAT_PT_Model, UEFORMAT_PT_Animation))
    assert not hasattr(bpy.types, "UEFORMAT_PT_Panel")
finally:
    op.unregister()

bpy.utils.register_class(UFSettings)
bpy.types.Scene.uf_settings = bpy.props.PointerProperty(type=UFSettings)
try:
    settings = bpy.context.scene.uf_settings
    expected_defaults = {
        "import_fmodel_materials": True,
        "build_fmodel_material_nodes": True,
        "load_fmodel_embedded_textures": True,
        "load_fmodel_external_textures": True,
        "keep_fmodel_unmapped_parameters": True,
        "fmodel_ue_export_root": "",
        "fmodel_bundle_root": "",
    }
    assert {name: getattr(settings, name) for name in expected_defaults} == expected_defaults

    context = type("Context", (), {"scene": type("Scene", (), {"uf_settings": settings})()})()
    panels = (UEFORMAT_PT_Scene, UEFORMAT_PT_Model, UEFORMAT_PT_Animation)
    assert [panel.bl_label for panel in panels] == [
        "UE Format: Scene",
        "UE Format: Model",
        "UE Format: Animation",
    ]
    assert [panel.bl_order for panel in panels] == [0, 1, 2]
    assert all(panel.bl_space_type == "VIEW_3D" for panel in panels)
    assert all(panel.bl_region_type == "UI" for panel in panels)
    assert all(panel.bl_category == "UE Format" for panel in panels)
    assert not hasattr(op, "UEFORMAT_PT_Panel")
    assert [panel for panel in op.operators if issubclass(panel, bpy.types.Panel)] == list(panels)

    scene_sidebar = DrawTarget()
    UEFORMAT_PT_Scene.draw(scene_sidebar, context)
    assert label_texts(scene_sidebar) == [
        "Scene Import",
        "Uses model and material settings below.",
        "Import Report",
        "No imports in this scene.",
        "General",
    ]
    assert calls_for(scene_sidebar, "operator") == [
        "uf.import_uescene", "uf.copy_last_import_report",
    ]
    assert operator_texts(scene_sidebar) == [
        "Import UE Scene", "Copy Last Import Report",
    ]
    assert not next(call[3] for call in scene_sidebar.layout.calls if call[0] == "operator" and call[1] == "uf.copy_last_import_report")
    assert calls_for(scene_sidebar, "prop") == [
        "flatten_uescene_hierarchy",
        "flatten_uescene_instances",
        "strict_uescene_models",
        "scale_factor",
    ]

    settings.last_import_summary = "Scene import: 1 actor(s)"
    settings.last_import_report = "UE Format Import Report\nStatus: Success"
    scene_sidebar = DrawTarget()
    UEFORMAT_PT_Scene.draw(scene_sidebar, context)
    assert "Scene import: 1 actor(s)" in label_texts(scene_sidebar)
    assert next(call[3] for call in scene_sidebar.layout.calls if call[0] == "operator" and call[1] == "uf.copy_last_import_report")
    bpy.utils.register_class(UFCopyLastImportReport)
    try:
        operator_settings = bpy.context.scene.uf_settings
        operator_settings.last_import_report = "UE Format Import Report\nStatus: Success"
        assert UFCopyLastImportReport.poll(bpy.context)
        assert bpy.ops.uf.copy_last_import_report() == {"FINISHED"}
    finally:
        bpy.utils.unregister_class(UFCopyLastImportReport)

    model_sidebar = DrawTarget()
    UEFORMAT_PT_Model.draw(model_sidebar, context)
    assert label_texts(model_sidebar) == ["Model Import", "Geometry", "Skeleton", "Advanced", "Materials"]
    assert calls_for(model_sidebar, "operator") == ["uf.import_uemodel"]
    assert operator_texts(model_sidebar) == ["Import UE Model"]

    animation_sidebar = DrawTarget()
    UEFORMAT_PT_Animation.draw(animation_sidebar, context)
    assert label_texts(animation_sidebar) == ["Animation Import", "Pose Import"]
    assert calls_for(animation_sidebar, "operator") == ["uf.import_ueanim", "uf.import_uepose"]
    assert operator_texts(animation_sidebar) == ["Import UE Animation", "Import UE Pose"]
    assert calls_for(scene_sidebar, "prop") + calls_for(model_sidebar, "prop") + calls_for(animation_sidebar, "prop") == [
        "flatten_uescene_hierarchy",
        "flatten_uescene_instances",
        "strict_uescene_models",
        "scale_factor",
        "target_lod",
        "import_collision",
        "import_morph_targets",
        "import_sockets",
        "reorient_bones",
        "bone_length",
        "import_virtual_bones",
        "import_fmodel_materials",
        "build_fmodel_material_nodes",
        "load_fmodel_embedded_textures",
        "keep_fmodel_unmapped_parameters",
        "load_fmodel_external_textures",
        "fmodel_ue_export_root",
        "fmodel_bundle_root",
        "rotation_only",
        "import_curves",
        "strict_fmodel_skeleton",
    ]

    for import_menu in (False, True):
        target = DrawTarget()
        UEFORMAT_PT_Layout.draw_model_options(target, settings, import_menu=import_menu)
        property_calls = [call for call in target.layout.calls if call[0] == "prop"]
        property_names = [call[1] for call in property_calls]
        assert all(name in property_names for name in expected_defaults)
        assert all(enabled for _, name, enabled in property_calls if name in expected_defaults)

    for draw_options in (
        UEFORMAT_PT_Layout.draw_model_options,
        UEFORMAT_PT_Layout.draw_anim_options,
        UEFORMAT_PT_Layout.draw_pose_options,
    ):
        dialog = DrawTarget()
        draw_options(dialog, settings, import_menu=True)
        assert not calls_for(dialog, "operator")
        assert "uf.import_uescene" not in calls_for(dialog, "operator")

    scene_dialog = DrawTarget()
    scene_dialog.draw(type("Context", (), {"scene": type("Scene", (), {"uf_settings": settings})()})())
    assert label_texts(scene_dialog) == [
        "Scene Import",
        "General",
        "Model Import",
        "Geometry",
        "Skeleton",
        "Advanced",
        "Materials",
    ]
    assert calls_for(scene_dialog, "prop") == [
        "flatten_uescene_hierarchy",
        "flatten_uescene_instances",
        "strict_uescene_models",
        "scale_factor",
        "target_lod",
        "import_collision",
        "import_morph_targets",
        "import_sockets",
        "reorient_bones",
        "bone_length",
        "import_virtual_bones",
        "import_fmodel_materials",
        "build_fmodel_material_nodes",
        "load_fmodel_embedded_textures",
        "keep_fmodel_unmapped_parameters",
        "load_fmodel_external_textures",
        "fmodel_ue_export_root",
        "fmodel_bundle_root",
    ]
    assert not calls_for(scene_dialog, "operator")
    assert all(enabled for _kind, _name, enabled in scene_dialog.layout.calls if _kind == "prop")

    settings.import_fmodel_materials = False
    scene_dialog = DrawTarget()
    scene_dialog.draw(type("Context", (), {"scene": type("Scene", (), {"uf_settings": settings})()})())
    assert all(
        not enabled
        for _kind, name, enabled in (call for call in scene_dialog.layout.calls if call[0] == "prop")
        if name in set(expected_defaults) - {"import_fmodel_materials"}
    )
    settings.import_fmodel_materials = True

    settings.load_fmodel_external_textures = False
    scene_dialog = DrawTarget()
    scene_dialog.draw(type("Context", (), {"scene": type("Scene", (), {"uf_settings": settings})()})())
    assert all(
        not enabled
        for _kind, name, enabled in (call for call in scene_dialog.layout.calls if call[0] == "prop")
        if name in {"fmodel_ue_export_root", "fmodel_bundle_root"}
    )
    assert all(
        enabled
        for _kind, name, enabled in (call for call in scene_dialog.layout.calls if call[0] == "prop")
        if name not in {"fmodel_ue_export_root", "fmodel_bundle_root"}
    )
    settings.load_fmodel_external_textures = True

    settings.load_fmodel_external_textures = False
    target = DrawTarget()
    UEFORMAT_PT_Layout.draw_model_options(target, settings)
    assert all(
        not enabled
        for _kind, name, enabled in (call for call in target.layout.calls if call[0] == "prop")
        if name in {"fmodel_ue_export_root", "fmodel_bundle_root"}
    )

    settings.load_fmodel_external_textures = True
    settings.import_fmodel_materials = False
    target = DrawTarget()
    UEFORMAT_PT_Layout.draw_model_options(target, settings)
    assert any(
        name == "import_fmodel_materials" and enabled
        for _kind, name, enabled in (call for call in target.layout.calls if call[0] == "prop")
    )
    assert all(
        not enabled
        for _kind, name, enabled in (call for call in target.layout.calls if call[0] == "prop")
        if name in set(expected_defaults) - {"import_fmodel_materials"}
    )
finally:
    del bpy.types.Scene.uf_settings
    bpy.utils.unregister_class(UFSettings)

print("FModel material options UI smoke passed")
