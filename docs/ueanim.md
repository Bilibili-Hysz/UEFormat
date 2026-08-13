# UEAnim Specifications

> **FModel pipeline profile:** `.ueanim` is emitted for `UAnimSequence`, `UAnimMontage` and `UAnimComposite` when the UEFormat profile is selected. `FMODEL_TARGET_SKELETON` stores Unreal object identity and provenance, not a filesystem resource URI. See [the unified pipeline specification](../../docs/current/UEFORMAT_BLENDER_PIPELINE_SPEC.md).


Binary layout for `.ueanim` files (`Identifier = "UEANIM"`).

---

## Data Chunks

| HeaderName | Count | Data |
|------------|-------|------|
| `METADATA` | `1` | [FAnimMetadata](#structures) |
| `TRACKS` | track count | [FTrack](#structures) x `Count` |
| `CURVES` | curve count | [FCurve](#structures) x `Count` |
| `FMODEL_TARGET_SKELETON` | `1` | Optional FModel target-skeleton identity extension v1; ignored by upstream readers as an unknown top-level chunk |

When Blender's strict FModel skeleton option is enabled **and** this identity chunk is present, the file is treated as the frozen FModel profile. The known chunks must be `METADATA`, `TRACKS`, optional `CURVES`, then `FMODEL_TARGET_SKELETON`; missing required chunks, duplicate known chunks, order drift and trailing chunk bytes fail closed. A file without the identity chunk remains a generic upstream UEANIM import.

### `FMODEL_TARGET_SKELETON`

This optional singleton is written after all standard animation chunks when a
resolved target `USkeleton` is available. Its payload is, in order:

```text
uint8 Version                 // 1
FString SourceAnimationPath    // canonical exported animation object path
FString SkeletonPath           // canonical resolved USkeleton object path
uint8 HasSkeletonGuid          // exactly 0 or 1
byte[16] SkeletonGuid          // present only when HasSkeletonGuid is 1
byte[32] BoneLayoutHash        // same deterministic hash as the model chunk
```

For FModel-provenanced imports, Blender compares the target skeleton path,
optionally compares GUID when both sides provide it, and requires an exact
layout hash before creating or assigning an Action. If either side lacks
identity, import remains available with one unverified-identity diagnostic.
Manual/legacy animation import therefore remains supported. This extension
does not add animation discovery, scanning, manifests, bundles, or a new file
format.

The deterministic identity fixture `tests/fixtures/fmodel_target_skeleton.ueanim`
is generated from the current FModel writer test and has SHA-256
`4af1eaca0bf69aae85f788bd33e2d55d351f70d94527148c02e6f8352a244cf2`.

---

## Structures

```csharp
struct FAnimMetadata
{
    int32 NumFrames;
    float FramesPerSecond;
    FString RefPosePath;
    uint8 AdditiveAnimType; // EAdditiveAnimationType
    uint8 RefPoseType; // EAdditiveBasePoseType
    int32 RefFrameIndex;
}

struct FTrack
{
    FString BoneName;
    TArray<FVectorKey> PositionKeys;
    TArray<FQuatKey> RotationKeys;
    TArray<FVectorKey> ScaleKeys;
}

struct FCurve
{
    FString CurveName;
    TArray<FFloatKey> Keys;
}

struct FVectorKey
{
    int32 Frame;
    FVector Value;
}

struct FQuatKey
{
    int32 Frame;
    FQuat Value;
}

struct FFloatKey
{
    int32 Frame;
    float Value;
}
```

`Frame` in every bone and curve key is a source animation tick. The
`FramesPerSecond` metadata field is the number of source ticks per wall-clock
second; it is not an instruction to overwrite the Blender scene frame rate.
The Blender importer preserves duration by mapping each key with
`blender_frame = source_tick * (scene_fps / scene_fps_base) / FramesPerSecond`.
Bone and morph keys use the same mapping. Imported Actions store this policy,
the source rate, and the effective Blender rate in `fmodel_animation_time`.

### `EAdditiveAnimationType`

| Value | Name |
|------:|------|
| 0 | `AAT_None` |
| 1 | `AAT_LocalSpaceBase` |
| 2 | `AAT_RotationOffsetMeshSpace` |

### `EAdditiveBasePoseType`

| Value | Name |
|------:|------|
| 0 | `ABPT_None` |
| 1 | `ABPT_RefPose` |
| 2 | `ABPT_AnimScaled` |
| 3 | `ABPT_AnimFrame` |
| 4 | `ABPT_LocalAnimFrame` |
