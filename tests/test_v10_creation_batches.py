from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace


IMPORTER_ROOT = Path(__file__).parents[1] / "plugins" / "blender" / "io_scene_ueformat" / "importer"


def _load(name: str):
    path = IMPORTER_ROOT / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"ueformat_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_material_sections_are_expanded_to_polygon_indices():
    batch = _load("mesh_batch")
    sections = [
        SimpleNamespace(first_index=0, num_faces=2),
        SimpleNamespace(first_index=6, num_faces=1),
        SimpleNamespace(first_index=9, num_faces=1),
    ]
    assert batch.material_indices_for_sections(sections, 4) == [0, 0, 1, 2]


def test_material_section_validation_rejects_out_of_range_triangle_offsets():
    batch = _load("mesh_batch")
    for section in (
        SimpleNamespace(first_index=1, num_faces=1),
        SimpleNamespace(first_index=0, num_faces=2),
        SimpleNamespace(first_index=-3, num_faces=1),
    ):
        try:
            batch.material_indices_for_sections([section], 1)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid section range was accepted")


def test_morph_deltas_accumulate_repeated_vertex_indices():
    batch = _load("morph_batch")
    deltas = [
        SimpleNamespace(vertex_index=0, position=(1.0, 0.0, 0.0)),
        SimpleNamespace(vertex_index=0, position=(0.0, 2.0, 0.0)),
    ]
    result = batch.morph_coordinates([0.0, 0.0, 0.0], deltas)
    assert result.tolist() == [1.0, 2.0, 0.0]


def test_creation_capture_deduplicates_and_discards_datablocks():
    tracker = _load("creation_tracker").UEFormatImportCapture()
    first = object()
    tracker.track("objects", first)
    tracker.track("objects", first)
    tracker.track("meshes", first)
    assert tracker.counts == {"objects": 1, "meshes": 1}

    tracker.discard("objects", first)
    assert tracker.objects == ()
    assert tracker.counts == {"meshes": 1}


def test_creation_capture_handles_invalidated_blender_wrappers():
    tracker_module = _load("creation_tracker")

    class RemovedObject:
        def as_pointer(self):
            raise ReferenceError("StructRNA of type Object has been removed")

    removed = RemovedObject()
    tracker = tracker_module.UEFormatImportCapture()
    tracker.track("objects", removed)
    tracker.discard("objects", removed)
    assert tracker.objects == ()


def test_creation_capture_untracks_wrappers_invalidated_after_tracking():
    tracker_module = _load("creation_tracker")

    class DyingObject:
        def __init__(self):
            self.calls = 0

        def as_pointer(self):
            self.calls += 1
            if self.calls == 1:
                return 4242
            raise ReferenceError("StructRNA of type Object has been removed")

    item = DyingObject()
    tracker = tracker_module.UEFormatImportCapture()
    tracker.track("objects", item)
    assert tracker.counts == {"objects": 1}

    # The template-object flow removes the datablock before calling discard,
    # so the wrapper can no longer produce its tracked pointer identity.
    tracker.discard("objects", item)
    assert tracker.objects == ()
    assert tracker.counts == {}


def test_creation_failure_paths_register_and_restore_mutable_state():
    importer_root = Path(__file__).parents[1] / "plugins" / "blender" / "io_scene_ueformat" / "importer"
    skeleton = (importer_root / "create" / "skeleton.py").read_text(encoding="utf-8")
    anim = (importer_root / "create" / "anim.py").read_text(encoding="utf-8")
    pose = (importer_root / "create" / "pose.py").read_text(encoding="utf-8")

    bind_lod = skeleton[skeleton.index("def _bind_lod("):]
    assert bind_lod.index('creation_tracker.track("objects", armature_object)') < bind_lod.index(
        'armature_object.show_in_front = True',
    )
    for marker in (
        "original_armature_action",
        "original_armature_action_slot",
        "original_pose_matrices",
        "restore_animation_data(",
        "if not completed:",
    ):
        assert marker in anim
    for marker in (
        "original_pose_matrices",
        "original_constraint_mutes",
        "original_shape_key_sliders",
        "if not completed:",
    ):
        assert marker in pose
