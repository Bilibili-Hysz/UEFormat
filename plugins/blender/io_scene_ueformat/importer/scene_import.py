"""Blender mutation layer for validated :mod:`uescene` import plans."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
from math import radians
import re
import sys

import bpy

from .logic import UEFormatImport, read_uemodel_source_lods
from .uescene import MaterialOverride, LightFlags, LightType, RequiredModelFailure, UESceneImportPlan, UEScenePlanComponent


class UEMODELSourceMaterialAmbiguityError(ValueError):
    code = "mato_source_material_mapping_ambiguous"


class RequiredModelImportError(RuntimeError):
    def __init__(self, failures: tuple[RequiredModelFailure, ...]):
        self.failures = failures
        super().__init__(f"{len(failures)} required model(s) failed to import")


@dataclass(slots=True)
class SceneImportSummary:
    collection: bpy.types.Collection
    models_collection: bpy.types.Collection
    lights_collection: bpy.types.Collection
    imported_actors: int = 0
    imported_components: int = 0
    imported_static_meshes: int = 0
    imported_lights: int = 0
    unresolved_models: int = 0
    failed_models: int = 0
    deferred_instances: int = 0
    warnings: list[str] = field(default_factory=list)
    outcome: str = "success"
    required_model_failures: list[RequiredModelFailure] = field(default_factory=list)


def _metadata(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _id_property(value: int) -> str:
    """Serialize uint64 scene identifiers beyond Blender IDProperty integer limits."""
    return str(value)


def _scene_name(source_world_path: str | None) -> str:
    normalized_path = (source_world_path or "Import").replace("\\", "/").rstrip("/")
    source_name = normalized_path.rsplit("/", 1)[-1]
    path_parts = normalized_path.split("/")
    terminal_parts = source_name.split(".")
    if (
        normalized_path.startswith("/")
        and len(path_parts) >= 3
        and all(path_parts[1:])
        and len(terminal_parts) == 2
        and terminal_parts[0] == terminal_parts[1]
        and _valid_unreal_material_identity(normalized_path) is not None
    ):
        source_name = terminal_parts[0]
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", source_name).strip("._") or "Import"


def _valid_unreal_material_identity(path: str | None) -> str | None:
    if not path:
        return None
    if not path.startswith("/"):
        path = "/" + path
    if len(path) < 4 or path.startswith("//"):
        return None
    slash = path.rfind("/")
    if slash <= 1:
        return None
    package_segments = path[1:slash].split("/")
    if any(not segment or segment in (".", "..") or "." in segment for segment in package_segments):
        return None
    terminal = path[slash + 1:]
    dot = terminal.find(".")
    if dot >= 0:
        if dot == 0 or dot != terminal.rfind(".") or dot == len(terminal) - 1:
            return None
        if terminal[:dot] != terminal[dot + 1:]:
            return None
        path = path[:slash + 1 + dot]
    if not path[path.rfind("/") + 1:] or "." in path[path.rfind("/") + 1:]:
        return None
    return path


def _scene_source_key(plan: UESceneImportPlan) -> str:
    """Stable identity for replace-style imports of the same exported world."""
    return json.dumps({
        "source_world_path": plan.provenance.source_world_path or "",
        "persistent_level_path": plan.provenance.persistent_level_path or "",
        "source_game": plan.provenance.source_game or "",
    }, separators=(",", ":"), sort_keys=True)


def _overrides(overrides: tuple[MaterialOverride, ...]) -> list[dict[str, object]]:
    return [{
        "slot_ordinal": item.slot_ordinal,
        "flags": item.flags,
        "base_material_asset_id": _id_property(item.base_material_asset_id),
        "override_material_asset_id": _id_property(item.override_material_asset_id),
        "parameter_payload_hex": item.parameter_payload.hex(),
    } for item in overrides]


def _apply_transform(obj: bpy.types.Object, transform, scale_factor: float) -> None:
    # UEScene is Unreal left-handed Z-up. Match the model importer's Y mirror
    # so actor, component, light, and instance transforms share one basis.
    obj.location = (
        transform.translation[0] * scale_factor,
        -transform.translation[1] * scale_factor,
        transform.translation[2] * scale_factor,
    )
    obj.rotation_mode = "QUATERNION"
    obj.rotation_quaternion = (
        -transform.rotation[3],
        transform.rotation[0],
        -transform.rotation[1],
        transform.rotation[2],
    )
    obj.scale = transform.scale


def _component_properties(obj: bpy.types.Object, component: UEScenePlanComponent) -> None:
    asset = component.mesh_asset
    obj["uescene_component_id"] = _id_property(component.id)
    obj["uescene_actor_id"] = _id_property(component.actor_id)
    obj["uescene_parent_component_id"] = _id_property(component.parent_id)
    obj["uescene_component_kind"] = component.kind
    obj["uescene_source_path"] = asset.path or ""
    obj["uescene_asset_id"] = _id_property(asset.id)
    obj["uescene_asset_name"] = asset.name or ""
    obj["uescene_asset_path"] = asset.path or ""
    obj["uescene_model_path"] = str(component.model_path) if component.model_path else ""
    obj["uescene_material_overrides"] = _metadata(_overrides(component.material_overrides))
    obj["uescene_visibility_flags"] = component.visibility_flags
    visible = bool(component.visibility_flags & 1)
    obj.hide_viewport = not visible
    obj.hide_render = not visible


def _light_properties(obj: bpy.types.Object, light) -> None:
    obj["uescene_light_id"] = _id_property(light.id)
    obj["uescene_light_name"] = light.name
    obj["uescene_actor_id"] = _id_property(light.actor_id)
    obj["uescene_parent_component_id"] = _id_property(light.parent_component_id)
    obj["uescene_transform_id"] = _id_property(light.transform_id)
    obj["uescene_light_type"] = int(light.light_type)
    obj["uescene_light_color_encoding"] = int(light.color_encoding)
    obj["uescene_light_units"] = int(light.light_units)
    obj["uescene_light_color_rgba"] = _metadata(light.color_rgba)
    obj["uescene_light_flags"] = light.flags
    obj["uescene_light_intensity"] = light.intensity
    obj["uescene_light_attenuation_radius"] = light.attenuation_radius
    obj["uescene_light_falloff_exponent"] = light.falloff_exponent
    obj["uescene_light_inner_cone_angle_degrees"] = light.inner_cone_angle_degrees
    obj["uescene_light_outer_cone_angle_degrees"] = light.outer_cone_angle_degrees
    obj["uescene_light_rect_width"] = light.rect_width
    obj["uescene_light_rect_height"] = light.rect_height
    obj["uescene_light_source_angle"] = light.light_source_angle
    obj["uescene_light_source_soft_angle"] = light.light_source_soft_angle
    obj["uescene_light_source_radius"] = light.source_radius
    obj["uescene_light_soft_source_radius"] = light.soft_source_radius
    obj["uescene_light_source_length"] = light.source_length
    obj["uescene_light_barn_door_angle"] = light.barn_door_angle
    obj["uescene_light_barn_door_length"] = light.barn_door_length
    obj["uescene_class_path"] = light.class_path
    obj["uescene_source_path"] = light.source_path
    obj["uescene_light_energy_policy"] = "raw_intensity"


def _move_to_models_collection(objects: list[bpy.types.Object], models_collection) -> list[bpy.types.Object]:
    for item in objects:
        if models_collection not in item.users_collection:
            models_collection.objects.link(item)
        for source_collection in tuple(item.users_collection):
            if source_collection != models_collection:
                source_collection.objects.unlink(item)
    return objects


def _snapshot_import_data() -> dict[str, set[int]]:
    """Capture importer-owned candidates before a component-level mutation."""
    names = ("objects", "meshes", "materials", "images", "armatures", "actions", "lights")
    return {
        name: {item.as_pointer() for item in getattr(bpy.data, name, ())}
        for name in names
    }


def _rollback_import_data(before: dict[str, set[int]], preserve_shared: bool = False) -> None:
    """Remove only datablocks created by a failed model import, in dependency order."""
    for name in ("objects", "actions", "armatures", "meshes", "lights", "materials", "images"):
        collection = getattr(bpy.data, name, None)
        if collection is None:
            continue
        created = [item for item in collection if item.as_pointer() not in before.get(name, set())]
        for item in created:
            if preserve_shared and name in ("materials", "images"):
                if not item.get("fmodel_importer_owned") or getattr(item, "users", 0) != 0:
                    continue
            elif preserve_shared and name in ("actions", "armatures", "meshes", "lights") and getattr(item, "users", 0) != 0:
                continue
            try:
                collection.remove(item, do_unlink=True)
            except TypeError:
                collection.remove(item)


def _remove_collection_objects(collection) -> None:
    """Remove only objects owned by the previous materialized scene graph."""
    objects = tuple(getattr(collection, "all_objects", collection.objects))
    template_names = {
        item.get("uescene_instance_template_collection")
        for item in objects
        if item.get("uescene_instance_template_collection")
    }
    for name in template_names:
        template = next((item for item in bpy.data.collections if item.name == name), None)
        if template is not None:
            _remove_collection(template)
    for item in objects:
        try:
            bpy.data.objects.remove(item, do_unlink=True)
        except TypeError:
            bpy.data.objects.remove(item)


def _remove_collection(collection) -> None:
    _remove_collection_objects(collection)
    for child in tuple(collection.children):
        _remove_collection(child)
    try:
        bpy.data.collections.remove(collection, do_unlink=True)
    except TypeError:
        bpy.data.collections.remove(collection)


def _replace_existing_scene_collection(source_key: str):
    for collection in tuple(bpy.data.collections):
        properties = collection if hasattr(collection, "get") else {}
        if properties.get("uescene_source_key") != source_key:
            continue
        _remove_collection(collection)


def _scene_child_collection(parent, name: str):
    """Get or create one named collection owned by a UEScene collection."""
    for child in parent.children:
        if child.name == name:
            return child
    child = bpy.data.collections.new(name)
    parent.children.link(child)
    return child


def _instance_model_roots(
    created: list[bpy.types.Object], component_object, instance_set,
    instanced_geometry_collection, source_templates_collection, source_asset_name: str,
    scale_factor: float,
) -> int:
    """Represent ISM/HISM with collection instances, never copied model trees."""
    instances = instance_set.transforms if instance_set else ()
    if not instances:
        for item in created:
            try:
                bpy.data.objects.remove(item, do_unlink=True)
            except TypeError:
                bpy.data.objects.remove(item)
        return 0

    instance_count = len(instances)
    component_object.name = f"HISM {source_asset_name} [{instance_count} instances]"
    component_object["uescene_instance_representation"] = "Collection Instance"
    component_object["uescene_instance_count"] = instance_count
    template_collection = bpy.data.collections.new(f"{component_object.name}_Source")
    source_templates_collection.children.link(template_collection)
    component_object["uescene_instance_template_collection"] = template_collection.name
    component_object["uescene_source_template_collection"] = template_collection.name
    if instanced_geometry_collection not in component_object.users_collection:
        instanced_geometry_collection.objects.link(component_object)
    for source_collection in tuple(component_object.users_collection):
        if source_collection != instanced_geometry_collection:
            source_collection.objects.unlink(component_object)
    for item in created:
        if template_collection not in item.users_collection:
            template_collection.objects.link(item)
        for source_collection in tuple(item.users_collection):
            if source_collection != template_collection:
                source_collection.objects.unlink(item)

    for index, transform in enumerate(instances):
        instance = bpy.data.objects.new(f"{component_object.name}_Instance_{index}", None)
        instanced_geometry_collection.objects.link(instance)
        instance.parent = component_object
        instance.instance_type = "COLLECTION"
        instance.instance_collection = template_collection
        _apply_transform(instance, transform, scale_factor)
        instance["uescene_instance_index"] = index
    return len(instances)


def _materialize_light(light, transform, actors, components, collection, scale_factor: float) -> bpy.types.Object:
    kind = {
        LightType.DIRECTIONAL: "SUN", LightType.POINT: "POINT",
        LightType.SPOT: "SPOT", LightType.RECT: "AREA",
    }[light.light_type]
    data = bpy.data.lights.new(light.name, kind)
    data.color = tuple(channel / 255 for channel in light.color_rgba[:3])
    data.energy = light.intensity
    data.use_shadow = bool(light.flags & LightFlags.CASTS_SHADOWS)
    if kind == "SUN":
        data.angle = radians(light.light_source_angle)
    elif kind == "SPOT":
        data.use_custom_distance = bool(light.flags & LightFlags.HAS_ATTENUATION)
        data.cutoff_distance = light.attenuation_radius * scale_factor
        data.shadow_soft_size = light.source_radius * scale_factor
        data.spot_size = radians(light.outer_cone_angle_degrees)
        data.spot_blend = max(0.0, min(1.0, 1.0 - light.inner_cone_angle_degrees / light.outer_cone_angle_degrees)) if light.outer_cone_angle_degrees else 0.0
    elif kind == "POINT":
        data.use_custom_distance = bool(light.flags & LightFlags.HAS_ATTENUATION)
        data.cutoff_distance = light.attenuation_radius * scale_factor
        data.shadow_soft_size = light.source_radius * scale_factor
    else:
        data.shape = "RECTANGLE"
        data.size = light.rect_width * scale_factor
        data.size_y = light.rect_height * scale_factor
        data.normalize = False
        data.use_custom_distance = bool(light.flags & LightFlags.HAS_ATTENUATION)
        data.cutoff_distance = light.attenuation_radius * scale_factor
    obj = bpy.data.objects.new(light.name, data)
    collection.objects.link(obj)
    _apply_transform(obj, transform, scale_factor)
    obj.parent = components[light.parent_component_id] if light.parent_component_id else actors[light.actor_id]
    _light_properties(obj, light)
    return obj


def _materialize_uescene_plan(
    plan: UESceneImportPlan,
    model_options,
    material_options,
    strict_required_models: bool = False,
    staging_root=None,
    replace_existing: bool = True,
) -> SceneImportSummary:
    """Materialize a validated plan while leaving parsing and validation bpy-free."""
    if plan.bundle_root is not None and hasattr(material_options, "bundle_root"):
        material_options = replace(material_options, bundle_root=str(plan.bundle_root))
    scene_name = _scene_name(plan.provenance.source_world_path)
    if replace_existing:
        _replace_existing_scene_collection(_scene_source_key(plan))
    collection = bpy.data.collections.new(f"{scene_name}_uescene")
    if replace_existing:
        collection["uescene_source_key"] = _scene_source_key(plan)
    (staging_root if staging_root is not None else bpy.context.scene.collection).children.link(collection)
    models_collection = _scene_child_collection(collection, f"{scene_name}_Models")
    lights_collection = _scene_child_collection(collection, f"{scene_name}_Lights")
    instanced_geometry_collection = _scene_child_collection(collection, "Instanced Geometry")
    technical_collection = _scene_child_collection(collection, "UEFormat Technical")
    source_templates_collection = _scene_child_collection(technical_collection, "Source Templates")
    summary = SceneImportSummary(
        collection=collection,
        models_collection=models_collection,
        lights_collection=lights_collection,
        warnings=list(plan.warnings),
        outcome=plan.outcome,
        required_model_failures=list(plan.required_model_failures),
    )
    scale_factor = model_options.scale_factor

    actors: dict[int, bpy.types.Object] = {}
    for actor in plan.actors:
        obj = bpy.data.objects.new(actor.name or f"Actor_{actor.id}", None)
        models_collection.objects.link(obj)
        _apply_transform(obj, plan.actor_transform(actor), scale_factor)
        obj["uescene_actor_id"] = _id_property(actor.id)
        obj["uescene_parent_actor_id"] = _id_property(actor.parent_id)
        obj["uescene_source_path"] = actor.source_path or ""
        obj["uescene_class_path"] = actor.class_path or ""
        actors[actor.id] = obj
        summary.imported_actors += 1
    for actor in plan.actors:
        if actor.parent_id:
            actors[actor.id].parent = actors[actor.parent_id]

    components: dict[int, bpy.types.Object] = {}
    for component in plan.components:
        obj = bpy.data.objects.new(component.name or f"Component_{component.id}", None)
        models_collection.objects.link(obj)
        _apply_transform(obj, component.transform, scale_factor)
        _component_properties(obj, component)
        components[component.id] = obj
        summary.imported_components += 1
    for component in plan.components:
        components[component.id].parent = components[component.parent_id] if component.parent_id else actors[component.actor_id]

    for light in plan.lights:
        _materialize_light(light, plan.light_transform(light), actors, components, lights_collection, scale_factor)
        summary.imported_lights += 1

    for component in plan.components:
        obj = components[component.id]
        if component.kind in (2, 3):
            obj["uescene_instance_set_id"] = _id_property(component.instance_set_id)
            obj["uescene_instance_count"] = component.instance_count
        if component.model_path is None:
            obj["uescene_import_state"] = "missing_model"
            summary.unresolved_models += 1
            summary.warnings.append(f"component {component.id}: static mesh model is unavailable")
            continue
        before = _snapshot_import_data()
        try:
            UEFormatImport(model_options, material_options).import_file(component.model_path)
        except Exception as error:
            _rollback_import_data(before)
            obj["uescene_import_state"] = "model_import_failed"
            summary.failed_models += 1
            summary.warnings.append(f"component {component.id}: model import failed: {error}")
            failure = RequiredModelFailure(
                component.id, component.mesh_asset.id, "malformed",
                component.mesh_asset.uri, component.model_path, str(error),
            )
            summary.required_model_failures.append(failure)
            summary.outcome = "partial"
            if strict_required_models:
                raise RequiredModelImportError((failure,)) from error
            continue
        created = [item for item in bpy.data.objects if item.as_pointer() not in before["objects"]]
        if not any(lod.index == model_options.target_lod for lod in component.mesh_lods):
            for item in created:
                bpy.data.objects.remove(item, do_unlink=True)
            obj["uescene_import_state"] = "requested_lod_unavailable"
            message = f"component {component.id}: requested LOD {model_options.target_lod} is unavailable"
            summary.warnings.append(message)
            if strict_required_models:
                failure = RequiredModelFailure(
                    component.id, component.mesh_asset.id, "requested_lod_unavailable",
                    component.mesh_asset.uri, component.model_path, message,
                )
                summary.required_model_failures.append(failure)
                raise RequiredModelImportError((failure,))
            continue
        _move_to_models_collection(created, models_collection)
        if component.kind in (2, 3):
            summary.deferred_instances += _instance_model_roots(
                created, obj, component.instance_set, instanced_geometry_collection,
                source_templates_collection, component.mesh_asset.name or component.name or f"Component {component.id}",
                scale_factor,
            )
            obj["uescene_import_state"] = "instances_imported"
        else:
            created_pointers = {item.as_pointer() for item in created}
            roots = [item for item in created if item.parent is None or item.parent.as_pointer() not in created_pointers]
            for root in roots:
                root.parent = obj
            obj["uescene_import_state"] = "model_imported"
        summary.imported_static_meshes += 1
    return summary


def _validate_model_lod_manifests(plan: UESceneImportPlan) -> None:
    assets = {asset.id: asset for asset in plan.assets}
    for component in plan.components:
        if component.model_path is None:
            continue
        declared = frozenset(lod.index for lod in component.mesh_lods)
        try:
            preflight = read_uemodel_source_lods(component.model_path)
        except (OSError, ValueError) as error:
            if getattr(error, "code", None) == "mato_source_material_mapping_ambiguous":
                failure = RequiredModelFailure(
                    component.id, component.mesh_asset.id, error.code,
                    component.mesh_asset.uri, component.model_path, str(error),
                )
                raise RequiredModelImportError((failure,)) from error
            continue
        if preflight is None:
            continue
        if preflight.source_lods != declared:
            message = f"component {component.id}: declared source LODs {sorted(declared)} do not match UEMODEL LODs {sorted(preflight.source_lods)}"
            failure = RequiredModelFailure(
                component.id, component.mesh_asset.id, "lod_manifest_mismatch",
                component.mesh_asset.uri, component.model_path, message,
            )
            raise RequiredModelImportError((failure,))
        for override in component.material_overrides:
            if not override.override_material_asset_id:
                continue
            expected = assets[override.override_material_asset_id].path
            if not preflight.source_material_mapping_present:
                message = f"component {component.id}: MATO slot {override.slot_ordinal} has no source material mapping"
                failure = RequiredModelFailure(
                    component.id, component.mesh_asset.id, "mato_source_material_mapping_missing",
                    component.mesh_asset.uri, component.model_path, message,
                )
                raise RequiredModelImportError((failure,))
            if override.slot_ordinal not in preflight.material_slots:
                message = f"component {component.id}: MATO slot {override.slot_ordinal} has no source material mapping"
                failure = RequiredModelFailure(
                    component.id, component.mesh_asset.id, "mato_source_material_mapping_missing",
                    component.mesh_asset.uri, component.model_path, message,
                )
                raise RequiredModelImportError((failure,))
            actual = preflight.material_slots.get(override.slot_ordinal)
            actual_identity = _valid_unreal_material_identity(actual)
            expected_identity = _valid_unreal_material_identity(expected)
            if actual_identity is not None and actual_identity == expected_identity:
                continue
            message = f"component {component.id}: MATO slot {override.slot_ordinal} material link {actual!r} does not match {expected!r}"
            failure = RequiredModelFailure(
                component.id, component.mesh_asset.id, "mato_material_link_mismatch",
                component.mesh_asset.uri, component.model_path, message,
            )
            raise RequiredModelImportError((failure,))


def import_uescene_plan(
    plan: UESceneImportPlan,
    model_options,
    material_options,
    strict_required_models: bool = False,
) -> SceneImportSummary:
    """Materialize a validated plan, staging strict replacements until complete."""
    _validate_model_lod_manifests(plan)
    if not strict_required_models:
        return _materialize_uescene_plan(plan, model_options, material_options)

    scene_name = _scene_name(plan.provenance.source_world_path)
    before = _snapshot_import_data()
    staging_root = bpy.data.collections.new(f"{scene_name}_uescene_staging")
    try:
        summary = _materialize_uescene_plan(
            plan, model_options, material_options, strict_required_models=True,
            staging_root=staging_root, replace_existing=False,
        )
    except Exception:
        _remove_collection(staging_root)
        _rollback_import_data(before, preserve_shared=True)
        raise

    staging_root.children.unlink(summary.collection)
    _replace_existing_scene_collection(_scene_source_key(plan))
    summary.collection.name = f"{scene_name}_uescene"
    summary.collection["uescene_source_key"] = _scene_source_key(plan)
    bpy.context.scene.collection.children.link(summary.collection)
    try:
        bpy.data.collections.remove(staging_root, do_unlink=True)
    except TypeError:
        bpy.data.collections.remove(staging_root)
    return summary
