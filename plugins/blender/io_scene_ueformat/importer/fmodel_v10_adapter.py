from __future__ import annotations

"""Bridge validated V10 FModel snapshots into the material runtime.

The wire decoder deliberately exposes immutable, Blender-free snapshots.  This
module is the only boundary that turns them into the mutable runtime records
needed by the existing resource resolver and node builder.  Keeping that
conversion explicit prevents V10 parsing from acquiring filesystem or ``bpy``
side effects.
"""

from .extensions.fmodel import (
    FModelIdentityRecord,
    FModelMaterialLinksSnapshot,
    FModelTextureSnapshot,
    FModelV10ExtensionSnapshot,
    FModelV10Attribute,
    decode_v10_identity_chunk,
    decode_v10_material_links,
    decode_v10_pbr,
    decode_v10_textures,
)
from .runtime_materials import (
    FModelMaterialLinks,
    FModelMaterialParameter,
    FModelMaterialSlot,
    FModelPBRInterface,
)
from .runtime_identity import (
    FModelSkeletonIdentity,
    FModelTargetPoseIdentity,
    FModelTargetSkeletonIdentity,
)
from .runtime_resources import TextureResource


def decode_v10_snapshot_semantics(
    snapshot: FModelV10ExtensionSnapshot,
    *,
    source_kind: str | None = None,
) -> tuple[
    FModelIdentityRecord | None,
    FModelMaterialLinksSnapshot | None,
    FModelTextureSnapshot | None,
    tuple[object, ...],
]:
    """Decode the complete V10 extension set in one deterministic pass."""

    identity = None
    materials = None
    textures = None
    pbr: tuple[object, ...] = ()
    identity_payload = snapshot.get(
        FModelV10Attribute.SKELETON_IDENTITY
        if source_kind is None
        else FModelV10Attribute.TARGET_SKELETON,
    )
    if identity_payload is not None:
        identity = decode_v10_identity_chunk(identity_payload, source_kind=source_kind)
    materials_payload = snapshot.get(FModelV10Attribute.MATERIALS)
    if materials_payload is not None:
        materials = decode_v10_material_links(materials_payload)
    textures_payload = snapshot.get(FModelV10Attribute.TEXTURES)
    if textures_payload is not None:
        textures = decode_v10_textures(textures_payload)
    pbr_payload = snapshot.get(FModelV10Attribute.PBR_INTERFACES)
    if pbr_payload is not None:
        pbr = decode_v10_pbr(pbr_payload)
    return identity, materials, textures, pbr


def to_runtime_identity(
    identity: FModelIdentityRecord | None,
    *,
    source_kind: str | None = None,
) -> FModelSkeletonIdentity | FModelTargetSkeletonIdentity | FModelTargetPoseIdentity | None:
    """Convert a wire-only identity record into the shared runtime type.

    The V10 decoder intentionally has no Blender or binding policy.  The
    runtime identity types are used only after a caller has decided to apply
    FModel provenance to an imported armature, animation or pose.
    """

    if identity is None:
        return None
    if identity.source_path is None:
        if source_kind is not None:
            raise ValueError("FModel target identity is missing source provenance")
        return FModelSkeletonIdentity(
            identity.version,
            identity.skeleton_path,
            identity.skeleton_guid,
            identity.bone_layout_hash,
        )
    if source_kind == "pose":
        return FModelTargetPoseIdentity(
            identity.version,
            identity.skeleton_path,
            identity.skeleton_guid,
            identity.bone_layout_hash,
            identity.source_path,
        )
    if source_kind not in ("animation", None):
        raise ValueError(f"Unsupported FModel identity source kind: {source_kind}")
    return FModelTargetSkeletonIdentity(
        identity.version,
        identity.skeleton_path,
        identity.skeleton_guid,
        identity.bone_layout_hash,
        identity.source_path,
    )


