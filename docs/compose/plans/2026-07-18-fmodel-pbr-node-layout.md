# FModel PBR Node Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compact V2 material node graphs through per-frame shared UV inputs, deterministic PBR ordering, an explicit processing column, and Base Color activation.

**Architecture:** Refactor only V2 graph construction in `material_builder.py`. It first classifies loaded parameters into PBR/custom frame groups, creates one UV Map per group/UV set, then positions source and processing columns before linking the unchanged role-driven Principled graph.

**Tech Stack:** Python 3.13, Blender 5.2 LTS, pytest, Blender background smoke scripts.

## Global Constraints

- The layout remains entirely role-driven; no parameter-name, image-name, or connection inference.
- Each frame contains at most one UV Map node for each used UV set.
- PBR ordering is Base Color, Normal, AO, Emissive, Roughness, then factors/ranges/untextured values.
- PBR processing nodes are right of PBR source nodes and left of Principled.
- Custom texture nodes remain unconnected to shader inputs.
- Do not alter wire parsing, FModel export, resource safety, or Blender 5.1.

---

### Task 1: Shared UV And Compact V2 Layout

**Covers:** [S1, S2, S3, S4, S5, S6]

**Files:**
- Modify: `plugins\blender\io_scene_ueformat\importer\material_builder.py`
- Modify: `tests\blender_material_v2_smoke.py`
- Modify: `tests\blender_deployed_material_v2_smoke.py`

**Interfaces:**
- Continues consuming `FModelMaterialSlot`, `FModelPBRInterface`, `FModelImageRepository`, and V2 role values.
- Produces the same Direct Principled semantic links plus two compact frames.

- [ ] **Step 1: Add failing Blender layout assertions**

```python
assert len(uv_nodes(pbr_frame, "UV0")) == 1
assert len(uv_nodes(custom_frame, "UV0")) == 1
assert max(node.location.x for node in uv_nodes(pbr_frame, "UV0")) < min_texture_x(pbr_frame)
assert ordered_y(tree, ["Odd_Base_Texture", "Odd_Normal", "Odd_AO", "Odd_Emissive"])
assert processing_x > source_x and processing_x < principled.location.x
assert tree.nodes.active.label == "Odd_Base_Texture"
```

- [ ] **Step 2: Run red Blender smoke**

Run:

```powershell
& "F:\Blender\Software\blender-5.2.0-windows-x64\blender.exe" --background --factory-startup --python "tests\blender_material_v2_smoke.py"
```

Expected: fail because current builder creates one UV node per texture and has no deterministic processing/source columns.

- [ ] **Step 3: Implement compact V2 layout**

Create one group-local UV Map node keyed by `(frame, uv_set)`. Place UV nodes at the group-left column, sources at the group source column, and V2 processing helpers in the PBR processing column. Sort PBR nodes by fixed role priority and source index; custom textures retain parameter order. Set the selected Base Color texture active, or the first PBR texture if no Base Color resolves.

- [ ] **Step 4: Run green layout and regressions**

Run the V2 smoke, V1 material builder smoke, deployed add-on smoke after deployment, and full Python suite. Expected: all pass.

### Task 2: Deploy And Verify Blender 5.2 Layout

**Covers:** [S5, S6]

**Files:**
- Deploy: `C:\Users\86177\AppData\Roaming\Blender Foundation\Blender\5.2\scripts\addons\io_scene_ueformat`

- [ ] **Step 1: Copy only the managed Blender 5.2 package**

Synchronize the development add-on into the Blender 5.2 managed path. Do not access Blender 5.1 paths.

- [ ] **Step 2: Verify installed add-on behavior**

Run:

```powershell
& "F:\Blender\Software\blender-5.2.0-windows-x64\blender.exe" --background --factory-startup --python "tests\blender_deployed_material_v2_smoke.py"
```

Update this smoke to assert installed V2 shared UV/frame layout and active Base Color node through `addon_utils.enable("io_scene_ueformat")`.

- [ ] **Step 3: Final checks**

Run Python tests, development V1/V2 Blender smokes, installed V2 smoke, `python -m compileall -q plugins tests`, source/deployment SHA-256 comparisons, and `git diff --check`.

## Self-Review

- Task 1 owns all layout behavior and its factory-startup evidence.
- Task 2 owns Blender 5.2 deployment and normal-add-on verification.
- Every S1-S6 requirement maps to Task 1 or Task 2.
