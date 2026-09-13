from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import pytest


MODULE_PATH = (
    Path(__file__).parents[1]
    / "plugins"
    / "blender"
    / "io_scene_ueformat"
    / "importer"
    / "animation_time.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("animation_time", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("source_rate", [24.0, 30.0, 60.0, 30000 / 1001])
def test_one_source_second_has_one_second_blender_duration(source_rate: float):
    animation_time = load_module()

    blender_frame = animation_time.source_tick_to_blender_frame(
        source_rate,
        source_rate=source_rate,
        blender_fps=24,
        blender_fps_base=1.0,
    )

    assert math.isclose(blender_frame / 24.0, 1.0, abs_tol=1e-9)


def test_bone_and_morph_ticks_use_identical_fractional_conversion():
    animation_time = load_module()
    convert = animation_time.make_source_tick_converter(
        source_rate=30000 / 1001,
        blender_fps=60,
        blender_fps_base=1.001,
    )

    bone_frames = [convert(tick) for tick in (0, 10, 20, 30)]
    morph_frames = [convert(tick) for tick in (0, 10, 20, 30)]

    assert bone_frames == morph_frames
    assert math.isclose(bone_frames[-1], 60.0, abs_tol=1e-9)


@pytest.mark.parametrize("source_rate", [None, 0.0, -1.0, float("nan")])
def test_invalid_animation_source_rate_falls_back_to_scene_rate(source_rate):
    animation_time = load_module()

    assert animation_time.resolve_animation_source_rate(source_rate, 24.0) == 24.0


def test_invalid_animation_source_and_scene_rates_are_rejected():
    animation_time = load_module()

    with pytest.raises(ValueError, match="finite positive rate"):
        animation_time.resolve_animation_source_rate(0.0, 0.0)


@pytest.mark.parametrize(
    ("source_rate", "blender_fps", "blender_fps_base"),
    [(0.0, 24, 1.0), (-1.0, 24, 1.0), (30.0, 0, 1.0), (30.0, 24, 0.0)],
)
def test_invalid_timebase_is_rejected(source_rate: float, blender_fps: int, blender_fps_base: float):
    animation_time = load_module()

    with pytest.raises(ValueError, match="rate|FPS|fps_base"):
        animation_time.make_source_tick_converter(source_rate, blender_fps, blender_fps_base)


def test_provenance_records_source_and_effective_blender_rates():
    animation_time = load_module()

    provenance = animation_time.animation_time_provenance(
        source_rate=30000 / 1001,
        blender_fps=60,
        blender_fps_base=1.001,
    )

    assert provenance == {
        "source_rate": pytest.approx(30000 / 1001),
        "source_time_unit": "source_tick",
        "blender_fps": 60,
        "blender_fps_base": pytest.approx(1.001),
        "effective_blender_fps": pytest.approx(60 / 1.001),
        "conversion_policy": "seconds_preserving_v1",
    }


def test_importer_uses_one_converter_for_bone_and_morph_keys_and_stores_provenance():
    logic = (
        Path(__file__).parents[1]
        / "plugins"
        / "blender"
        / "io_scene_ueformat"
        / "importer"
        / "logic.py"
    ).read_text(encoding="utf-8")

    assert "convert_source_tick(key.frame)" in logic
    assert logic.count("convert_source_tick(key.frame)") == 4
    assert logic.count('["fmodel_animation_time"]') == 2


def test_validated_animation_binding_never_falls_back_after_an_intentional_skip():
    create_anim = (
        Path(__file__).parents[1]
        / "plugins"
        / "blender"
        / "io_scene_ueformat"
        / "importer"
        / "create"
        / "anim.py"
    ).read_text(encoding="utf-8")

    assert "if binding_plan is None:" in create_anim
    assert "bone = pose_bones.get(bound_name) if bound_name is not None else None" in create_anim
