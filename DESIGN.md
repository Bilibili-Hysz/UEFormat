# UE Format Blender Panel Design

## 1. Objective

Make the View3D sidebar the fastest reliable entry point for importing UE Format assets. The panel should surface each asset import action before its relevant options and keep expert controls available without presenting an undifferentiated wall of toggles.

## 2. Product Context

This is a Blender 5.2 technical add-on used to import `.uemodel`, `.ueanim`, and `.uepose` files. UESCENE support was retired in version 1.2.0 without a compatibility fallback. The dominant workflow is importing a model, animation, or pose with existing settings, then adjusting only the few controls needed for that asset.

## 3. Visual Foundations

- Use Blender-native Sidebar conventions: compact boxed sections, standard icons, direct property labels, and full-width action buttons.
- Top-level panel order: Model, Animation. Each uses Blender's native collapsible Panel header.
- Model contains Model Import and General; Animation contains Animation Import and Pose Import.
- Model also contains the last-import report action, because one Blender-scene-scoped report applies to every import mode without duplicating controls across panels.
- Model settings are grouped by source semantics: Geometry, Skeleton, Materials.
- Default-visible controls are the settings most likely to change per import; low-frequency skeleton controls live in an Advanced subsection.
- Use existing Blender icons only. No custom color, decorative separator, or non-native spacing system.

## 4. Accessibility

Use explicit action labels with file extensions where helpful. Disabled material-dependent controls retain existing enabled-state logic. Do not encode state only with icons or color.

## 5. Voice & Tone

Use concise technical Blender language: `Import UE Model`, `Import UE Animation`, `Import UE Pose`, `Geometry`, `Skeleton`, `Materials`, `Advanced`. Labels state the imported asset or affected data, not implementation details.

## 6. Implementation Practices

Keep settings and option serialization unchanged. Panel layout helpers must remain reusable by file-browser operator dialogs; sidebar-only actions are suppressed in dialog mode. Add recorder coverage for panel metadata/order, section/action ordering, material dependency states, and report-button availability.

## 7. Anti-Patterns

- Do not create a custom visual system that conflicts with Blender's native UI.
- Do not hide existing import settings behind new persistent state or a separate preferences screen.
- Do not mix import actions into a long property list without a semantic section boundary.
- Do not make users reconstruct an import result from transient status-bar messages or console output.

## 8. Decision-Making

Prioritize fast model/animation/pose imports over exposing every setting at once. Preserve all current import behavior and shared settings; this is an information-architecture change, not an option-policy change.

## 9. Workflow

Validate through the existing layout-recorder smoke test, full Python tests, and a fresh managed Blender 5.2 process. Deploy only to Blender 5.2 and verify source/deployed SHA parity.

## Decision Trace

```json
[
  {
    "decision": "Split model controls into Geometry, Skeleton, and Materials.",
    "reason": "Model import currently presents unrelated mesh, bone, and FModel material controls as one uninterrupted list.",
    "alternatives": ["Keep all properties flat", "Move all options into a single Advanced disclosure"],
    "tradeoff": "The layout uses a few additional labels and nested boxes."
  },
  {
    "decision": "Keep the panel visually Blender-native.",
    "reason": "This is a dense technical Sidebar tool where recognizability and direct manipulation matter more than branding.",
    "alternatives": ["Custom themed controls", "A card-based add-on dashboard"],
    "tradeoff": "The result is deliberately restrained rather than visually distinctive."
  },
  {
    "decision": "Store one Blender-scene-scoped textual last-import report with a copy action in the Model panel.",
    "reason": "Import outcomes are otherwise transient Blender reports that are difficult to share when diagnosing an asset.",
    "alternatives": ["Write a file for every import", "Duplicate report buttons in both top-level panels"],
    "tradeoff": "Only the latest import is retained and it is scoped to the active Blender scene."
  }
]
```
