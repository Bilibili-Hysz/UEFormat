# UEModel Specifications

> **FModel pipeline profile:** `.uemodel` is the canonical model artifact for StaticMesh, SkeletalMesh and skeleton-only exports. Its material/texture resource semantics are frozen by [the unified pipeline specification](../../docs/current/UEFORMAT_BLENDER_PIPELINE_SPEC.md) and [the URI contract](../../docs/current/RELATIVE_RESOURCE_URI_CONTRACT.md). Material JSON is never an importer fallback.


Binary layout for `.uemodel` files (`Identifier = "UEMODEL"`).

---

## Data Chunks

| HeaderName | Count | Data |
|------------|-------|------|
| `LODS` | LOD count | [FStaticDataChunk](generic.md#fstaticdatachunk) per LOD (`LOD0`, `LOD1`, ...) |
| `SKELETON` | `1` | Nested skeleton [FDataChunk](generic.md#fdatachunk)s |
| `COLLISION` | convex count | [FConvexMeshCollision](#structures) x `Count` |
| `FMODEL_MATERIALS` | material-link slot count | Optional FModel material extension v1 or v2; ignored by upstream readers as an unknown top-level chunk |
| `FMODEL_TEXTURES` | `1` (identity v1/v2 singleton) or legacy embedded-resource count | Optional FModel texture extension. Identity v1/v2 stores its own `texture_count` and `binding_count` inside the payload; legacy embedded v1 uses the envelope count as its resource count. Ignored by upstream readers as an unknown top-level chunk |
| `FMODEL_SKELETON_IDENTITY` | `1` | Optional FModel skeleton identity extension v1; ignored by upstream readers as an unknown top-level chunk |

### `FMODEL_SKELETON_IDENTITY`

This optional singleton is written after all standard model chunks when the
resolved Unreal `USkeleton` is available. Its payload is, in order:

```text
uint8 Version                 // 1
FString SkeletonPath           // canonical resolved USkeleton object path
uint8 HasSkeletonGuid          // exactly 0 or 1
byte[16] SkeletonGuid          // present only when HasSkeletonGuid is 1
byte[32] BoneLayoutHash        // SHA-256 of the deterministic v1 bone layout
```

The layout hash input is the ASCII domain string `fmodel-skeleton-layout-v1\0`,
the little-endian bone count, then each ordered UTF-8 bone name length, name
bytes, and little-endian parent index. Blender matches path first, compares the
GUID only when both files provide one, and then requires an exact layout hash.
Old files without this chunk remain importable but cannot be identity-verified.
This extension does not add animation discovery, scanning, manifests, bundles,
or a new file format.

The deterministic identity fixture `tests/fixtures/fmodel_skeleton_identity.uemodel`
is generated from the current FModel writer test and has SHA-256
`f6e10f736fea2356f23917a0be186c8b7fee784a2d745b3d65a81ed636957ddc`.

## FModel Golden Fixture

### `FMODEL_MATERIALS` V2

The V2 payload begins with `uint8 Version = 2` and a slot count. Each slot
stores both `SlotIndex` and `SourceMaterialIndex` before the same material-link
fields used by V1. `SlotIndex` is the section index in the exported mesh;
`SourceMaterialIndex` is the source material index used by `MATO` overrides.
The sentinel `SourceMaterialIndex = -1` means that the slot has no source
material mapping and must not be used to infer an MATO target.

Source material indices may repeat. Repeated entries are valid only when their
material paths normalize to the same valid Unreal material identity. A missing,
malformed, or otherwise invalid Unreal material identity is an invalid source
mapping, including when it is repeated with another malformed path or with a
valid path; Blender preflight rejects the model before scene mutation. V1 files
have no source-index mapping, and V1 or otherwise unmapped material links cannot
infer an MATO override target.

`tests/fixtures/fmodel_golden.uemodel` is produced by the FModel C# test
`GoldenFixtureWriterTests.WritesDeterministicUeModelGoldenFixture`, not by a
handwritten binary encoder. It has SHA-256
`d5f0724e6d45824148f9d572122aedc2eb2ef05312fc8da58816fd38b6346722`.

The fixture contains one triangle in `LOD0` with standard `VERTICES`,
`NORMALS`, `TEXCOORDS`, `INDICES`, and two `MATERIALS` entries. Its FModel v1
extension layout is deterministic:

| Slot | Standard material | FModel parameters |
|------|-------------------|-------------------|
| `0` `Body` | `M_GoldenBody` | embedded `BaseColor` ID `0`, embedded `Normal` ID `1`, custom embedded `WetnessMask` ID `2`, scalar `Roughness=0.25`, vector `Tint=(0.1, 0.2, 0.3, 1)` |
| `1` `Overlay` | `M_GoldenOverlay` | embedded `Normal` reusing texture ID `1` |

`FMODEL_TEXTURES` stores the three IDs once. The final `FUTURE_GOLDEN` chunk
is intentionally unknown and confirms top-level forward-compatible skipping.

## FModel V2 Deployment Fixture

`tests/fixtures/fmodel_v2_deployment.uemodel` is a deterministic serialized
deployment fixture derived from the golden triangle payload. It adds a V2
`FMODEL_PBR_INTERFACES` chunk mapping `BaseColor` to role `1`, `Normal` to
role `3`, and `WetnessMask` to role `9`. `WetnessMask` retains texture packing
`7`; texture-backed role `9` has no supported producer channel contract, so
the Blender V2 importer must preserve it in Custom rather than wire it to the
Principled graph. The managed Blender 5.2 operator smoke imports this file
through `bpy.ops.uf.import_uemodel` and validates metadata, direct PBR wiring,
embedded image resolution, and the `import_fmodel_materials=False` boundary.
Its SHA-256 is `baed6227e36c33d8f177d7540ff30bba0f122753ce3dcbb2c2558d0c56d95d0a`.

### LOD

Each LOD entry inside `LODS` is an `FStaticDataChunk` named `LOD0`, `LOD1`, etc. Its `Data` is a nested `FDataChunk` stream:

| HeaderName | Count | Packed layout |
|------------|-------|---------------|
| `VERTICES` | vertex count | `FVector` x `Count` |
| `NORMALS` | vertex count | (`float` BinormalSign + `FVector` Normal) x `Count` |
| `TANGENTS` | vertex count | `FVector` x `Count` |
| `TEXCOORDS` | UV channel count | `TArray<FMeshUVFloat>` x `Count` |
| `INDICES` | index count | `uint32` x `Count` (triangle list) |
| `VERTEXCOLORS` | layer count | [FVertexColor](#structures) x `Count` |
| `MATERIALS` | section count | [FMaterial](#structures) x `Count` |
| `WEIGHTS` | influence count | [FWeight](#structures) x `Count` |
| `MORPHTARGETS` | morph count | [FMorphTarget](#structures) x `Count` |

The V10 importer validates cross-field references before creating Blender
datablocks: triangle and collision indices must address their vertex arrays,
normals/UVs/colors must be vertex-aligned when present, material section ranges
must stay inside the triangle list, weights must reference an existing bone and
vertex, and Morph deltas must reference an existing vertex. Skeleton parent
indices must be `-1` or point to an earlier bone. Invalid data fails closed.

`NORMALS` packing per vertex:

```csharp
float BinormalSign;
FVector Normal;
```

### Skeleton

`SKELETON` data is a nested `FDataChunk` stream:

| HeaderName | Count | Data |
|------------|-------|------|
| `METADATA` | `1` | `FString` skeleton path |
| `BONES` | bone count | [FBone](#structures) x `Count` |
| `SOCKETS` | socket count | [FSocket](#structures) x `Count` |
| `VIRTUALBONES` | virtual bone count | [FVirtualBone](#structures) x `Count` |

---

## Structures

```csharp
struct FVertexColor
{
    FString Name;
    TArray<FColor> Data;
}

struct FMaterial
{
    FString MaterialName;
    FString MaterialPath;
    int32 FirstIndex;
    int32 NumFaces;
}

struct FWeight
{
    uint16 Bone; // bone index
    int32 VertexIndex;
    float Weight;
}

struct FMorphTarget
{
    FString MorphName;
    TArray<FMorphData> MorphData;
}

struct FMorphData
{
    FVector PositionDelta;
    FVector TangentZDelta;
    uint32 VertexIndex;
}

struct FBone
{
    FString BoneName;
    int32 ParentIndex;
    FVector Position;
    FQuat Orientation;
}

struct FSocket
{
    FString SocketName;
    FString BoneName; // parent bone
    FVector RelativeLocation;
    FQuat RelativeRotation;
    FVector RelativeScale;
}

struct FVirtualBone
{
    FString SourceBoneName;
    FString TargetBoneName;
    FString VirtualBoneName;
}

struct FConvexMeshCollision
{
    FString Name;
    TArray<FVector> VertexData;
    TArray<int32> IndexData; // triangle list
}
```