def to_runtime_material_links(
    materials: FModelMaterialLinksSnapshot,
    textures: FModelTextureSnapshot | None,
    pbr: tuple[object, ...] = (),
) -> tuple[FModelMaterialLinks, dict[int, TextureResource]]:
    """Convert immutable V10 snapshots to the shared node-builder records."""

    slots_by_index = {slot.slot_index: slot for slot in materials.slots}
    if len(slots_by_index) != len(materials.slots):
        raise ValueError("V10 material links contain duplicate slot indices")
    for interface in pbr:
        slot = slots_by_index.get(interface.slot_index)
        if (
            slot is None
            or interface.parameter_index < 0
            or interface.parameter_index >= len(slot.parameters)
        ):
            raise ValueError("V10 PBR interface references an unknown material parameter")

    links = FModelMaterialLinks(
        slots=[
            FModelMaterialSlot(
                slot_index=slot.slot_index,
                slot_name=slot.slot_name,
                unreal_material_path=slot.unreal_material_path,
                material_json_uri=slot.material_json_uri,
                source_material_index=slot.source_material_index,
                parameters=[
                    FModelMaterialParameter(
                        name=parameter.name,
                        kind=parameter.kind,
                        semantic=parameter.semantic,
                        value=(
                            TextureResource.parameter(
                                id=parameter.value.texture_id,
                                unreal_object_path=parameter.value.unreal_object_path,
                                location_mode=parameter.value.location_mode,
                                uri=parameter.value.uri,
                                color_space=parameter.value.color_space,
                                packing=parameter.value.packing,
                                uv_set=parameter.value.uv_set,
                            )
                            if parameter.kind == 1
                            else parameter.value
                        ),
                    )
                    for parameter in slot.parameters
                ],
            )
            for slot in materials.slots
        ],
        source_material_mapping_present=materials.source_material_mapping_present,
        pbr_interfaces=[
            FModelPBRInterface(item.slot_index, item.parameter_index, item.role)
            for item in pbr
        ],
        pbr_interfaces_present=bool(pbr),
    )

    embedded_resources: dict[int, TextureResource] = {}
    if textures is not None:
        embedded_identity_records = [
            record.texture_id
            for record in textures.textures
            if record.is_embedded
        ]
        if embedded_identity_records:
            raise ValueError(
                "V10 FMODEL_TEXTURES identity records marked embedded have no payload bytes: "
                + ", ".join(repr(item) for item in embedded_identity_records)
            )
        if len({record.texture_id for record in textures.embedded}) != len(textures.embedded):
            raise ValueError("V10 embedded textures contain duplicate ids")
        embedded_resources = {
            record.texture_id: TextureResource.embedded(
                id=record.texture_id,
                unreal_object_path=record.unreal_object_path,
                mime=record.mime,
                data=record.data,
            )
            for record in textures.embedded
        }
        _apply_texture_bindings(links, textures, pbr)
    return links, embedded_resources


def _apply_texture_bindings(
    links: FModelMaterialLinks,
    textures: FModelTextureSnapshot,
    pbr: tuple[object, ...],
) -> None:
    """Project the identity texture table into missing material parameters."""

    by_id = {record.texture_id: record for record in textures.textures}
    slots: dict[str, list[FModelMaterialSlot]] = {}
    for slot in links.slots:
        slots.setdefault(slot.slot_name, []).append(slot)
    semantic_by_kind = {
        0: ("FMODEL_TEXTURES.BaseColor", "base_color", 0),
        1: ("FMODEL_TEXTURES.Normal", "normal", 0),
        2: ("FMODEL_TEXTURES.Orm", "orm", 1),
    }
    for binding in textures.bindings:
        matched_slots = slots.get(binding.material_id, [])
        record = by_id.get(binding.texture_id)
        if not matched_slots or record is None:
            raise ValueError("V10 texture binding references an unknown material or texture")
        if len({slot.unreal_material_path for slot in matched_slots}) > 1:
            raise ValueError(
                "V10 texture binding material_id is ambiguous across Unreal material identities: "
                f"{binding.material_id}"
            )
        name, semantic, packing = semantic_by_kind[int(binding.map_kind)]
        for slot in matched_slots:
            matching = [p for p in slot.parameters if p.semantic.casefold() == semantic]
            if matching:
                if len(matching) == 1 and matching[0].kind == 1:
                    texture = matching[0].texture
                    if (
                        texture.stable_id == record.texture_id
                        and texture.uri == record.logical_resource_uri
                        and texture.expected_byte_length == record.byte_length
                        and texture.expected_content_hash == record.content_hash
                    ):
                        continue
                raise ValueError("V10 texture binding conflicts with a material parameter")
            slot.parameters.append(
                FModelMaterialParameter(
                    name=name,
                    kind=1,
                    semantic=semantic,
                    value=TextureResource.identity(
                        id=0,
                        unreal_object_path="",
                        location_mode=record.location_mode,
                        uri=record.logical_resource_uri,
                        color_space=1 if binding.is_srgb else 0,
                        packing=packing,
                        uv_set=0,
                        stable_id=record.texture_id,
                        expected_byte_length=record.byte_length,
                        expected_content_hash=record.content_hash,
                        identity_embedded=record.is_embedded,
                    ),
                ),
            )
