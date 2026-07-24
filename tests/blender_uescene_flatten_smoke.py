import os
import sys
from pathlib import Path

import bpy
from mathutils import Vector


PLUGIN_ROOT = Path(os.environ.get(
    "UESCENE_ADDON_ROOT", Path(__file__).resolve().parents[1] / "plugins" / "blender"
))
sys.path.insert(0, str(PLUGIN_ROOT))

import io_scene_ueformat  # noqa: E402


def link(collection, obj):
    collection.objects.link(obj)
    return obj


io_scene_ueformat.register()
try:
    collection = bpy.data.collections.new("FlattenTest_uescene")
    bpy.context.scene.collection.children.link(collection)
    models = bpy.data.collections.new("FlattenTest_Models")
    collection.children.link(models)
    instanced_geometry = bpy.data.collections.new("Instanced Geometry")
    collection.children.link(instanced_geometry)
    technical = bpy.data.collections.new("UEFormat Technical")
    collection.children.link(technical)
    source_templates = bpy.data.collections.new("Source Templates")
    technical.children.link(source_templates)
    bpy.context.view_layer.active_layer_collection = next(
        child for child in bpy.context.view_layer.layer_collection.children if child.collection == collection
    )

    actor = link(models, bpy.data.objects.new("Actor", None))
    actor["uescene_actor_id"] = "1"
    actor.location = (2, 0, 0)

    component = link(models, bpy.data.objects.new("Component", None))
    component["uescene_component_id"] = "2"
    component.parent = actor
    component.location = (3, 0, 0)

    mesh = bpy.data.meshes.new("Mesh")
    child = link(models, bpy.data.objects.new("MeshChild", mesh))
    child.parent = component
    child.location = (4, 0, 0)
    bpy.context.view_layer.update()
    expected = child.matrix_world.copy()

    constrained = link(models, bpy.data.objects.new("Constrained", None))
    constrained["uescene_actor_id"] = "3"
    constrained.constraints.new("COPY_LOCATION")

    user_property = link(models, bpy.data.objects.new("UserProperty", None))
    user_property["uescene_actor_id"] = "4"
    user_property["custom_note"] = "keep"

    template = bpy.data.collections.new("Chair Source Template")
    source_templates.children.link(template)
    template_mesh = bpy.data.meshes.new("Chair Source Mesh")
    template_object = link(template, bpy.data.objects.new("Chair Source Object", template_mesh))
    template_object.location = (1, 0, 0)

    controller = link(instanced_geometry, bpy.data.objects.new("HISM Chair [2 instances]", None))
    controller["uescene_component_id"] = "5"
    controller["uescene_instance_representation"] = "Collection Instance"
    controller["uescene_instance_count"] = 2
    controller["uescene_instance_template_collection"] = template.name
    controller["uescene_source_template_collection"] = template.name
    instance = link(instanced_geometry, bpy.data.objects.new("Chair Instance 0", None))
    instance.parent = controller
    instance.instance_type = "COLLECTION"
    instance.instance_collection = template
    instance.location = (10, 0, 0)
    bpy.context.view_layer.update()

    assert collection.children["Instanced Geometry"] == instanced_geometry
    assert collection.children["UEFormat Technical"].children["Source Templates"] == source_templates
    assert len([child for child in collection.children if child.name == "Instanced Geometry"]) == 1
    assert len([child for child in collection.children if child.name == "UEFormat Technical"]) == 1
    assert len([child for child in technical.children if child.name == "Source Templates"]) == 1
    assert controller.name == "HISM Chair [2 instances]"
    assert controller["uescene_instance_representation"] == "Collection Instance"
    assert controller["uescene_instance_template_collection"] == template.name
    assert controller["uescene_source_template_collection"] == template.name
    assert template_object.data == template_mesh
    assert template_object.name in template.objects
    assert source_templates.children[template.name] == template
    controller_name = controller.name
    instance_name = instance.name
    template_name = template.name

    assert bpy.ops.uf.flatten_uescene_hierarchy() == {"FINISHED"}
    bpy.context.view_layer.update()
    assert "Actor" not in bpy.data.objects
    assert "Component" not in bpy.data.objects
    assert child.parent is None
    assert (child.matrix_world.translation - expected.translation).length < 0.0001
    assert "Constrained" in bpy.data.objects
    assert "UserProperty" in bpy.data.objects
    assert controller_name not in bpy.data.objects
    assert instance_name not in bpy.data.objects
    assert template_name not in bpy.data.collections
    assert "Instanced Geometry" not in collection.children
    assert "UEFormat Technical" not in collection.children
    materialized = [obj for obj in collection.all_objects if obj.data == template_mesh]
    assert len(materialized) == 1
    assert (materialized[0].matrix_world.translation - Vector((11, 0, 0))).length < 0.0001
    assert bpy.ops.uf.flatten_uescene_hierarchy() == {"FINISHED"}
finally:
    io_scene_ueformat.unregister()

print("UEScene hierarchy flatten smoke passed")
