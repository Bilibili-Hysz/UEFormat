# UEFormat V10 migration notes

> This document describes the current importer migration seam. It is not a
> frozen producer contract and must not be used to generate release files.

## Header

V10 files (`FileVersion >= 10`) extend the shared header with `ObjectPath`:

```text
uint8[8] Magic                 // "UEFORMAT"
FString Identifier             // UEMODEL, UEANIM or UEPOSE
uint8 FileVersion              // 10 for the current V10 format
FString ObjectName
FString ObjectPath             // V10 only
bool IsCompressed
```

When compressed, the header is followed by `CompressionFormat`,
`UncompressedSize`, `CompressedSize` and the compressed body. The importer
requires the declared compressed size to equal the remaining file bytes and
requires the decompressed body size to equal the declared uncompressed size.
GZIP expansion is bounded by the declared size. ZSTD is resolved lazily from
the addon's bundled runtime.

## Standard attribute envelope

The V10 body is a sequence of length-bounded attributes:

```text
int32 AttributeCount
repeat AttributeCount:
    FString AttributeName
    int32 ByteSize
    uint8[ByteSize] Payload
```

Unknown attributes are skipped by their `ByteSize`. Standard readers reject
negative or excessive attribute counts, truncated payloads and trailing bytes
outside the envelope. Standard payload readers also require known payloads to
be fully consumed. This prevents a malformed payload from silently shifting
the next field while preserving forward-compatible unknown-attribute skips.

### Standard attributes currently covered by the reader

| File | Attributes |
| --- | --- |
| UEMODEL | `LODS`, `SKELETON`, `COLLISION` |
| UEANIM | `METADATA`, `TRACKS`, `CURVES` |
| UEPOSE | `POSES`, `CURVES` |

Nested LOD and skeleton attributes use the same envelope shape. The exact
payload readers are implemented in `importer/deserialize/`; pure Python
synthetic coverage is in `tests/test_v10_standard_deserialize.py`.

## FModel extension boundary

`FMODEL_*` attributes are collected only through
`importer/extensions/fmodel/` and only when
`UEFormatOptions.enable_fmodel_v10_extensions` is explicitly enabled. The V10
snapshot layer validates names, lengths, file-kind applicability and duplicate
handling, but does not yet decode V10 producer payloads. The separate V9 bridge
below can decode selected legacy semantics; it must not be treated as a V10
producer contract.

### V9 legacy bridge

The migration line now has a gated V9 bridge in `extensions/fmodel/legacy.py`.
It scans the V9 `Name + ArraySize + ByteSize + Payload` stream and retains
recognized FModel records without changing the standard legacy parser. The V9
V1 skeleton and target-skeleton identity payloads are decoded into a neutral
`FModelIdentityRecord` by `extensions/fmodel/identity.py`.

This is an intake/semantic checkpoint, not complete FModel compatibility:
V9 material links, identity/embedded texture shapes and PBR interface records
now decode into neutral snapshots, but URI resolution, cross-resource binding
projection and Blender material creation are still not connected to the V10
creation layer.
The gate remains disabled by default.

The five checked-in V9 FModel fixtures also pass through the standard legacy
DTO router. This confirms that additive FModel chunks do not desynchronize the
standard model, animation or pose payload. It does not exercise `bpy`, Blender
scene creation or real filesystem resource resolution.

The V9 FModel chunk payloads must not be assumed to be valid V10 payloads.
Before any producer or Blender release integration, a versioned V10 extension
wire contract and a real FModel V10 fixture are required.

## Verification

Run from this repository root:

```powershell
$env:PYTHONPATH='plugins/blender'
python -B -m pytest -q tests/test_v10_fmodel_extension_contract.py tests/test_v10_standard_deserialize.py
```

The current migration checkpoint reports 41 passed tests. This does not
replace real V10 producer output, Blender GUI validation or ZSTD validation
against Blender's bundled wheel.

This is a source-only Phase 3 checkpoint. No Candidate was built, no Blender
user installation was updated, and the V9/R60 release line remains untouched.
