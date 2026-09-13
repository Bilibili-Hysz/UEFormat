from collections.abc import Collection
from dataclasses import dataclass
from typing import cast

import bpy
from bpy.types import PoseBone, bpy_prop_collection
from mathutils import Vector, Quaternion
from math import *


def bytes_to_str(in_bytes: bytes) -> str:
    return in_bytes.rstrip(b"\x00").decode()

# linq 
def first(target, expr, default=None):
    if not target:
        return None
    filtered = filter(expr, target)

    return next(filtered, default)
    
def best(target, expr, goal, default=None):
    if not target:
        return None

    for item in target:
        if expr(item) == goal:
            return item

    for item in target:
        if expr(item) in goal:
            return item

    return default

def make_axis_vector(vec_in: Vector) -> Vector:
    vec_out = Vector()
    x, y, z = vec_in

    if abs(x) > abs(y):
        if abs(x) > abs(z):
            vec_out.x = 1 if x >= 0 else -1
        else:
            vec_out.z = 1 if z >= 0 else -1
    elif abs(y) > abs(z):
        vec_out.y = 1 if y >= 0 else -1
    else:
        vec_out.z = 1 if z >= 0 else -1

    return vec_out


def get_case_insensitive(source: bpy_prop_collection, string: str) -> PoseBone | None:
    string = string.lower()
    source_ = cast(Collection[PoseBone], source)

    for item in source_:
        if item.name.lower() == string:
            return item

    return None


def get_active_armature():
    obj = bpy.context.object

    if obj is None:
        return None

    if obj.type == "ARMATURE":
        return obj
    elif obj.type == "MESH":
        for modifier in obj.modifiers:  # type: ignore
            if modifier.type == "ARMATURE":
                return modifier.object

def get_armature_mesh(obj):
    if obj.type == 'ARMATURE' and len(obj.children) > 0:
        for child in obj.children:
            if child.type == "MESH":
                return child

    if obj.type == 'MESH':
        return obj
    return None

# pose utils

def disable_constraints(armature: bpy.types.Object) -> list[bpy.types.Constraint]:
    constraints_muted = []
    for bone in armature.pose.bones:
        for constraint in bone.constraints:
            if not constraint.mute:
                constraint.mute = True
                constraints_muted.append(constraint)
    return constraints_muted

def bone_hierarchy_has_vertex_groups(bone, vertex_groups):
    if bone.name in vertex_groups:
        return True
    children = bone.children_recursive
    for child in children:
        if child.name in vertex_groups:
            return True
    return False

def bone_has_parent(child, parent):
    if child == parent:
        return True
    return parent in child.parent_recursive

def bone_roll(bone: bpy.types.EditBone, roll: float):
    assert 'orig_roll' not in bone
    bone['orig_roll'] = bone.roll
    bone.roll = roll

def bone_tail(bone: bpy.types.EditBone, tail):
    assert 'orig_tail' not in bone
    bone['orig_tail'] = bone.tail
    bone.tail = tail

def bone_head(bone: bpy.types.EditBone, head):
    assert 'orig_head' not in bone
    bone['orig_head'] = bone.head
    bone.head = head

def bone_parent(child: bpy.types.EditBone, parent_to: bpy.types.EditBone):
    assert child != parent_to

    # Save original parent as a custom property
    if child.parent:
        assert 'orig_parent' not in child
        child['orig_parent'] = child.parent.name

    child.parent = parent_to

def bone_swap_properties(all_bones: bpy.types.ArmatureEditBones, bone: bpy.types.EditBone):
    if orig_roll := bone.get('orig_roll'):
        curr_roll = bone.roll
        bone.roll = orig_roll
        bone['orig_roll'] = curr_roll

    if orig_tail := bone.get('orig_tail'):
        curr_tail = bone.tail.copy()
        bone.tail = orig_tail
        bone['orig_tail'] = curr_tail

    if orig_head := bone.get('orig_head'):
        curr_head = bone.head.copy()
        bone.head = orig_head
        bone['orig_head'] = curr_head

    if orig_parent := bone.get('orig_parent'):
        curr_bone_parent_name = bone.parent.name
        bone.parent = all_bones.get(orig_parent)
        bone['orig_parent'] = curr_bone_parent_name

