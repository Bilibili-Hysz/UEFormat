from __future__ import annotations

import json
import bpy
from bpy.types import Action, FCurve, PoseBone
from bpy_extras import anim_utils
from mathutils import Quaternion, Vector

from ...options import UEAnimOptions
from ..dto.anim import AnimDto, QuatKeyDto, VectorKeyDto
from ..animation_time import (
    animation_time_provenance,
    make_source_tick_converter,
    resolve_animation_source_rate,
)
from ..creation_tracker import UEFormatImportCapture
from ..runtime_fmodel import FModelAnimationBindingPlan, skeleton_identity_metadata
from ..runtime_identity import FModelTargetSkeletonIdentity
from ..utils import best, get_active_armature, get_armature_mesh, get_case_insensitive


def _key_vector(key: VectorKeyDto) -> Vector:
    return Vector(key.value)


def _key_quat(key: QuatKeyDto) -> Quaternion:
    return Quaternion((key.value[3], key.value[0], key.value[1], key.value[2]))


def create_anim(
    dto: AnimDto,
    options: UEAnimOptions,
    name: str,
    *,
    binding_plan: FModelAnimationBindingPlan | None = None,
    target_identity: FModelTargetSkeletonIdentity | None = None,
    creation_tracker: UEFormatImportCapture | None = None,
) -> Action:
    action = bpy.data.actions.new(name=name)
    if creation_tracker is not None:
        creation_tracker.track("actions", action)
    armature = options.override_skeleton or get_active_armature()
    assert isinstance(armature, bpy.types.Object)

    original_armature_animation_data = armature.animation_data
    original_armature_action = (
        original_armature_animation_data.action
        if original_armature_animation_data is not None
        else None
    )
    missing = object()
    original_armature_action_slot = (
        getattr(original_armature_animation_data, "action_slot", missing)
        if original_armature_animation_data is not None
        else missing
    )
    original_pose_matrices = {
        bone.name: bone.matrix_basis.copy()
        for bone in armature.pose.bones
    }

    shape_keys = None
    original_shape_keys_name = None
    original_shape_key_values = ()
    original_shape_key_animation_data = None
    original_shape_key_action = None
    original_shape_key_action_slot = missing
    completed = False

    def restore_animation_data(owner, original_data, original_action, original_slot) -> None:
        if original_data is None:
            if owner.animation_data is not None:
                owner.animation_data_clear()
            return
        data = owner.animation_data
        if data is None:
            data = owner.animation_data_create()
        data.action = original_action
        if original_slot is not missing and hasattr(data, "action_slot"):
            data.action_slot = original_slot

    def should_restore_animation_data() -> bool:
        if not completed:
            return True
        return not options.link

    try:
        if target_identity is not None:
            action["fmodel_target_skeleton_identity"] = json.dumps(
                skeleton_identity_metadata(target_identity),
                separators=(",", ":"),
                sort_keys=True,
            )
        if binding_plan is not None:
            action["fmodel_animation_binding"] = json.dumps(
                {
                    "bound_track_count": binding_plan.bound_track_count,
                    "skipped_track_count": binding_plan.skipped_track_count,
                    "total_track_count": binding_plan.total_track_count,
                    "strict": options.strict_fmodel_skeleton,
                },
                separators=(",", ":"),
                sort_keys=True,
            )

        render = bpy.context.scene.render
        effective_blender_fps = render.fps / render.fps_base
        source_rate = resolve_animation_source_rate(
            dto.metadata.frames_per_second if dto.metadata is not None else None,
            effective_blender_fps,
        )
        convert_source_tick = make_source_tick_converter(
            source_rate,
            render.fps,
            render.fps_base,
        )
        time_provenance = animation_time_provenance(
            source_rate,
            render.fps,
            render.fps_base,
        )
        action["fmodel_animation_time"] = json.dumps(
            time_provenance,
            separators=(",", ":"),
            sort_keys=True,
        )

        # The official importer temporarily detaches the current action while
        # it writes pose channels.  A non-linked import must put that action
        # back when it succeeds; otherwise creating an unlinked Action would
        # silently delete the user's existing animation.
        if original_armature_animation_data is not None:
            original_armature_animation_data.action = None

        if options.link:
            armature.animation_data_create()
            armature.animation_data.action = action

        if options.link and bpy.app.version >= (4, 4, 0):
            slot = action.slots.new(id_type="OBJECT", name=f"Slot_{armature.name}")
            armature.animation_data.action_slot = slot

        pose_bones = armature.pose.bones
        for track_index, track in enumerate(dto.tracks):
            if binding_plan is None:
                bone = get_case_insensitive(pose_bones, track.name)
            else:
                # A None binding is an intentional skip produced by the validated
                # plan. Never fall back to a second, potentially ambiguous lookup.
                bound_name = binding_plan.bound_bone_names[track_index]
                bone = pose_bones.get(bound_name) if bound_name is not None else None
            if bone is None:
                continue

            def create_fcurves(data_path: str, count: int, key_count: int, bone: PoseBone) -> list[FCurve]:
                path = bone.path_from_id(data_path)
                curves: list[FCurve] = []
                for i in range(count):
                    if bpy.app.version < (5, 0, 0):
                        curve = action.fcurves.new(path, index=i)
                    else:
                        slot = (
                            action.slots[0]
                            if len(action.slots) > 0
                            else action.slots.new(id_type="OBJECT", name=f"Slot_{armature.name}")
                        )
                        channelbag = anim_utils.action_ensure_channelbag_for_slot(action, slot)
                        curve = channelbag.fcurves.new(path, index=i)
                    curve.keyframe_points.add(key_count)
                    curves.append(curve)
                return curves

            def add_key(curves: list[FCurve], vector: Vector | Quaternion, key_index: int, frame: float) -> None:
                for i in range(len(vector)):
                    curves[i].keyframe_points[key_index].co = frame, vector[i]
                    curves[i].keyframe_points[key_index].interpolation = "LINEAR"

            orig_loc = Vector(orig_loc) if (orig_loc := bone.bone.get("orig_loc")) else Vector()
            orig_quat = Quaternion(orig_quat) if (orig_quat := bone.bone.get("orig_quat")) else Quaternion()
            post_quat = Quaternion(post_quat) if (post_quat := bone.bone.get("post_quat")) else Quaternion()

            if not options.rotation_only:
                loc_curves = create_fcurves("location", 3, len(track.position_keys), bone)
                scale_curves = create_fcurves("scale", 3, len(track.scale_keys), bone)
                for index, key in enumerate(track.position_keys):
                    pos = _key_vector(key)
                    pos -= orig_loc
                    pos.rotate(post_quat.conjugated())
                    add_key(loc_curves, pos, index, convert_source_tick(key.frame))
                for index, key in enumerate(track.scale_keys):
                    add_key(scale_curves, Vector(key.value), index, convert_source_tick(key.frame))

            rot_curves = create_fcurves("rotation_quaternion", 4, len(track.rotation_keys), bone)
            for index, key in enumerate(track.rotation_keys):
                p_quat = _key_quat(key).conjugated()
                q = post_quat.copy()
                q.rotate(orig_quat)
                quat = q
                q = post_quat.copy()
                q.rotate(p_quat)
                quat.rotate(q.conjugated())
                add_key(rot_curves, quat, index, convert_source_tick(key.frame))

            bone.matrix_basis.identity()

        if options.import_curves:
            mesh = get_armature_mesh(armature)
            if mesh and (shape_keys := mesh.data.shape_keys):
                original_shape_keys_name = shape_keys.name
                original_shape_key_values = tuple(
                    (key_block, key_block.value)
                    for key_block in shape_keys.key_blocks
                )
                original_shape_key_animation_data = shape_keys.animation_data
                if original_shape_key_animation_data is not None:
                    original_shape_key_action = original_shape_key_animation_data.action
                    original_shape_key_action_slot = getattr(
                        original_shape_key_animation_data,
                        "action_slot",
                        missing,
                    )
                    original_shape_key_animation_data.action = None
                shape_keys.name = "Pose Asset"

                shape_keys_action = bpy.data.actions.new(name=f"{name}_Curves")
                if creation_tracker is not None:
                    creation_tracker.track("actions", shape_keys_action)
                if options.link:
                    shape_keys.animation_data_create()
                    shape_keys.animation_data.action = shape_keys_action

                key_blocks = shape_keys.key_blocks
                for key_block in key_blocks:
                    key_block.value = 0

                for curve in dto.curves:
                    shape_key = best(key_blocks, lambda block: block.name.lower(), curve.name.lower())
                    if not shape_key:
                        continue
                    for key in curve.keys:
                        shape_key.value = key.value
                        shape_key.keyframe_insert(data_path="value", frame=convert_source_tick(key.frame))

        completed = True
        return action
    finally:
        if should_restore_animation_data():
            try:
                restore_animation_data(
                    armature,
                    original_armature_animation_data,
                    original_armature_action,
                    original_armature_action_slot,
                )
            except (AttributeError, ReferenceError, RuntimeError, ValueError):
                pass
            if shape_keys is not None and should_restore_animation_data():
                try:
                    if original_shape_keys_name is not None:
                        shape_keys.name = original_shape_keys_name
                    restore_animation_data(
                        shape_keys,
                        original_shape_key_animation_data,
                        original_shape_key_action,
                        original_shape_key_action_slot,
                    )
                    for key_block, value in original_shape_key_values:
                        key_block.value = value
                except (AttributeError, ReferenceError, RuntimeError, ValueError):
                    pass
            try:
                for bone_name, matrix in original_pose_matrices.items():
                    bone = armature.pose.bones.get(bone_name)
                    if bone is not None:
                        bone.matrix_basis = matrix
            except (AttributeError, ReferenceError, RuntimeError, ValueError):
                pass
