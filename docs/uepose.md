# UEPose Specifications

> **FModel pipeline profile:** `.uepose` is emitted for `UPoseAsset` through Save Animation and bulk animation export. `FMODEL_TARGET_SKELETON` stores Unreal object identity and provenance, not a filesystem resource URI. See [the unified pipeline specification](../../docs/current/UEFORMAT_BLENDER_PIPELINE_SPEC.md).


Binary layout for `.uepose` files (`Identifier = "UEPOSE"`).

---

## Data Chunks

| HeaderName | Count | Data |
|------------|-------|------|
| `POSES` | pose count | [FPoseData](#structures) x `Count` |
| `CURVES` | curve name count | `FString` x `Count` |
| `FMODEL_TARGET_SKELETON` | `1` | Optional FModel target-skeleton identity extension v1; ignored by upstream readers as an unknown top-level chunk |

When Blender's strict FModel skeleton option is enabled **and** this identity chunk is present, the file is treated as the frozen FModel profile. The known chunks must be `POSES`, `CURVES`, then `FMODEL_TARGET_SKELETON`; missing required chunks, duplicate known chunks, order drift and trailing chunk bytes fail closed. A file without the identity chunk remains a generic upstream UEPOSE import.

### `FMODEL_TARGET_SKELETON`

This optional singleton is appended after `POSES` and `CURVES` when FModel
resolves the Pose Asset's target `USkeleton`. Its payload is, in order:

```text
uint8 Version                 // 1
FString SourcePosePath         // canonical exported Pose Asset object path
FString SkeletonPath            // canonical resolved USkeleton object path
uint8 HasSkeletonGuid           // exactly 0 or 1
byte[16] SkeletonGuid           // present only when HasSkeletonGuid is 1
byte[32] BoneLayoutHash         // same deterministic hash as the model chunk
```

Blender retains this record on the imported pose result and, when strict FModel
skeleton checking is enabled, compares it with the selected armature before
mutating pose or shape-key data. Legacy `.uepose` files without this chunk remain
importable. This extension associates a standalone Pose Asset with its target
skeleton; it does not add UEScene skeletal graph support or pose discovery.

The deterministic identity fixture `tests/fixtures/fmodel_target_skeleton.uepose`
has SHA-256
`b1a77dc3fb6b88682a35c5549b1cc50946c942a2e062a2b9c860bed125e732cc`.

---

## Structures

```csharp
struct FPoseData
{
    FString PoseName;
    TArray<FPoseKey> Keys;
    TArray<FPoseCurveInfluence> Curves;
}

struct FPoseKey
{
    FString BoneName;
    FVector Location;
    FQuat Rotation;
    FVector Scale;
}

struct FPoseCurveInfluence
{
    int32 CurveIndex;
    float Influence;
}
```

Every `CurveIndex` must address the `CURVES` name table. The V10 importer
rejects an out-of-range index before it mutates the selected armature or mesh.