def bone_swap_orig_parents(armature_obj: bpy.types.Object):
    original_mode = bpy.context.active_object.mode
    original_selected_object = bpy.context.active_object
    try:
        bpy.context.view_layer.objects.active = armature_obj
        edit_bones = armature_obj.data.edit_bones
        bpy.ops.object.mode_set(mode='EDIT')
        # Iterate all edit bones and reparent back to 'orig_parent' if property exists and make note of existing parent.
        for edit_bone in edit_bones:
            bone_swap_properties(edit_bones, edit_bone)
    finally:
        bpy.ops.object.mode_set(mode=original_mode)
        bpy.context.view_layer.objects.active = original_selected_object


@dataclass(frozen=True)
class BoneSwapEditState:
    """Exact edit-bone state needed to undo ``bone_swap_orig_parents``."""

    parent_name: str | None
    use_connect: bool
    head: Vector
    tail: Vector
    roll: float
    custom_properties: tuple[tuple[str, bool, object], ...]


_BONE_SWAP_CUSTOM_PROPERTIES = ("orig_parent", "orig_head", "orig_tail", "orig_roll")


def _copy_edit_bone_value(value):
    copy = getattr(value, "copy", None)
    return copy() if callable(copy) else value


def capture_bone_swap_state(armature_obj: bpy.types.Object) -> dict[str, BoneSwapEditState]:
    """Capture the fields changed by ``bone_swap_orig_parents``.

    The caller must have the armature in Edit Mode.  Keeping this snapshot
    separate from the toggle helper makes recovery independent of how far a
    toggle progressed before Blender raised an exception.
    """

    if armature_obj.mode != "EDIT":
        raise ValueError("Bone swap state must be captured in Edit Mode")

    return {
        bone.name: BoneSwapEditState(
            parent_name=bone.parent.name if bone.parent is not None else None,
            use_connect=bone.use_connect,
            head=bone.head.copy(),
            tail=bone.tail.copy(),
            roll=bone.roll,
            custom_properties=tuple(
                (
                    key,
                    key in bone,
                    _copy_edit_bone_value(bone.get(key)),
                )
                for key in _BONE_SWAP_CUSTOM_PROPERTIES
            ),
        )
        for bone in armature_obj.data.edit_bones
    }


def restore_bone_swap_state(
    armature_obj: bpy.types.Object,
    state: dict[str, BoneSwapEditState],
) -> None:
    """Restore a pre-toggle edit-bone snapshot without a second toggle.

    Parent links are cleared before they are rebuilt so a partially completed
    toggle cannot leave a transient cycle or make restoration order-dependent.
    """

    original_active_object = bpy.context.view_layer.objects.active
    original_mode = getattr(original_active_object, "mode", "OBJECT")
    try:
        bpy.context.view_layer.objects.active = armature_obj
        if armature_obj.mode != "EDIT":
            bpy.ops.object.mode_set(mode="EDIT")
        edit_bones = armature_obj.data.edit_bones

        for bone in edit_bones:
            bone.parent = None
            bone.use_connect = False

        for name, snapshot in state.items():
            bone = edit_bones.get(name)
            if bone is None:
                continue
            bone.head = snapshot.head.copy()
            bone.tail = snapshot.tail.copy()
            bone.roll = snapshot.roll

        for name, snapshot in state.items():
            bone = edit_bones.get(name)
            if bone is None:
                continue
            bone.parent = edit_bones.get(snapshot.parent_name) if snapshot.parent_name else None
            bone.use_connect = snapshot.use_connect
            for key, existed, value in snapshot.custom_properties:
                if existed:
                    bone[key] = _copy_edit_bone_value(value)
                else:
                    bone.pop(key, None)
    finally:
        try:
            if armature_obj.mode != original_mode:
                bpy.ops.object.mode_set(mode=original_mode)
        finally:
            bpy.context.view_layer.objects.active = original_active_object

# pitch, yaw, roll
def make_quat(rot):
    return Quaternion((rot[3], rot[0], rot[1], rot[2]))

def make_vector(vec):
    return Vector((vec[0], vec[1], vec[2]))

def get_weighted_vertex_group_indices(obj):
    """Collect vertex-group indices that actually have weights"""
    weighted = set()
    for v in obj.data.vertices:
        for g in v.groups:
            if g.weight > 0.0:
                weighted.add(g.group)
    return weighted

def has_vertex_weights(obj, vertex_group, weighted_indices=None):
    if weighted_indices is not None:
        return vertex_group.index in weighted_indices

    # Fallback for callers that don't precompute indices.
    group_index = vertex_group.index
    for v in obj.data.vertices:
        for g in v.groups:
            if g.group == group_index and g.weight > 0.0:
                return True
    return False
