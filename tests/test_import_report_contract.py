from __future__ import annotations

import ast
from pathlib import Path


IMPORT_HELPERS = (
    Path(__file__).parents[1]
    / "plugins"
    / "blender"
    / "io_scene_ueformat"
    / "op"
    / "import_helpers.py"
)


def _function(source: str, name: str) -> ast.FunctionDef:
    tree = ast.parse(source, filename=str(IMPORT_HELPERS))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"missing function {name!r}")


def _assigned_names(function: ast.FunctionDef) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = (node.target,)
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Attribute):
                names.add(target.attr)
    return names


def test_import_helpers_is_valid_python_and_persists_all_report_fields():
    source = IMPORT_HELPERS.read_text(encoding="utf-8")
    function = _function(source, "_set_last_import_report")

    assert {"last_import_summary", "last_import_report", "last_import_status"} <= _assigned_names(function)


def test_import_helpers_records_failed_and_success_statuses_at_operator_boundaries():
    source = IMPORT_HELPERS.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(IMPORT_HELPERS))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_set_last_import_report"
    ]

    statuses = {
        keyword.value.value
        for call in calls
        for keyword in call.keywords
        if keyword.arg == "status"
        and isinstance(keyword.value, ast.Constant)
        and isinstance(keyword.value.value, str)
    }
    assert statuses == {"FAILED", "SUCCESS"}
