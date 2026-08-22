# UEFormat V10 migration notes

> This document defines the V10 FModel extension seam implemented in this
> migration branch. It is source-complete, but it remains **unreleased** until
> Blender headless/GUI and real FModel producer gates pass.

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

`FMODEL_*` attributes are additive to the V10 body and are collected only
through `importer/extensions/fmodel/`. `enable_fmodel_extensions` is the
explicit import option (enabled by default for this FModel edition; callers may
disable it for a strictly standard-only import). The standard DTO parser always
skips unknown attributes by their byte sizes. Recognized FModel attributes have
bounded, duplicate-fail-closed decoding in a Blender-free layer.

### Frozen FModel V10 producer contract

Every recognized attribute has this outer payload envelope:

```text
uint8 EnvelopeVersion = 1
int32 ArraySize
uint8[] SemanticPayload
```

The semantic payloads are intentionally reused from the validated V9 records:

| File | Attribute | Array size | Semantic payload |
| --- | --- | --- | --- |
| UEMODEL | `FMODEL_MATERIALS` | number of linked sections | material-links V2 |
| UEMODEL | `FMODEL_TEXTURES` | `1` | identity texture table V1/V2 |
| UEMODEL | `FMODEL_SKELETON_IDENTITY` | `1` | skeleton identity V1 |
| UEANIM | `FMODEL_TARGET_SKELETON` | `1` | source animation path + skeleton identity V1 |
| UEPOSE | `FMODEL_TARGET_SKELETON` | `1` | source pose path + skeleton identity V1 |

`FMODEL_TEXTURES` is the sole V10 PBR binding table. Its canonical
`BaseColor`/`Normal`/`Orm` bindings produce synthetic runtime parameters for
the existing node builder. V10 producers **must not** emit
`FMODEL_PBR_INTERFACES`; that attribute is retained only for V9 legacy intake.
All logical resource URIs remain relative, forward-slash-only and bounded;
owner-relative/external identity resources are containment-checked and
SHA-256/byte-length verified before Blender loads them.

### V9 legacy bridge

The migration line now has a gated V9 bridge in `extensions/fmodel/legacy.py`.
It scans the V9 `Name + ArraySize + ByteSize + Payload` stream and retains
recognized FModel records without changing the standard legacy parser. The V9
V1 skeleton and target-skeleton identity payloads are decoded into a neutral
`FModelIdentityRecord` by `extensions/fmodel/identity.py`.

The V9 bridge decodes material links, identity/embedded texture shapes, PBR
interfaces and skeleton provenance into the same neutral snapshots used by V10.
Both V9 and V10 paths then use the common URI resolver, texture tagged-union,
node builder, material mapping and animation/pose identity validation. This is
semantic compatibility, not a promise that V9 can read V10 bytes.

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
python -B -m pytest -q
```

The migration suite includes real C# writer fixtures in
`tests/fixtures/fmodel_v10/` for NONE, GZIP and ZSTD. The ordinary Python
environment validates NONE/GZIP; the ZSTD fixture is skipped unless Blender's
bundled `zstandard` wheel is available. This does not replace real asset
export, Blender headless scene creation or GUI validation.

The branch keeps V9 API-shape tests from the old `classes.py` implementation
out of V10 collection (`tests/conftest.py`); their five checked-in V9 fixtures
remain covered by the semantic bridge tests. No Candidate was built, no Blender
user installation was updated, and the V9/R60 release line remains untouched.
