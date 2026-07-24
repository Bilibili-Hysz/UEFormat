# FModel PBR Node Vertical Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align PBR and Custom frame first rows and give Image Texture rows sufficient vertical clearance in V2 material graphs.

**Architecture:** Keep independent frame-local source cursors in the V2 builder. Both begin at zero; every emitted source consumes a 320-unit row in its own frame. PBR processing helpers retain their source-relative placement but use a 160-unit collision minimum.

**Tech Stack:** Python 3.13, Blender 5.2 LTS, Blender background smoke tests.

## Global Constraints

- Apply only to V2 node layout in `material_builder.py`.
- PBR and Custom source cursors each begin at local Y `0`.
- Source row spacing is exactly `320` Blender node units.
- Processing collision spacing is at least `160` Blender node units.
- Do not alter semantic roles, resource loading, parsing, FModel export, or Blender 5.1.

---

### Task 1: Align Frame Rows And Widen Texture Spacing

**Covers:** Current approved vertical-layout decision.

**Files:**
- Modify: `plugins\blender\io_scene_ueformat\importer\material_builder.py`
- Modify: `tests\blender_material_v2_smoke.py`
- Modify: `tests\blender_deployed_material_v2_smoke.py`

- [ ] **Step 1: Add failing Blender V2 layout assertions**

```python
assert pbr_first_source.location.y == 0
assert custom_first_source.location.y == 0
assert pbr_first_texture.location.y - pbr_second_texture.location.y == 320
assert all(abs(a.location.y - b.location.y) >= 160 for a, b in processing_pairs)
```

- [ ] **Step 2: Run the factory-startup smoke and observe red**

```powershell
& "F:\Blender\Software\blender-5.2.0-windows-x64\blender.exe" --background --factory-startup --python "tests\blender_material_v2_smoke.py"
```

Expected: fail because the old global 180-unit cursor places Custom after PBR rows and processing helpers only require 120-unit separation.

- [ ] **Step 3: Implement independent cursors and constants**

Use named source and processing spacing constants inside `_build_v2_material`. Increment `pbr_y` only after PBR nodes and `custom_y` only after Custom nodes. Raise every processing collision threshold/step from 120 to 160.

- [ ] **Step 4: Run green development and deployment regression checks**

Run the V2 smoke, V1 material smoke, full Python suite, then deploy only to Blender 5.2 and run the managed add-on smoke with equivalent layout assertions.

## Self-Review

- The one task covers the approved 320/160 constants, independent frame alignment, and deployment verification.
- No protocol or material semantic change is included.
