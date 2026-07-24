# UEScene Import Report Summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the Blender import panel compact by showing one-line import summaries while preserving full diagnostics for copying.

**Architecture:** Keep `last_import_report` unchanged as the complete report. Change only visible panel/report summary rendering so long warning lists are not displayed inline. The existing Copy Last Import Report operator remains the detailed diagnostics access path.

**Tech Stack:** Blender Python API, background UI smoke tests.

## Global Constraints

- The panel must show one concise summary line.
- Full import diagnostics must remain available through Copy Last Import Report.
- Do not remove or truncate stored `last_import_report`.
- Preserve import behavior and status JSON.

### Task 1: Compact visible report and regression smoke

**Files:**
- Modify: `io_scene_ueformat-fmodel/plugins/blender/io_scene_ueformat/op/panels.py`
- Modify: `io_scene_ueformat-fmodel/plugins/blender/io_scene_ueformat/op/import_helpers.py`
- Modify: `io_scene_ueformat-fmodel/tests/blender_material_options_ui_smoke.py`

- [ ] Add a compact visible-summary helper or use the existing `last_import_summary` field so the panel renders only one label line and never renders the full report body.
- [ ] Keep `_set_last_import_report()` callers writing complete reports unchanged.
- [ ] Add a UI smoke assertion with a multiline detailed report and assert the panel records only the one-line summary while the copy operator remains enabled.
- [ ] Run the focused UI smoke red before implementation, implement the smallest change, then run it green.
- [ ] Run the full explicit addon test suite and Blender UI smoke.
