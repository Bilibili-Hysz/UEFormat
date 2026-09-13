"""Pure-Python helpers for batch-writing UEFormat mesh attributes."""

from __future__ import annotations


def material_indices_for_sections(sections, polygon_count: int) -> list[int]:
    """Build Blender polygon material indices from UEFormat section ranges.

    UEFormat stores section offsets in triangle indices. Blender stores one
    material index per polygon, so each section range is converted once and
    written through ``Mesh.polygons.foreach_set`` by the caller.
    """
    if isinstance(polygon_count, bool) or not isinstance(polygon_count, int) or polygon_count < 0:
        raise ValueError("polygon_count must be a non-negative integer")

    indices = [0] * polygon_count
    for material_index, section in enumerate(sections):
        first_index = getattr(section, "first_index")
        face_count = getattr(section, "num_faces")
        if (
            isinstance(first_index, bool) or not isinstance(first_index, int)
            or isinstance(face_count, bool) or not isinstance(face_count, int)
        ):
            raise ValueError("UEFormat material section offsets must be integers")
        if first_index < 0 or face_count < 0 or first_index % 3:
            raise ValueError("UEFormat material section range is invalid")
        start_face = first_index // 3
        end_face = start_face + face_count
        if end_face > polygon_count:
            raise ValueError(
                f"UEFormat material section ends at face {end_face}, "
                f"but mesh contains {polygon_count} polygon(s)"
            )
        indices[start_face:end_face] = [material_index] * face_count
    return indices
