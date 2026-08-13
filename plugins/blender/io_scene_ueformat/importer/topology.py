from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from .identity import compute_skeleton_layout_hash

if TYPE_CHECKING:
    from io_scene_ueformat.importer.classes import FModelSkeletonIdentity, UEModelLOD, UEModelSkeleton


@dataclass(frozen=True, slots=True)
class UEMODELLodTopologySummary:
    lod_index: int
    vertex_count: int
    index_count: int
    material_slots: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class UEMODELSkeletonTopologySummary:
    bone_count: int
    bone_layout_hash: bytes


@dataclass(frozen=True, slots=True)
class UEMODELTopologySummary:
    lods: tuple[UEMODELLodTopologySummary, ...]
    skeleton: UEMODELSkeletonTopologySummary | None


def _validate_uemodel_lods(lods: list[UEModelLOD]) -> list[UEMODELLodTopologySummary]:
    summaries: list[UEMODELLodTopologySummary] = []
    seen_lods: set[int] = set()
    expected_slots: tuple[int, ...] | None = None
    expected_index = 0
    for lod in lods:
        if not lod.name.startswith("LOD") or not lod.name[3:].isdigit():
            raise ValueError(f"Invalid UEMODEL source LOD identity {lod.name!r}")
        lod_index = int(lod.name[3:])
        if lod_index in seen_lods:
            raise ValueError("LOD indices must be unique")
        if lod_index != expected_index:
            raise ValueError("LOD order must be deterministic and contiguous")
        seen_lods.add(lod_index)
        expected_index += 1
        required = {"VERTICES", "INDICES", "MATERIALS"}
        if lod.topology_children and not required.issubset(lod.topology_children):
            missing = ", ".join(sorted(required - lod.topology_children))
            raise ValueError(f"LOD {lod.name} missing required {missing} child")
        if not lod.topology_children and (lod.vertices.size == 0 or lod.indices.size == 0):
            raise ValueError(f"LOD {lod.name} missing required VERTICES or INDICES child")
        if lod.vertices.ndim != 2 or lod.vertices.shape[1] != 3:
            raise ValueError("VERTICES must contain XYZ records")
        if lod.indices.ndim != 2 or lod.indices.shape[1] != 3:
            raise ValueError("INDICES must be triangle-aligned")
        vertex_count = len(lod.vertices)
        if np.any(lod.indices < 0) or np.any(lod.indices >= vertex_count):
            raise ValueError("Indices must be within [0, VertexCount)")
        slots = tuple(material.source_slot_index for material in lod.materials)
        if any(slot is None or slot < 0 for slot in slots) or len(set(slots)) != len(slots):
            raise ValueError("Material slots must be unique non-negative indices")
        if expected_slots is None:
            expected_slots = slots
        elif slots != expected_slots:
            raise ValueError("Material slots must match across LODs")
        for material in lod.materials:
            if material.first_index < 0 or material.first_index % 3 or material.num_faces < 0:
                raise ValueError("Material slot ranges must be triangle-aligned")
            if material.first_index // 3 + material.num_faces > len(lod.indices):
                raise ValueError("Material slot range exceeds LOD indices")
        summaries.append(UEMODELLodTopologySummary(lod_index, vertex_count, lod.indices.size, slots))
    return summaries


def _validate_uemodel_skeleton(
    skeleton: UEModelSkeleton | None,
    identity: FModelSkeletonIdentity | None,
) -> UEMODELSkeletonTopologySummary | None:
    if skeleton is None:
        if identity is not None:
            raise ValueError("FModel skeleton identity requires SKELETON")
        return None
    layout_hash = compute_skeleton_layout_hash(skeleton.bones)
    if identity is not None:
        if identity.bone_layout_hash != layout_hash:
            raise ValueError("Skeleton bone layout hash must match Skeleton Identity V1 layout hash")
        if skeleton.metadata is not None and identity.skeleton_path != skeleton.metadata.skeleton_path:
            raise ValueError("Skeleton identity path must match SKELETON metadata")
    return UEMODELSkeletonTopologySummary(len(skeleton.bones), layout_hash)


def validate_uemodel_topology(
    lods: list[UEModelLOD],
    skeleton: UEModelSkeleton | None,
    identity: FModelSkeletonIdentity | None,
) -> UEMODELTopologySummary:
    """Validate decoded UEMODEL topology while avoiding a classes.py import cycle."""
    lod_summaries = _validate_uemodel_lods(lods)
    skeleton_summary = _validate_uemodel_skeleton(skeleton, identity)
    for lod in lods:
        for weight in lod.weights:
            if weight.vertex_index < 0 or weight.vertex_index >= len(lod.vertices):
                raise ValueError("Weight vertex_index must be within [0, VertexCount)")
            if skeleton is None:
                raise ValueError("Weights require SKELETON")
            if weight.bone_index < 0 or weight.bone_index >= len(skeleton.bones):
                raise ValueError("Weight bone_index must be within [0, BoneCount)")
    return UEMODELTopologySummary(tuple(lod_summaries), skeleton_summary)

