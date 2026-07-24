# FModel PBR Node Layout Design

## [S1] Goal

Make V2 Direct Principled material graphs compact and readable without changing FModel PBR role semantics, resource resolution, or wire parsing. Each material keeps two independent node-frame groups and shares UV Map inputs within each group.

## [S2] Grouped Shared UV Inputs

`PBR Inputs` and `Custom / Unmapped (Preserved)` are independent groups. For each group, create at most one `ShaderNodeUVMap` for each declared existing UV set used by that group's loaded texture nodes.

Each group places its shared UV nodes at the left side of its frame and its texture nodes a fixed, moderate distance to their right. All textures using the same group/UV set link from that group-local UV Map output. No UV node is created for a skipped, unresolved, disabled, or missing-UV texture.

## [S3] PBR Ordering And Selection

Within `PBR Inputs`, texture and factor nodes are vertically ordered by the highest role assigned to each parameter:

```text
Base Color
Normal
AO
Emissive
Roughness
Factors, ranges, and untextured values
```

Nodes in each tier use compact fixed vertical spacing. A parameter with multiple roles appears once at its highest priority. Custom texture nodes preserve original material parameter order.

When a Base Color role resolves to an image texture, make that texture node the active node in the material node tree. If none resolves, use the first PBR image texture; if no PBR image resolves, leave the active node unchanged.

## [S4] Processing Column

The `PBR Inputs` frame has three left-to-right internal columns:

```text
shared UV Maps -> PBR texture/factor inputs -> PBR processing nodes -> Principled BSDF
```

Base/Emissive/AO `Mix Color` Multiply nodes, `Normal Map`, and `Roughness Midpoint` Value nodes belong to the processing column, right of PBR source nodes and left of Principled. AO strength remains the visible factor of its explicit AO Multiply node. Custom nodes have no shader output links.

## [S5] Boundaries

The layout remains entirely role-driven. Blender does not infer semantics from parameter names, image names, connections, or old-plugin heuristics. V1 conservative materials retain their existing graph behavior unless shared layout helpers can be used without changing their output semantics.

No FModel writer, V2 parser, location-mode safety, image repository, or Blender 5.1 installation changes are in scope.

## [S6] Verification

Blender 5.2 factory-startup tests must prove:

- both exact frames have no duplicate UV Maps per group/UV set;
- UV nodes are left of their group textures, with bounded horizontal distance;
- PBR source vertical order follows Base, Normal, AO, Emissive, Roughness, then factors;
- processing nodes are right of PBR sources and left of Principled;
- custom texture nodes remain in the custom frame without shader links;
- Base Color image is active, with the defined fallback;
- existing V1 material and deployed V2 add-on smokes pass; Blender 5.1 remains unchanged.
