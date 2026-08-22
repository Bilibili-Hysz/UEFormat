<div align="center">

# UEFormat — FModel / Blender Edition

Canonical Blender reader for the FModel → UEFormat → Blender pipeline.

[![Blender](https://img.shields.io/badge/Blender-5.2+-blue?logo=blender&logoColor=white&color=orange)](https://www.blender.org/download/)
[![Release](https://img.shields.io/github/release/hysz-01/UEFormat)](https://github.com/hysz-01/UEFormat/releases)

</div>

## Scope

This fork keeps upstream UEFormat compatibility where possible and defines the
canonical Blender behavior for the FModel workflow:

```text
FModel / CUE4Parse
  -> .uemodel / .ueanim / .uepose
  -> io_scene_ueformat
  -> Blender geometry, materials, animation, pose, diagnostics
```

**Addon version:** V10 manifest `0.10.0` (migration branch; not released)
**Minimum Blender version:** `4.2.0`
**Current workflow target:** Blender 5.2
**Canonical addon source:** `plugins/blender/io_scene_ueformat/`

The V9 frozen specification is maintained at
[`../docs/current/UEFORMAT_BLENDER_PIPELINE_SPEC.md`](../docs/current/UEFORMAT_BLENDER_PIPELINE_SPEC.md).
The V9 frozen machine contract is
[`../docs/current/ueformat-content-contract-v1.json`](../docs/current/ueformat-content-contract-v1.json).

## Supported FModel profile

| File | Import result | FModel additions |
|---|---|---|
| `.uemodel` | geometry, skeleton, LODs, materials, textures | material links, texture identity, PBR roles, skeleton identity |
| `.ueanim` | Blender Action | target skeleton identity and source animation provenance |
| `.uepose` | pose/shape-key mutation or pose result | target skeleton identity and source pose provenance |

The documented FModel profile does **not** use PSK, PSA, GLTF, OBJ or material
JSON parsing as hidden import fallbacks. FModel can still expose its official
non-UEFormat exporters, but they are outside this addon's frozen V1 contract.

## Resource and material behavior

- Standalone `.uemodel` resources resolve relative to the model owner when
  `LocationMode.Relative` is declared.
- URI resolution rejects absolute/scheme/drive paths, unsafe traversal, root
  escapes and integrity mismatches.
- External resources carry byte length and SHA-256 checks; embedded resources
  are resolved only through declared texture IDs.
- Material JSON sidecars are metadata only. The importer does not scan JSON or
  guess texture names when FModel links are missing.
- Node graphs preserve readable UV → texture → processor → Principled layouts,
  PBR/custom frames, declared UV sets and FModel provenance metadata.

## Import reports

Each model, animation or pose operation records a stable result with kind,
status, source file and an actionable error summary.

## GUI and headless registration

Normal Blender registration:

```python
import io_scene_ueformat
io_scene_ueformat.register()
```

Vendor/headless registration without panels or import-menu entries:

```python
import io_scene_ueformat
io_scene_ueformat.register_headless()
# equivalent: io_scene_ueformat.register(headless=True)
```

Both paths use the same importer, reader, resource resolver and report contract.
A vendor must not maintain a second protocol implementation.

## V10 migration status

The V10 branch is source-complete for standard V10 parsing plus additive
FModel materials/textures/skeleton provenance. It reads V9 legacy FModel
semantic records and V10 FModel attributes, but V9 readers cannot read V10
files. V10 release is blocked on real FModel output and Blender headless/GUI
validation; do not sync this worktree into a user add-on directory yet.

See [V10 migration notes](docs/v10-migration.md) for the frozen producer wire
contract, V9 bridge and test gates.

## Deployment parity

The following runtime files are parity-critical between this source, the
FModel Tools V2 vendor and a managed Blender installation:

```text
importer/wire.py
importer/extensions/fmodel/
importer/import_context.py
importer/fmodel_v10_adapter.py
importer/runtime_*.py
importer/create/
op/import_helpers.py
```

Use the workspace preflight/release tooling to verify source, ZIP and managed
addon parity before distribution.

## Format documentation

- [Shared UEFormat binary conventions](docs/generic.md)
- [UEModel wire details](docs/uemodel.md)
- [UEAnim wire details](docs/ueanim.md)
- [UEPose wire details](docs/uepose.md)
- [V10 migration notes](docs/v10-migration.md)
- [Unified FModel → Blender specification](../docs/current/UEFORMAT_BLENDER_PIPELINE_SPEC.md)
- [Frozen content contract](../docs/current/UEFORMAT_BLENDER_CONTENT_CONTRACT.md)
- [URI contract](../docs/current/RELATIVE_RESOURCE_URI_CONTRACT.md)

## Testing

Run the Blender addon's Python suite from this repository root:

```powershell
pytest -q
```

The suite covers protocol readers, material/texture resolution, operator
reports, fixtures and release/source parity. Automated
coverage does not replace real package-backed export, independent parse or
Blender GUI validation; those remain separate release gates.

## Upstream attribution

This repository remains a working fork of
[h4lfheart/UEFormat](https://github.com/h4lfheart/UEFormat). Upstream format
and plugin work remains credited to its maintainers. This edition adds the
FModel-specific export/import contract, provenance, URI safety, scene
transaction handling and parity verification.
