"""Product regression: requested FModel resources fail closed; explicit opt-outs still work."""
from __future__ import annotations

import ast
import importlib
import json
from pathlib import Path
import subprocess
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / "src/io_scene_ueformat-fmodel/plugins/blender/io_scene_ueformat"


@pytest.fixture
def material_builder(monkeypatch):
    bpy = types.ModuleType("bpy")
    bpy.types = types.SimpleNamespace(Mesh=object, Material=object, Image=object)
    bpy.data = types.SimpleNamespace(materials=[], images=[])
    monkeypatch.setitem(sys.modules, "bpy", bpy)
    name = "io_scene_ueformat.importer.runtime_material_builder"
    monkeypatch.delitem(sys.modules, name, raising=False)
    return importlib.import_module(name)


def _slot():
    from io_scene_ueformat.importer.runtime_materials import FModelMaterialSlot, FModelMaterialParameter
    from io_scene_ueformat.importer.runtime_resources import TextureResource
    texture = TextureResource.identity(
        id=0, unreal_object_path="/Game/T.T", location_mode=1,
        uri="../Texture/T.png", color_space=1, packing=0, uv_set=0,
        stable_id="texture", expected_byte_length=4, expected_content_hash=b"x" * 32,
        identity_embedded=False,
    )
    return FModelMaterialSlot(0, "Body", "/Game/M.M", "../Material/M.json", 0,
        [FModelMaterialParameter("BaseColor", 1, "base_color", texture)])


class _NoIoImages:
    def resource_cache_key(self, *args):
        raise AssertionError("Explicitly disabled resources must not be accessed")
    def get_image(self, *args):
        raise AssertionError("Explicitly disabled resources must not be accessed")


def test_unmapped_opt_out_uses_same_selection_in_cache_and_builder(material_builder):
    from io_scene_ueformat.importer.runtime_materials import FModelMaterialParameter, FModelPBRInterface
    slot = _slot()
    slot.parameters[0].semantic = 'custom'
    slot.parameters.insert(0, FModelMaterialParameter('Color', 3, 'base_color', (1, 0, 0, 1)))
    interfaces = [FModelPBRInterface(0, 0, 1)]
    options = material_builder.FModelMaterialImportOptions(keep_unmapped=False)
    mesh = types.SimpleNamespace(uv_layers={'UV0': object()})
    assert material_builder._material_cache_identity(slot, interfaces, _NoIoImages(), options, mesh)
    assert material_builder._prevalidate_material(mesh, slot, interfaces, _NoIoImages(), lambda _: None, options)


def test_mapped_texture_still_participates_with_unmapped_opt_out(material_builder):
    from io_scene_ueformat.importer.runtime_materials import FModelPBRInterface
    options = material_builder.FModelMaterialImportOptions(keep_unmapped=False)
    with pytest.raises(AssertionError, match='must not be accessed'):
        material_builder._material_cache_identity(_slot(), [FModelPBRInterface(0, 0, 1)], _NoIoImages(), options)


def test_declared_resource_failures_reach_failed_report_and_rollback(tmp_path):
    output = tmp_path / "product-resource-failure.json"
    run = subprocess.run([
        sys.executable, "-B", str(ROOT / "scripts/audit_pipeline_failure_boundaries.py"),
        "--output", str(output),
    ], capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert len(report["cases"]) == 4
    for case in report["cases"]:
        assert case["resolverRejected"]
        assert case["operatorStatus"] == "FAILED"
        assert case["rollbackCalled"]
        assert not case["failureReportedAsSuccess"]
        assert case["materialWasNotBuilt"]


def test_explicit_external_opt_out_does_not_validate_or_load_files(material_builder):
    options = material_builder.FModelMaterialImportOptions(load_external=False)
    slot = _slot()
    images = _NoIoImages()
    mesh = types.SimpleNamespace(uv_layers={})
    assert material_builder._prevalidate_material(mesh, slot, [], images, lambda _: None, options)
    assert material_builder._material_cache_identity(slot, [], images, options)


@pytest.mark.parametrize("disabled", ["import_materials", "build_nodes"])
def test_explicit_material_or_node_opt_out_preserves_metadata_without_io(material_builder, disabled):
    from io_scene_ueformat.importer.runtime_materials import FModelMaterialLinks
    mesh = types.SimpleNamespace(materials=[{}], uv_layers={})
    options = material_builder.FModelMaterialImportOptions(**{disabled: False})
    material_builder.build_fmodel_materials(
        mesh, [types.SimpleNamespace(source_slot_index=0)], FModelMaterialLinks(slots=[_slot()]),
        _NoIoImages(), lambda _: None, options, source_slot_to_blender_index={0: 0},
    )
    assert ("fmodel_material_link" in mesh.materials[0]) == (disabled == "build_nodes")


def test_standard_only_model_does_not_invent_materials_or_scan_json():
    path = RUNTIME / "importer/create/model.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_build_fmodel_materials")
    module = ast.Module(body=[function], type_ignores=[])
    ast.fix_missing_locations(module)
    env = {}
    exec(compile(module, str(path), "exec", flags=__import__("__future__").annotations.compiler_flag), env)
    # The real standard-only guard must return before any resolver/bpy/JSON
    # dependency is touched; none of those names is available in this scope.
    env["_build_fmodel_materials"](None, None, types.SimpleNamespace(import_fmodel_materials=True), None)


def test_material_cache_and_image_loading_cannot_downgrade_invalid_resource(material_builder):
    class InvalidImages:
        def resource_cache_key(self, *args):
            raise ValueError("FModel identity texture SHA-256 mismatch")
        def get_image(self, *args):
            raise ValueError("FModel identity texture SHA-256 mismatch")
    slot = _slot()
    warnings = []
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        material_builder._find_matching_material(slot, [], InvalidImages(), material_builder.FModelMaterialImportOptions())
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        material_builder._load_image(slot.parameters[0], "base_color", InvalidImages(), warnings.append)
    assert not warnings
