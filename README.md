<div align="center">

# UEFormat — FModel / Blender Edition

An edited UEFormat pipeline for moving Unreal Engine assets from FModel into
Blender through validated `.uemodel`, `.ueanim`, and `.uescene` files.

This repository is a working fork of [h4lfheart/UEFormat](https://github.com/h4lfheart/UEFormat).
It keeps the original exchange format and plugins while extending the workflow
around FModel exports, Blender scene import, material links, and reproducible
tests.

[![Discord](https://discord.com/api/guilds/866821077769781249/widget.png?style=shield)](https://discord.gg/DZ5YFXdBA6)
[![Blender](https://img.shields.io/badge/Blender-5.2+-blue?logo=blender&logoColor=white&color=orange)](https://www.blender.org/download/)
[![Unreal](https://img.shields.io/badge/Unreal-5.8+-blue?logo=unreal-engine&logoColor=white&color=white)](https://www.unrealengine.com/en-US/download)
[![Release](https://img.shields.io/github/release/hysz-01/UEFormat)](https://github.com/hysz-01/UEFormat/releases)
[![Downloads](https://img.shields.io/github/downloads/hysz-01/UEFormat/total?color=green)](https://github.com/hysz-01/UEFormat/releases)

</div>

## Edited pipeline

```text
Unreal Engine archives
        │
        ▼
FModel / CUE4Parse exporters
        │  .uemodel · .ueanim · .uescene
        ▼
UEFormat validation and resource links
        │
        ▼
Blender 5.2 importer
```

The edited pipeline focuses on complete, inspectable imports instead of only
single static meshes. It preserves source provenance and reports failures at
the scene and resource boundary so exported data can be diagnosed and tested.

## Plugins

- [Blender importer](https://github.com/hysz-01/UEFormat/tree/fmodel/material-links-v1/plugins/blender)
- [Unreal Engine plugin](https://github.com/hysz-01/UEFormat/tree/fmodel/material-links-v1/plugins/unreal)

## Edited capabilities

- UEScene actor, hierarchy, static-mesh, material, and light import into Blender.
- Strict required-model validation with staged scene import and rollback on failure.
- UEMODEL geometry, skeleton identity, source LOD selection, and material-slot mapping.
- FModel material and texture URI resolution with explicit source-slot provenance.
- Material parameter metadata retention and safe handling of sparse or renamed links.
- Optional ISM/HISM flattening while retaining shared instanced geometry by default.
- UEANIM timing and source tick semantics preserved for Blender animation import.
- Operator, deployed-Blender, fixture, release-parity, and golden-data coverage.

## Supported Exporters
- [FortnitePorting](https://github.com/h4lfheart/FortnitePorting)
- [FModel](https://github.com/4sval/FModel)
- [CUE4Parse](https://github.com/FabianFG/CUE4Parse)

## Testing

The Python suite covers protocol readers, FModel material links, UEScene
validation and staging, Blender operator behavior, fixture-based imports, and
release source parity. From this repository root, run:

```powershell
pytest -q
```

Blender deployment smoke tests are kept alongside the regular Python tests so
the source addon and the managed Blender addon can be checked separately.

## Format Specification
- [Generic](https://github.com/h4lfheart/UEFormat/tree/main/docs/generic.md)
- [UEModel](https://github.com/h4lfheart/UEFormat/tree/main/docs/uemodel.md)
- [UEAnim](https://github.com/h4lfheart/UEFormat/tree/main/docs/ueanim.md)
- [UEPose](https://github.com/h4lfheart/UEFormat/tree/main/docs/uepose.md)

## Upstream and scope

The exchange format remains compatible with the upstream UEFormat project
wherever possible. Changes in this fork are centered on the FModel-to-Blender
workflow: scene-level import reliability, material and texture resource links,
source identity, diagnostics, and verification. Upstream protocol and plugin
work remains credited to the original maintainers.
