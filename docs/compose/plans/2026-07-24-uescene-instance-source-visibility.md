# UEScene Instance Source Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make ISM/HISM collection instances understandable in Blender by grouping them semantically and retaining an explicit editable source template.

**Architecture:** Keep Collection Instance rendering and shared Mesh data unchanged. Organize instance controllers under a readable `Instanced Geometry` collection and templates under `UEFormat Technical/Source Templates`; retain the full template hierarchy and add controller provenance pointing to the template collection.

**Tech Stack:** Blender Python API, background smoke tests.

## Global Constraints

- At least one editable source object must remain for every ISM/HISM template.
- Prefer retaining the full template hierarchy when it contains multiple objects.
- Do not expand instances or duplicate mesh data as part of this visibility improvement.
- Preserve existing optional `Flatten ISM/HISM Instances` behavior.

### Task 1: Add semantic collection organization and source metadata

**Files:**
- Modify: `io_scene_ueformat-fmodel/plugins/blender/io_scene_ueformat/importer/scene_import.py`
- Modify: `io_scene_ueformat-fmodel/plugins/blender/io_scene_ueformat/op/import_helpers.py`
- Modify: `io_scene_ueformat-fmodel/tests/blender_uescene_flatten_smoke.py`

- [ ] Add a scene-owned `Instanced Geometry` collection under the imported UEScene root and a `UEFormat Technical` child containing `Source Templates`.
- [ ] Move ISM/HISM controller objects into `Instanced Geometry` while preserving their collection-instance children and transforms.
- [ ] Move each generated template collection under `Source Templates`; retain all template objects and parent links.
- [ ] Set controller properties `uescene_instance_representation = "Collection Instance"`, `uescene_instance_count`, `uescene_source_template_collection`, and existing source asset provenance.
- [ ] Rename controllers to a readable form containing asset name and instance count, while retaining stable source properties for programmatic lookup.
- [ ] Add smoke assertions for collection hierarchy, template retention, at least one editable template object, and controller-to-template property linkage.
- [ ] Run the smoke before implementation to establish the failing assertions, implement minimally, and run it green.

### Task 2: Verify default, expanded, and release behavior

**Files:**
- Modify: `io_scene_ueformat-fmodel/tests/blender_material_options_ui_smoke.py` only if the new collection behavior affects UI expectations.

- [ ] Run the instance flatten smoke and UI smoke.
- [ ] Run the full explicit addon Python suite.
- [ ] Verify `Flatten ISM/HISM Instances` disabled keeps shared templates and no mesh duplication.
- [ ] Verify enabled instance flatten still removes the controller/template structures after materializing ordinary objects.
- [ ] Synchronize the managed Blender 5.2 addon and rebuild deterministic release ZIPs; run release parity validation.
