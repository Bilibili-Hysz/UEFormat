"""Cross-field validation for the standard UEFormat DTO boundary.

The binary readers validate lengths and scalar encodings.  This module validates
references between fields before any Blender datablock is created, so malformed
files fail with a deterministic format error instead of an IndexError or a
partially-created scene.
"""

from __future__ import annotations

import math

import numpy as np

from .dto.anim import AnimDto
from .dto.model import ModelDto
from .dto.pose import PoseDto


def _integer(value, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise ValueError(f"Invalid V10 {field_name}: expected an integer")
    return int(value)


def _finite_vector(value, width: int, field_name: str) -> None:
    array = np.asarray(value)
    if array.shape != (width,):
        raise ValueError(
            f"Invalid V10 {field_name}: expected {width} values, got shape {array.shape}"
        )
    if not np.issubdtype(array.dtype, np.number) or not np.all(np.isfinite(array)):
        raise ValueError(f"Invalid V10 {field_name}: values must be finite numbers")


def _finite_matrix(value, width: int, field_name: str, expected_rows: int | None = None) -> np.ndarray:
    array = np.asarray(value)
    if array.size == 0:
        if expected_rows not in (None, 0):
            raise ValueError(
                f"Invalid V10 {field_name}: expected {expected_rows} rows, got 0"
            )
        return array
    if array.ndim != 2 or array.shape[1] != width:
        raise ValueError(
            f"Invalid V10 {field_name}: expected an N x {width} matrix, got shape {array.shape}"
        )
    if expected_rows is not None and array.shape[0] != expected_rows:
        raise ValueError(
            f"Invalid V10 {field_name}: expected {expected_rows} rows, got {array.shape[0]}"
        )
    if not np.issubdtype(array.dtype, np.number) or not np.all(np.isfinite(array)):
        raise ValueError(f"Invalid V10 {field_name}: values must be finite numbers")
    return array


def _integer_matrix(value, field_name: str) -> np.ndarray:
    array = np.asarray(value)
    if array.size == 0:
        return array
    if array.ndim != 2 or array.shape[1] != 3:
        raise ValueError(
            f"Invalid V10 {field_name}: expected an N x 3 index matrix, got shape {array.shape}"
        )
    if not np.issubdtype(array.dtype, np.integer):
        raise ValueError(f"Invalid V10 {field_name}: indices must be integers")
    return array


def _validate_skeleton(skeleton, *, field_name: str = "skeleton") -> None:
    names: set[str] = set()
    for index, bone in enumerate(skeleton.bones):
        name_key = bone.name.casefold()
        if name_key in names:
            raise ValueError(f"Invalid V10 {field_name}: duplicate bone name {bone.name!r}")
        names.add(name_key)

        parent_index = _integer(bone.parent_index, f"{field_name} bone parent index")
        if parent_index < -1 or parent_index >= index:
            raise ValueError(
                f"Invalid V10 {field_name} bone parent index {parent_index} at bone {index}"
            )
        _finite_vector(bone.position, 3, f"{field_name} bone position")
        _finite_vector(bone.rotation, 4, f"{field_name} bone rotation")
        _finite_vector(bone.scale, 3, f"{field_name} bone scale")

    for socket in skeleton.sockets:
        _finite_vector(socket.position, 3, f"{field_name} socket position")
        _finite_vector(socket.rotation, 4, f"{field_name} socket rotation")
        _finite_vector(socket.scale, 3, f"{field_name} socket scale")


def _validate_lod(lod, skeleton, lod_index: int) -> None:
    prefix = f"LOD {lod_index}"
    vertices = _finite_matrix(lod.vertices, 3, f"{prefix} vertices")
    vertex_count = 0 if vertices.size == 0 else vertices.shape[0]

    indices = _integer_matrix(lod.indices, f"{prefix} indices")
    polygon_count = 0 if indices.size == 0 else indices.shape[0]
    if indices.size and (np.any(indices < 0) or np.any(indices >= vertex_count)):
        raise ValueError(f"Invalid V10 {prefix} indices: vertex index is outside the vertex array")

    _finite_matrix(lod.normals, 3, f"{prefix} normals", vertex_count if lod.normals.size else None)
    for uv_index, uvs in enumerate(lod.uvs):
        _finite_matrix(
            uvs,
            2,
            f"{prefix} UV{uv_index}",
            vertex_count,
        )
    for color in lod.colors:
        _finite_matrix(
            color.data,
            4,
            f"{prefix} vertex color {color.name}",
            vertex_count,
        )

    for section_index, material in enumerate(lod.materials):
        first_index = _integer(material.first_index, f"{prefix} material {section_index} first index")
        num_faces = _integer(material.num_faces, f"{prefix} material {section_index} face count")
        if first_index < 0 or first_index % 3 or num_faces < 0:
            raise ValueError(f"Invalid V10 {prefix} material section range at {section_index}")
        end_face = first_index // 3 + num_faces
        if end_face > polygon_count:
            raise ValueError(
                f"Invalid V10 {prefix} material section {section_index}: "
                f"ends at face {end_face}, mesh contains {polygon_count}"
            )

    if lod.weights:
        if skeleton is None or not skeleton.bones:
            raise ValueError(f"Invalid V10 {prefix} weights: model has no skeleton bones")
        for weight in lod.weights:
            bone_index = _integer(weight.bone_index, f"{prefix} weight bone index")
            vertex_index = _integer(weight.vertex_index, f"{prefix} weight vertex index")
            if bone_index < 0 or bone_index >= len(skeleton.bones):
                raise ValueError(f"Invalid V10 {prefix} weight bone index {bone_index}")
            if vertex_index < 0 or vertex_index >= vertex_count:
                raise ValueError(f"Invalid V10 {prefix} weight vertex index {vertex_index}")
            if not math.isfinite(float(weight.weight)):
                raise ValueError(f"Invalid V10 {prefix} weight: value must be finite")

    for morph_index, morph in enumerate(lod.morphs):
        for delta in morph.deltas:
            vertex_index = _integer(delta.vertex_index, f"{prefix} Morph {morph_index} vertex index")
            if vertex_index < 0 or vertex_index >= vertex_count:
                raise ValueError(
                    f"Invalid V10 {prefix} Morph {morph_index} vertex index {vertex_index}"
                )
            _finite_vector(delta.position, 3, f"{prefix} Morph {morph_index} position")
            _finite_vector(delta.normals, 3, f"{prefix} Morph {morph_index} normal")


def _validate_collision(collision, collision_index: int) -> None:
    prefix = f"collision {collision_index}"
    vertices = _finite_matrix(collision.vertices, 3, f"{prefix} vertices")
    vertex_count = 0 if vertices.size == 0 else vertices.shape[0]
    indices = _integer_matrix(collision.indices, f"{prefix} indices")
    if indices.size and (np.any(indices < 0) or np.any(indices >= vertex_count)):
        raise ValueError(f"Invalid V10 {prefix} indices: vertex index is outside the vertex array")


def validate_model_dto(dto: ModelDto) -> ModelDto:
    """Validate cross-field model references and return ``dto`` unchanged."""

    skeleton = dto.skeleton
    if skeleton is not None:
        _validate_skeleton(skeleton)
    for lod_index, lod in enumerate(dto.lods):
        _validate_lod(lod, skeleton, lod_index)
    for collision_index, collision in enumerate(dto.collisions):
        _validate_collision(collision, collision_index)
    return dto


def _validate_animation_key(key, width: int, field_name: str) -> None:
    _integer(key.frame, f"{field_name} frame")
    _finite_vector(key.value, width, f"{field_name} value")


def validate_anim_dto(dto: AnimDto) -> AnimDto:
    """Validate animation vectors while allowing invalid metadata FPS to fallback."""

    if dto.metadata is not None:
        _integer(dto.metadata.num_frames, "animation frame count")
        if dto.metadata.num_frames < 0:
            raise ValueError("Invalid V10 animation frame count")
        _integer(dto.metadata.ref_frame_index, "animation reference frame index")

    for track_index, track in enumerate(dto.tracks):
        for key in track.position_keys:
            _validate_animation_key(key, 3, f"track {track_index} position key")
        for key in track.rotation_keys:
            _validate_animation_key(key, 4, f"track {track_index} rotation key")
        for key in track.scale_keys:
            _validate_animation_key(key, 3, f"track {track_index} scale key")
    for curve_index, curve in enumerate(dto.curves):
        for key in curve.keys:
            _integer(key.frame, f"curve {curve_index} key frame")
            if not math.isfinite(float(key.value)):
                raise ValueError(f"Invalid V10 curve {curve_index} key value")
    return dto


def validate_pose_dto(dto: PoseDto) -> PoseDto:
    """Validate pose transforms and curve-name references."""

    for pose_index, pose in enumerate(dto.poses):
        for key in pose.keys:
            _finite_vector(key.position, 3, f"pose {pose_index} key position")
            _finite_vector(key.rotation, 4, f"pose {pose_index} key rotation")
            _finite_vector(key.scale, 3, f"pose {pose_index} key scale")
        for curve in pose.curves:
            curve_index = _integer(curve.curve_index, f"pose {pose_index} curve index")
            if curve_index < 0 or curve_index >= len(dto.curve_names):
                raise ValueError(
                    f"Invalid V10 pose curve index {curve_index}: "
                    f"curve table contains {len(dto.curve_names)} name(s)"
                )
            if not math.isfinite(float(curve.influence)):
                raise ValueError(f"Invalid V10 pose {pose_index} curve influence")
    return dto
