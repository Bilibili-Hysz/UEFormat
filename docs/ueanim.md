# UEAnim Specifications

Binary layout for `.ueanim` files (`Identifier = "UEANIM"`).

---

## Data Chunks

| HeaderName | Count | Data |
|------------|-------|------|
| `METADATA` | `1` | [FAnimMetadata](#structures) |
| `TRACKS` | track count | [FTrack](#structures) x `Count` |
| `CURVES` | curve count | [FCurve](#structures) x `Count` |

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