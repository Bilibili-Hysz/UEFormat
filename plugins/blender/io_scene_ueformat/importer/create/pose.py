from __future__ import annotations

import json
import bpy
from mathutils import Quaternion, Vector

from ...options import UEPoseOptions
from ..creation_tracker import UEFormatImportCapture
from ..dto.pose import PoseDto
from ..runtime_fmodel import skeleton_identity_metadata
from ..runtime_identity import FModelTargetPoseIdentity
from ..utils import (
    bone_has_parent,
    bone_hierarchy_has_vertex_groups,
    bone_swap_orig_parents,
    capture_bone_swap_state,
    disable_constraints,
    first,
    get_active_armature,
    get_armature_mesh,
    get_case_insensitive,
    make_quat,
    make_vector,
    restore_bone_swap_state,
)


def create_pose(
    dto: PoseDto,
    options: UEPoseOptions,
    name: str,
    *,
    target_identity: FModelTargetPoseIdentity | None = None,
    creation_tracker: UEFormatImportCapture | None = None,
):
    selected_armature = options.override_skeleton or get_active_armature()
    assert isinstance(selected_armature, bpy.types.Object)

    selected_mesh = get_armature_mesh(selected_armature)
    assert isinstance(selected_mesh, bpy.types.Object)

    original_active_object = bpy.context.view_layer.objects.active
    original_selected_objects = tuple(bpy.context.selected_objects)
    original_mode = getattr(original_active_object, "mode", "OBJECT")
    original_shape_key_lock = selected_mesh.show_only_shape_key
    original_shape_key_names = tuple(
        key.name for key in (selected_mesh.data.shape_keys.key_blocks if selected_mesh.data.shape_keys else ())
    )
    original_shape_key_values = {
        key.name: key.value
        for key in (selected_mesh.data.shape_keys.key_blocks if selected_mesh.data.shape_keys else ())
    }
    original_shape_key_sliders = {
        key.name: (key.slider_min, key.slider_max)
        for key in (selected_mesh.data.shape_keys.key_blocks if selected_mesh.data.shape_keys else ())
    }
    original_pose_matrices = {
        bone.name: bone.matrix_basis.copy()
        for bone in selected_armature.pose.bones
    }
    original_constraint_mutes = tuple(
        (constraint, constraint.mute)
        for bone in selected_armature.pose.bones
        for constraint in bone.constraints
    )
    missing = object()
    original_target_pose_identity = selected_mesh.get("fmodel_target_pose_identity", missing)
    original_bone_swap_state = None
    parents_touched = False
    completed = False

    try:
        if target_identity is not None:
            selected_mesh["fmodel_target_pose_identity"] = json.dumps(
                skeleton_identity_metadata(target_identity),
                separators=(",", ":"),
                sort_keys=True,
            )
        bpy.context.view_layer.objects.active = selected_armature
        bpy.ops.object.mode_set(mode="OBJECT")
        armature_modifier = first(selected_mesh.modifiers, lambda mod: mod.type == "ARMATURE")
        if armature_modifier is None:
            raise ValueError("UEPOSE import requires an armature modifier on the selected mesh")

        selected_mesh.show_only_shape_key = False
        bpy.ops.object.mode_set(mode="EDIT")
        original_bone_swap_state = capture_bone_swap_state(selected_armature)
        bpy.ops.object.mode_set(mode="OBJECT")
        parents_touched = True
        bone_swap_orig_parents(selected_armature)
        disable_constraints(selected_armature)

        if not selected_mesh.data.shape_keys:
            selected_mesh.shape_key_add(name="Basis", from_mix=False)

        for shape_key in selected_mesh.data.shape_keys.key_blocks:
            shape_key.value = 0

        root_bone = selected_armature.pose.bones.get(options.root_bone) or selected_armature.pose.bones[0]

        for pose in dto.poses:
            bpy.context.view_layer.objects.active = selected_armature
            bpy.ops.object.mode_set(mode="POSE")
            bpy.ops.pose.select_all(action="SELECT")
            bpy.ops.pose.transforms_clear()
            bpy.ops.pose.select_all(action="DESELECT")

            contributed = False
            for pose_key in pose.keys:
                pose_bone = get_case_insensitive(selected_armature.pose.bones, pose_key.bone_name)
                if not pose_bone:
                    continue
                if root_bone and not bone_has_parent(pose_bone, root_bone):
                    continue
                if not bone_hierarchy_has_vertex_groups(pose_bone, selected_mesh.vertex_groups):
                    continue

                pose_bone.matrix_basis.identity()
                edit_bone = pose_bone.bone
                post_quat = Quaternion(post_quat) if (post_quat := edit_bone.get("post_quat")) else Quaternion()

                q = post_quat.copy()
                q.rotate(make_quat(pose_key.rotation))
                quat = post_quat.copy()
                quat.rotate(q.conjugated())
                pose_bone.rotation_quaternion = quat.conjugated() @ pose_bone.rotation_quaternion

                loc = make_vector(pose_key.position)
                loc.rotate(post_quat.conjugated())
                pose_bone.location = pose_bone.location + loc
                pose_bone.scale = Vector((1, 1, 1)) + make_vector(pose_key.scale)
                pose_bone.rotation_quaternion.normalize()
                contributed = True

            if not contributed:
                continue

            bpy.ops.object.mode_set(mode="OBJECT")
            bpy.context.view_layer.objects.active = selected_mesh
            selected_mesh.select_set(True)
            bpy.ops.object.modifier_apply_as_shapekey(keep_modifier=True, modifier=armature_modifier.name)
            selected_mesh.data.shape_keys.key_blocks[-1].name = pose.name
            selected_mesh.data.shape_keys.key_blocks[-1].value = 0

        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.context.view_layer.objects.active = selected_mesh
        selected_mesh.select_set(True)

        key_blocks = selected_mesh.data.shape_keys.key_blocks
        for pose in dto.poses:
            if len(pose.curves) == 0:
                continue

            pose_name = pose.name
            if pose_name in key_blocks:
                pose_name = f"curve_{pose_name}"

            contributed = False
            for curve in pose.curves:
                if curve.curve_index < 0 or curve.curve_index >= len(dto.curve_names):
                    raise ValueError(
                        f"UEPOSE curve index {curve.curve_index} is outside the curve table"
                    )
                target_curve_name = dto.curve_names[curve.curve_index]
                curve_shape_key = key_blocks.get(target_curve_name)
                if not curve_shape_key:
                    continue

                curve_value = curve.influence
                if curve_value < curve_shape_key.slider_min:
                    curve_shape_key.slider_min = curve_value - 1.0
                if curve_value > curve_shape_key.slider_max:
                    curve_shape_key.slider_max = curve_value + 1.0
                curve_shape_key.value = curve_value
                contributed = True

            if contributed:
                selected_mesh.shape_key_add(name=pose_name, from_mix=True)

            for key in key_blocks:
                key.value = 0

        completed = True
    finally:
        # Cleanup must never replace the original import exception. Every
        # operation below is therefore independently guarded.
        try:
            if bpy.context.active_object is not None and bpy.context.active_object.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
        except (ReferenceError, RuntimeError):
            pass

        if parents_touched:
            try:
                bpy.context.view_layer.objects.active = selected_armature
                bpy.ops.object.mode_set(mode="OBJECT")
                bpy.ops.object.mode_set(mode="POSE")
                bpy.ops.pose.select_all(action="SELECT")
                bpy.ops.pose.transforms_clear()
                bpy.ops.pose.select_all(action="DESELECT")
                bpy.ops.object.mode_set(mode="OBJECT")
                if original_bone_swap_state is not None:
                    restore_bone_swap_state(selected_armature, original_bone_swap_state)
            except (ReferenceError, RuntimeError, ValueError):
                pass

        for constraint, original_mute in original_constraint_mutes:
            try:
                constraint.mute = original_mute
            except (ReferenceError, RuntimeError):
                pass

        try:
            if not completed and selected_mesh.data.shape_keys:
                for key_block in reversed(tuple(selected_mesh.data.shape_keys.key_blocks)):
                    if key_block.name not in original_shape_key_names:
                        selected_mesh.shape_key_remove(key_block)
            if selected_mesh.data.shape_keys:
                for key_block in selected_mesh.data.shape_keys.key_blocks:
                    if key_block.name in original_shape_key_values:
                        key_block.value = original_shape_key_values[key_block.name]
                    if key_block.name in original_shape_key_sliders:
                        key_block.slider_min, key_block.slider_max = original_shape_key_sliders[key_block.name]
            selected_mesh.show_only_shape_key = original_shape_key_lock
            if not completed:
                if original_target_pose_identity is missing:
                    selected_mesh.pop("fmodel_target_pose_identity", None)
                else:
                    selected_mesh["fmodel_target_pose_identity"] = original_target_pose_identity
        except (ReferenceError, RuntimeError, ValueError):
            pass

        if not completed:
            try:
                for bone_name, matrix in original_pose_matrices.items():
                    pose_bone = selected_armature.pose.bones.get(bone_name)
                    if pose_bone is not None:
                        pose_bone.matrix_basis = matrix
            except (ReferenceError, RuntimeError, ValueError):
                pass

        try:
            bpy.ops.object.select_all(action="DESELECT")
            for obj in original_selected_objects:
                if obj.name in bpy.data.objects:
                    obj.select_set(True)
            if original_active_object is not None and original_active_object.name in bpy.data.objects:
                bpy.context.view_layer.objects.active = original_active_object
                if original_mode != "OBJECT":
                    bpy.ops.object.mode_set(mode=original_mode)
            else:
                bpy.context.view_layer.objects.active = selected_mesh
        except (ReferenceError, RuntimeError, ValueError):
            pass
