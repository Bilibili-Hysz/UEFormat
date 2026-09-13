from __future__ import annotations

import math
from collections.abc import Callable


CONVERSION_POLICY = "seconds_preserving_v1"


def _effective_blender_fps(blender_fps: float, blender_fps_base: float) -> float:
    if not math.isfinite(blender_fps) or blender_fps <= 0:
        raise ValueError("Blender FPS must be finite and positive")
    if not math.isfinite(blender_fps_base) or blender_fps_base <= 0:
        raise ValueError("Blender fps_base must be finite and positive")
    return blender_fps / blender_fps_base


def resolve_animation_source_rate(source_rate: float | None, fallback_rate: float) -> float:
    """Use a valid UE rate, falling back to the effective Blender scene rate."""

    if source_rate is not None and math.isfinite(source_rate) and source_rate > 0:
        return float(source_rate)
    if math.isfinite(fallback_rate) and fallback_rate > 0:
        return float(fallback_rate)
    raise ValueError("Animation source and fallback rates must contain a finite positive rate")


def make_source_tick_converter(
    source_rate: float,
    blender_fps: float,
    blender_fps_base: float,
) -> Callable[[float], float]:
    if not math.isfinite(source_rate) or source_rate <= 0:
        raise ValueError("UEAnim source rate must be finite and positive")
    effective_blender_fps = _effective_blender_fps(blender_fps, blender_fps_base)
    scale = effective_blender_fps / source_rate
    return lambda source_tick: source_tick * scale


def source_tick_to_blender_frame(
    source_tick: float,
    *,
    source_rate: float,
    blender_fps: float,
    blender_fps_base: float,
) -> float:
    return make_source_tick_converter(source_rate, blender_fps, blender_fps_base)(source_tick)


def animation_time_provenance(
    source_rate: float,
    blender_fps: float,
    blender_fps_base: float,
) -> dict[str, float | int | str]:
    effective_blender_fps = _effective_blender_fps(blender_fps, blender_fps_base)
    make_source_tick_converter(source_rate, blender_fps, blender_fps_base)
    return {
        "source_rate": source_rate,
        "source_time_unit": "source_tick",
        "blender_fps": blender_fps,
        "blender_fps_base": blender_fps_base,
        "effective_blender_fps": effective_blender_fps,
        "conversion_policy": CONVERSION_POLICY,
    }
