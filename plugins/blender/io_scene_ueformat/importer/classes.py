from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt
from mathutils import Quaternion, Vector

from ..logging import Log
from .protocol import (
    ANIM_IDENTIFIER,
    MAGIC,
    MODEL_IDENTIFIER,
    POSE_IDENTIFIER,
    EUEFormatVersion,
    MAX_FMODEL_IDENTITY_PATH_BYTES,
    MAX_FMODEL_MATERIAL_TEXTURE_BINDINGS,
    MAX_FMODEL_PARAMETERS,
    MAX_FMODEL_PBR_INTERFACES,
    MAX_FMODEL_SLOTS,
    MAX_FMODEL_TEXTURE_ID_BYTES,
    MAX_FMODEL_TEXTURES,
    _FModelTextureLegacyFormatMismatchError,
    _read_non_negative_count,
    _read_strict_identity_fstring,
    _read_strict_legacy_fstring,
    _read_strict_utf8_string,
    _validate_fmodel_identity_chunk_count,
    _validate_fmodel_material_id,
    _validate_logical_resource_uri,
    _normalize_logical_unreal_path,
    _validate_logical_unreal_path,
    _validate_max_count,
    _validate_top_level_count,
)
from .identity import (
    FModelSkeletonIdentity,
    FModelTargetPoseIdentity,
    FModelTargetSkeletonIdentity,
    compute_skeleton_layout_hash,
)

from .materials import (
    FModelMaterialLinks,
    FModelMaterialParameter,
    FModelMaterialSlot,
    FModelMaterialTextureBinding,
    FModelMaterialTextureRecord,
    FModelMaterialTextures,
    FModelPBRInterface,
    FModelTextureMapKind,
)
from .resources import TextureResource, TextureResourceKind

from .topology import (
    UEMODELLodTopologySummary,
    UEMODELSkeletonTopologySummary,
    UEMODELTopologySummary,
    _validate_uemodel_lods,
    _validate_uemodel_skeleton,
    validate_uemodel_topology,
)


if TYPE_CHECKING:
    from io_scene_ueformat.importer.reader import FArchiveReader


@dataclass(slots=True)
class UEModel:
    lods: list[UEModelLOD] = field(default_factory=list)
    collisions: list[ConvexCollision] = field(default_factory=list)
    skeleton: UEModelSkeleton | None = None
    material_links: FModelMaterialLinks = field(default_factory=lambda: FModelMaterialLinks())
    textures: list[TextureResource] = field(default_factory=list)
    material_textures: FModelMaterialTextures | None = None
    skeleton_identity: FModelSkeletonIdentity | None = None
    skeleton_identity_derived: bool = False
    # physics = None  # noqa: ERA001

    @classmethod
    def from_archive(
        cls,
        ar: FArchiveReader
    ) -> UEModel:
        data = cls()
        recognized_chunks: set[str] = set()

        while not ar.eof():
            section_name = ar.read_fstring()
            array_size = ar.read_int()
            byte_size = ar.read_int()
            if array_size < 0:
                raise ValueError(f"Invalid top-level array_size: {array_size}")
            extension_name = {
                "FMODEL_MATERIALS": "FModel materials",
                "FMODEL_TEXTURES": "FModel textures",
                "FMODEL_PBR_INTERFACES": "FModel PBR interfaces",
                "FMODEL_SKELETON_IDENTITY": "FModel skeleton identity",
            }.get(section_name, f"Top-level chunk {section_name}")
            if byte_size < 0:
                raise ValueError(f"{extension_name} chunk declared negative byte_size")
            if byte_size > ar.remaining():
                raise ValueError(f"{extension_name} chunk declared byte_size exceeds available data")

            if section_name in {
                "LODS",
                "SKELETON",
                "COLLISION",
                "FMODEL_MATERIALS",
                "FMODEL_TEXTURES",
                "FMODEL_PBR_INTERFACES",
                "FMODEL_SKELETON_IDENTITY",
            }:
                if section_name in recognized_chunks:
                    raise ValueError(f"Invalid duplicate top-level chunk: {section_name}")
                recognized_chunks.add(section_name)

            chunk = ar.chunk(byte_size)

            match section_name:
                case "LODS":
                    if array_size > chunk.remaining() // 8:
                        raise ValueError("Top-level LODS array_size cannot fit minimum records")
                    data.lods = chunk.read_array(array_size, UEModelLOD.from_archive)
                case "SKELETON":
                    if array_size != 1:
                        raise ValueError("SKELETON chunk array_size must be 1")
                    data.skeleton = UEModelSkeleton.from_archive(chunk)
                case "COLLISION":
                    if array_size > chunk.remaining() // 12:
                        raise ValueError("Top-level COLLISION array_size cannot fit minimum records")
                    data.collisions = chunk.read_array(
                        array_size,
                        lambda ar: ConvexCollision.from_archive(ar),
                    )
                case "FMODEL_MATERIALS":
                    data.material_links = FModelMaterialLinks.from_archive(chunk, array_size)
                case "FMODEL_TEXTURES":
                    data.textures, data.material_textures = TextureResource.read_all(chunk, array_size)
                case "FMODEL_PBR_INTERFACES":
                    data.material_links.pbr_interfaces = FModelPBRInterface.read_all(chunk, array_size)
                    data.material_links.pbr_interfaces_present = True
                case "FMODEL_SKELETON_IDENTITY":
                    _validate_fmodel_identity_chunk_count(array_size, section_name)
                    data.skeleton_identity = FModelSkeletonIdentity.from_archive(chunk)
                case _:
                    Log.warn(f"Unknown Model Data: {section_name}")
                    chunk.read_to_end()
            if not chunk.eof():
                raise ValueError(f"Trailing bytes in {extension_name} chunk")
        if (
            data.skeleton_identity is None
            and data.skeleton is not None
            and data.skeleton.metadata is not None
            and data.skeleton.bones
            and data.skeleton.metadata.skeleton_path
        ):
            # The official standard SKELETON chunk already carries the logical
            # skeleton path and ordered bone layout. Derive the same V1 identity
            # tuple when the optional additive chunk is absent so a skeletal
            # UEMODEL can still be the strict target of R38 UEANIM/UEPOSE files.
            # GetPathName() may omit the leading slash; root it exactly as the
            # active FModel target-identity producer does.
            skeleton_path = _normalize_logical_unreal_path(
                data.skeleton.metadata.skeleton_path,
                "UEMODEL standard skeleton metadata path",
            )
            data.skeleton_identity = FModelSkeletonIdentity(
                1,
                skeleton_path,
                None,
                compute_skeleton_layout_hash(data.skeleton.bones),
            )
            data.skeleton_identity_derived = True
        slots_by_ordinal: dict[int, FModelMaterialSlot] = {}
        for slot in data.material_links.slots:
            if slot.slot_index < 0:
                raise ValueError(f"Invalid FModel material slot_index: {slot.slot_index}")
            if slot.slot_index in slots_by_ordinal:
                raise ValueError(f"Invalid FModel material duplicate slot_index: {slot.slot_index}")
            slots_by_ordinal[slot.slot_index] = slot
        if data.material_textures is not None and data.material_links.slots:
            from .fmodel import apply_material_texture_bindings

            apply_material_texture_bindings(data.material_links, data.material_textures)

        singleton_roles: set[tuple[int, int]] = set()
        for interface in data.material_links.pbr_interfaces:
            slot = slots_by_ordinal.get(interface.slot_index)
            if slot is None:
                msg = f"Invalid FModel PBR interface slot_index: {interface.slot_index}"
                raise ValueError(msg)
            parameters = slot.parameters
            if interface.parameter_index >= len(parameters):
                msg = f"Invalid FModel PBR interface parameter_index: {interface.parameter_index}"
                raise ValueError(msg)
            role_key = (interface.slot_index, interface.role)
            if role_key in singleton_roles:
                msg = (
                    "Invalid FModel PBR interface duplicate role "
                    f"for slot_index {interface.slot_index}: {interface.role}"
                )
                raise ValueError(msg)
            singleton_roles.add(role_key)
        return data

    def topology_summary(self) -> UEMODELTopologySummary:
        """Validate standard UEMODEL topology after binary chunk decoding."""
        return validate_uemodel_topology(self.lods, self.skeleton, self.skeleton_identity)

    @classmethod
    def from_archive_legacy(self, ar: FArchiveReader) -> UEModel:
        data = UEModel()
        data.skeleton = UEModelSkeleton()
        lod = UEModelLOD(name="LOD0")

        while not ar.eof():
            header_name = ar.read_fstring()
            array_size = ar.read_int()
            byte_size = ar.read_int()

            pos = ar.data.tell()
            if header_name == "VERTICES":
                flattened = ar.read_float_vector(array_size * 3)
                lod.vertices = (np.array(flattened) * ar.metadata["scale"]).reshape(array_size, 3)
            elif header_name == "INDICES":
                lod.indices = np.array(ar.read_int_vector(array_size), dtype=np.int32).reshape(array_size // 3, 3)
            elif header_name == "NORMALS":
                if ar.file_version >= EUEFormatVersion.SerializeBinormalSign:
                    flattened = np.array(
                        ar.read_float_vector(array_size * 4),
                    )  # W XYZ #  # noqa: TD002, FIX002, TD003
                    lod.normals = flattened.reshape(-1, 4)[:, 1:]
                else:
                    flattened = np.array(ar.read_float_vector(array_size * 3)).reshape(array_size, 3)
                    lod.normals = flattened
            elif header_name == "TANGENTS":
                # FModel serializes each FVector tangent as three float32 values.
                ar.skip(array_size * 3 * 4)
                # flattened = np.array(ar.read_float_vector(array_size * 3)).reshape(array_size, 3)  # noqa: ERA001
            elif header_name == "VERTEXCOLORS":
                if ar.file_version >= EUEFormatVersion.AddMultipleVertexColors:
                    lod.colors = [VertexColor.from_archive(ar) for _ in range(array_size)]
                else:
                    lod.colors = [
                        VertexColor(
                            "COL0",
                            (np.array(ar.read_byte_vector(array_size * 4)).reshape(array_size, 4) / 255).astype(
                                np.float32,
                            ),
                        ),
                    ]
            elif header_name == "TEXCOORDS":
                lod.uvs = []
                for _ in range(array_size):
                    count = ar.read_int()
                    lod.uvs.append(np.array(ar.read_float_vector(count * 2)).reshape(count, 2))
            elif header_name == "MATERIALS":
                lod.materials = [
                    Material.from_archive(ar, source_slot_index)
                    for source_slot_index in range(array_size)
                ]
            elif header_name == "WEIGHTS":
                lod.weights = ar.read_array(array_size, lambda ar: Weight.from_archive(ar))
            elif header_name == "MORPHTARGETS":
                lod.morphs = ar.read_array(
                    array_size,
                    lambda ar: MorphTarget.from_archive(ar),
                )
            elif header_name == "BONES":
                data.skeleton.bones = ar.read_array(
                    array_size,
                    lambda ar: Bone.from_archive(ar),
                )
            elif header_name == "SOCKETS":
                data.skeleton.sockets = ar.read_array(
                    array_size,
                    lambda ar: Socket.from_archive(ar),
                )
            elif header_name == "COLLISION":
                data.collisions = ar.read_array(
                    array_size,
                    lambda ar: ConvexCollision.from_archive(ar),
                )
            else:
                Log.warn(f"Unknown Data: {header_name}")
                ar.skip(byte_size)
            ar.data.seek(pos + byte_size, 0)

        data.lods.append(lod)

        return data


@dataclass(slots=True)
class UEModelLOD:
    name: str
    vertices: npt.NDArray[np.floating] = field(default_factory=lambda: np.zeros(0))
    indices: npt.NDArray[np.int32] = field(default_factory=lambda: np.zeros(0, dtype=np.int32))
    normals: npt.NDArray[np.floating] = field(default_factory=lambda: np.zeros(0))
    tangents: list = field(default_factory=list)
    colors: list[VertexColor] = field(default_factory=list)
    uvs: list[npt.NDArray[Any]] = field(default_factory=list)
    materials: list[Material] = field(default_factory=list)
    morphs: list[MorphTarget] = field(default_factory=list)
    weights: list[Weight] = field(default_factory=list)
    topology_children: set[str] = field(default_factory=set)

    @classmethod
    def from_archive(
        cls,
        ar: FArchiveReader
    ) -> UEModelLOD:
        data = cls(name=ar.read_fstring())
        seen_children: set[str] = set()
        known_children = {
            "VERTICES", "INDICES", "NORMALS", "TANGENTS", "VERTEXCOLORS",
            "TEXCOORDS", "MATERIALS", "WEIGHTS", "MORPHTARGETS",
        }

        lod_size = ar.read_int()
        ar = ar.chunk(lod_size)

        while not ar.eof():
            header_name = ar.read_fstring()
            array_size = ar.read_int()
            byte_size = ar.read_int()
            if array_size < 0:
                raise ValueError(f"Invalid negative LOD child array_size: {array_size}")
            if byte_size < 0:
                raise ValueError(f"LOD child {header_name} declared negative byte_size")
            if byte_size > ar.remaining():
                raise ValueError(f"LOD child {header_name} declared byte_size exceeds available data")

            child = ar.chunk(byte_size)

            if header_name in known_children:
                if header_name in seen_children:
                    raise ValueError(f"Invalid duplicate LOD child: {header_name}")
                seen_children.add(header_name)

            if header_name == "VERTICES":
                data.topology_children.add(header_name)
                flattened = child.read_float_vector(array_size * 3)
                data.vertices = (np.array(flattened) * child.metadata["scale"]).reshape(array_size, 3)
                
                if child.file_version >= EUEFormatVersion.PreserveOriginalTransforms:
                    data.vertices *= (1, -1, 1)
                    
            elif header_name == "INDICES":
                data.topology_children.add(header_name)
                data.indices = np.array(
                    child.read_int_vector(array_size),
                    dtype=np.int32,
                ).reshape(array_size // 3, 3)
            elif header_name == "NORMALS":
                # W XYZ  # TODO: change to XYZ W 
                flattened = np.array(child.read_float_vector(array_size * 4))
                data.normals = flattened.reshape(-1, 4)[:, 1:]

                if child.file_version >= EUEFormatVersion.PreserveOriginalTransforms:
                    data.normals *= (1, -1, 1)
                    
            elif header_name == "TANGENTS":
                # FModel serializes each FVector tangent as three float32 values.
                child.skip(array_size * 3 * 4)
            elif header_name == "VERTEXCOLORS":
                data.colors = [VertexColor.from_archive(child) for _ in range(array_size)]
            elif header_name == "TEXCOORDS":
                data.uvs = []
                for _ in range(array_size):
                    count = child.read_int()
                    uvs = np.array(child.read_float_vector(count * 2)).reshape(count, 2)

                    if child.file_version >= EUEFormatVersion.PreserveOriginalTransforms:
                        uvs *= (1, -1)
                        uvs += (0, 1)
                
                    data.uvs.append(uvs)
            elif header_name == "MATERIALS":
                data.topology_children.add(header_name)
                data.materials = [
                    Material.from_archive(child, source_slot_index)
                    for source_slot_index in range(array_size)
                ]
            elif header_name == "WEIGHTS":
                data.weights = child.read_array(
                    array_size,
                    lambda ar: Weight.from_archive(ar),
                )
            elif header_name == "MORPHTARGETS":
                data.morphs = child.read_array(
                    array_size,
                    lambda ar: MorphTarget.from_archive(ar),
                )
            else:
                Log.warn(f"Unknown LOD Data: {header_name}")
                child.read_to_end()

            if not child.eof():
                raise ValueError(f"Trailing bytes in LOD child {header_name}")
        return data


@dataclass(slots=True)
class UEModelSkeleton:
    metadata: UEModelSkeletonMetadata = None
    bones: list[Bone] = field(default_factory=list)
    sockets: list[Socket] = field(default_factory=list)
    virtual_bones: list[VirtualBone] = field(default_factory=list)

    @classmethod
    def from_archive(cls, ar: FArchiveReader) -> UEModelSkeleton:
        data = cls()
        seen_children: set[str] = set()
        known_children = {"METADATA", "BONES", "SOCKETS", "VIRTUALBONES"}
        
        while not ar.eof():
            section_name = ar.read_fstring()
            array_size = ar.read_int()
            byte_size = ar.read_int()
            if array_size < 0:
                raise ValueError(f"Invalid negative skeleton child array_size: {array_size}")
            if byte_size < 0:
                raise ValueError(f"Skeleton child {section_name} declared negative byte_size")
            if byte_size > ar.remaining():
                raise ValueError(f"Skeleton child {section_name} declared byte_size exceeds available data")

            child = ar.chunk(byte_size)
            if section_name in known_children:
                if section_name in seen_children:
                    raise ValueError(f"Invalid duplicate skeleton child: {section_name}")
                seen_children.add(section_name)
            match section_name:
                case "METADATA":
                    if array_size != 1:
                        raise ValueError("METADATA child array_size must be 1")
                    data.metadata = UEModelSkeletonMetadata.from_archive(child)
                case "BONES":
                    if array_size <= 0:
                        raise ValueError("BONES child array_size must be greater than 0")
                    data.bones = child.read_array(
                        array_size,
                        lambda ar: Bone.from_archive(ar),
                    )
                case "SOCKETS":
                    data.sockets = child.read_array(
                        array_size,
                        lambda ar: Socket.from_archive(ar),
                    )
                case "VIRTUALBONES":
                    data.virtual_bones = child.read_array(
                        array_size,
                        lambda ar: VirtualBone.from_archive(ar),
                    )
                case _:
                    Log.warn(f"Unknown Skeleton Data: {section_name}")
                    child.read_to_end()
            if not child.eof():
                raise ValueError(f"Trailing bytes in skeleton child {section_name}")

        if "METADATA" not in seen_children:
            raise ValueError("SKELETON requires exactly one METADATA child")
        if "BONES" not in seen_children:
            raise ValueError("SKELETON requires BONES child")
        return data


@dataclass(slots=True)
class UEModelSkeletonMetadata:
    skeleton_path: str
    
    @classmethod
    def from_archive(cls, ar: FArchiveReader) -> UEModelSkeletonMetadata:
        return cls(
            skeleton_path=_normalize_logical_unreal_path(
                ar.read_fstring(),
                "UEMODEL standard skeleton metadata path",
            ),
        )

@dataclass(slots=True)
class ConvexCollision:
    name: str
    vertices: npt.NDArray[np.floating[Any]]
    indices: npt.NDArray[np.int32]

    @classmethod
    def from_archive(cls, ar: FArchiveReader) -> ConvexCollision:
        name = ar.read_fstring()

        vertices_count = ar.read_int()
        vertices_flattened = ar.read_float_vector(vertices_count * 3)
        vertices = (np.array(vertices_flattened) * ar.metadata["scale"]).reshape(
            vertices_count,
            3,
        )

        if ar.file_version >= EUEFormatVersion.PreserveOriginalTransforms:
            vertices *= (1, -1, 1)

        indices_count = ar.read_int()
        indices = np.array(
            ar.read_int_vector(indices_count),
            dtype=np.int32,
        ).reshape(indices_count // 3, 3)

        return cls(name=name, vertices=vertices, indices=indices)


@dataclass(slots=True)
class VertexColor:
    name: str
    data: npt.NDArray[np.float32]

    @classmethod
    def from_archive(cls, ar: FArchiveReader) -> VertexColor:
        name = ar.read_fstring()
        count = ar.read_int()
        data = (np.array(ar.read_byte_vector(count * 4)).reshape(count, 4) / 255).astype(np.float32)

        return cls(name, data)


@dataclass(slots=True)
class Material:
    material_name: str
    material_path: str
    first_index: int
    num_faces: int
    source_slot_index: int | None = None

    @classmethod
    def from_archive(cls, ar: FArchiveReader, source_slot_index: int) -> Material:
        return cls(
            material_name=ar.read_fstring(),
            material_path=ar.read_fstring() if ar.file_version >= EUEFormatVersion.SerializeMaterialPath else "",
            first_index=ar.read_int(),
            num_faces=ar.read_int(),
            source_slot_index=source_slot_index,
        )


@dataclass(slots=True)
class Bone:
    name: str
    parent_index: int
    position: list[float]
    rotation: tuple[float, float, float, float]

    @classmethod
    def from_archive(cls, ar: FArchiveReader) -> Bone:
        return cls(
            name=ar.read_fstring(),
            parent_index=ar.read_int(),
            position=ar.read_float_vector(3) 
                     * ar.metadata["scale"]
                     * ((1, -1, 1) if ar.file_version >= EUEFormatVersion.PreserveOriginalTransforms else (1, 1, 1)),
            rotation=ar.read_float_vector(4)
                     * ((1, -1, 1, -1) if ar.file_version >= EUEFormatVersion.PreserveOriginalTransforms else (1, 1, 1, 1)),
        )


@dataclass(slots=True)
class Weight:
    bone_index: int
    vertex_index: int
    weight: float

    @classmethod
    def from_archive(cls, ar: FArchiveReader) -> Weight:
        return cls(
            bone_index=ar.read_ushort(),
            vertex_index=ar.read_int(),
            weight=ar.read_float(),
        )


@dataclass(slots=True)
class MorphTarget:
    name: str
    deltas: list[MorphTargetData]

    @classmethod
    def from_archive(cls, ar: FArchiveReader) -> MorphTarget:
        return cls(
            name=ar.read_fstring(),
            deltas=ar.read_serialized_array(
                lambda ar: MorphTargetData.from_archive(ar),
            ),
        )


@dataclass(slots=True)
class MorphTargetData:
    position: list[float]
    normals: tuple[float, float, float]
    vertex_index: int

    @classmethod
    def from_archive(cls, ar: FArchiveReader) -> MorphTargetData:
        return cls(
            position=ar.read_float_vector(3)
                      * ar.metadata["scale"]
                      * ((1, -1, 1) if ar.file_version >= EUEFormatVersion.PreserveOriginalTransforms else (1, 1, 1)),
            normals=ar.read_float_vector(3)
                    * ((1, -1, 1) if ar.file_version >= EUEFormatVersion.PreserveOriginalTransforms else (1, 1, 1)),
            vertex_index=ar.read_int(),
        )


@dataclass(slots=True)
class Socket:
    name: str
    parent_name: str
    position: list[float]
    rotation: tuple[float, float, float, float]
    scale: tuple[float, float, float]

    @classmethod
    def from_archive(cls, ar: FArchiveReader) -> Socket:
        return cls(
            name=ar.read_fstring(),
            parent_name=ar.read_fstring(),
            position=ar.read_float_vector(3)
                     * ar.metadata["scale"]
                     * ((1, -1, 1) if ar.file_version >= EUEFormatVersion.PreserveOriginalTransforms else (1, 1, 1)),
            rotation=ar.read_float_vector(4)
                     * ((1, -1, 1, -1) if ar.file_version >= EUEFormatVersion.PreserveOriginalTransforms else (1, 1, 1, 1)),
            scale=ar.read_float_vector(3)
                  * ar.metadata["scale"]
                  * ((1, -1, 1) if ar.file_version >= EUEFormatVersion.PreserveOriginalTransforms else (1, 1, 1)),
        )


@dataclass(slots=True)
class VirtualBone:
    source_name: str
    target_name: str
    virtual_name: str

    @classmethod
    def from_archive(cls, ar: FArchiveReader) -> VirtualBone:
        return cls(
            source_name=ar.read_fstring(),
            target_name=ar.read_fstring(),
            virtual_name=ar.read_fstring(),
        )




def _validate_fmodel_animation_chunk_profile(identifier: str, sections: list[str]) -> None:
    """Validate frozen FModel chunk structure after target identity proves provenance."""
    target = "FMODEL_TARGET_SKELETON"
    if identifier == ANIM_IDENTIFIER:
        required = ("METADATA", "TRACKS")
        optional = ("CURVES",)
    elif identifier == POSE_IDENTIFIER:
        required = ("POSES", "CURVES")
        optional = ()
    else:
        raise ValueError(f"Unsupported FModel animation profile: {identifier}")

    known = (*required, *optional, target)
    counts = {name: sections.count(name) for name in known}
    duplicates = [name for name, count in counts.items() if count > 1]
    if duplicates:
        raise ValueError(f"Invalid FModel {identifier} duplicate known chunk: {duplicates[0]}")

    missing = [name for name in (*required, target) if counts[name] != 1]
    if missing:
        raise ValueError(f"Invalid FModel {identifier} missing required chunk: {missing[0]}")

    expected = [*required]
    expected.extend(name for name in optional if counts[name] == 1)
    expected.append(target)
    actual = [name for name in sections if name in known]
    if actual != expected:
        raise ValueError(
            f"Invalid FModel {identifier} chunk order: expected {expected}, got {actual}"
        )


@dataclass(slots=True)
class UEAnim:
    metadata: UEAnimMetadata = None
    tracks: list[Track] = field(default_factory=list)
    curves: list[Curve] = field(default_factory=list)
    target_skeleton_identity: FModelTargetSkeletonIdentity | None = None

    @classmethod
    def from_archive(
        cls,
        ar: FArchiveReader,
        *,
        strict_fmodel_profile: bool = False,
    ) -> UEAnim:
        data = cls()
        recognized_chunks: set[str] = set()
        section_sequence: list[str] = []
        
        if ar.file_version < EUEFormatVersion.SerializeAssetMetadata:
            data.metadata = UEAnimMetadata(num_frames=ar.read_int(), frames_per_second=ar.read_float())

        while not ar.eof():
            section_name = ar.read_fstring()
            section_sequence.append(section_name)
            array_size = ar.read_int()
            byte_size = ar.read_int()

            if array_size < 0:
                raise ValueError(f"Invalid top-level array_size: {array_size}")
            extension_name = {
                "FMODEL_TARGET_SKELETON": "FModel target skeleton",
            }.get(section_name, f"Top-level chunk {section_name}")
            if byte_size < 0:
                raise ValueError(f"{extension_name} chunk declared negative byte_size")
            if byte_size > ar.remaining():
                raise ValueError(f"{extension_name} chunk declared byte_size exceeds available data")
            if section_name == "FMODEL_TARGET_SKELETON":
                if section_name in recognized_chunks:
                    raise ValueError(f"Invalid duplicate {section_name} chunk")
                recognized_chunks.add(section_name)

            chunk = ar.chunk(byte_size)

            match section_name:
                case "METADATA":
                    data.metadata = UEAnimMetadata.from_archive(chunk)
                case "TRACKS":
                    data.tracks = chunk.read_array(
                        array_size,
                        lambda ar: Track.from_archive(ar),
                    )
                case "CURVES":
                    data.curves = chunk.read_array(
                        array_size,
                        lambda ar: Curve.from_archive(ar),
                    )
                case "FMODEL_TARGET_SKELETON":
                    _validate_fmodel_identity_chunk_count(array_size, section_name)
                    data.target_skeleton_identity = FModelTargetSkeletonIdentity.from_archive(chunk)
                case _:
                    Log.warn(f"Unknown Animation Data: {section_name}")
                    chunk.read_to_end()
            if not chunk.eof():
                raise ValueError(f"Trailing bytes in {extension_name} chunk")

        if strict_fmodel_profile:
            _validate_fmodel_animation_chunk_profile(ANIM_IDENTIFIER, section_sequence)

        return data

class EAdditiveAnimationType(IntEnum):
    AAT_None = 0
    AAT_LocalSpaceBase = 1
    AAT_RotationOffsetMeshSpace = 2
    AAT_MAX = 3

class EAdditiveBasePoseType(IntEnum):
    ABPT_None = 0
    ABPT_RefPose = 1
    ABPT_AnimScaled = 2
    ABPT_AnimFrame = 3
    ABPT_LocalAnimFrame = 4
    ABPT_MAX = 5

@dataclass(slots=True)
class UEAnimMetadata:
    num_frames: int
    frames_per_second: float
    
    ref_pose_path: str = ""
    additive_anim_type: EAdditiveAnimationType = EAdditiveAnimationType.AAT_None
    ref_pose_type: EAdditiveBasePoseType = EAdditiveBasePoseType.ABPT_None
    ref_frame_index: int = 0
    
    @classmethod
    def from_archive(cls, ar: FArchiveReader) -> UEAnimMetadata:
        return cls(
            num_frames=ar.read_int(),
            frames_per_second=ar.read_float(),
            ref_pose_path=ar.read_fstring(),
            additive_anim_type=EAdditiveAnimationType(ar.read_uint8()),
            ref_pose_type=EAdditiveBasePoseType(ar.read_uint8()),
            ref_frame_index=ar.read_int()
        )
    
@dataclass(slots=True)
class Curve:
    name: str
    keys: list[FloatKey]

    @classmethod
    def from_archive(cls, ar: FArchiveReader) -> Curve:
        return cls(
            name=ar.read_fstring(),
            keys=ar.read_serialized_array(lambda ar: FloatKey.from_archive(ar)),
        )


@dataclass(slots=True)
class Track:
    name: str
    position_keys: list[VectorKey]
    rotation_keys: list[QuatKey]
    scale_keys: list[VectorKey]

    @classmethod
    def from_archive(cls, ar: FArchiveReader) -> Track:
        return cls(
            name=ar.read_fstring(),
            position_keys=ar.read_serialized_array(
                lambda ar: VectorKey.from_archive(ar, ar.metadata["scale"]),
            ),
            rotation_keys=ar.read_serialized_array(lambda ar: QuatKey.from_archive(ar)),
            scale_keys=ar.read_serialized_array(lambda ar: VectorKey.from_archive(ar, allows_mirror=False)),
        )


@dataclass(slots=True)
class AnimKey:
    frame: int

    @classmethod
    def from_archive(cls, ar: FArchiveReader) -> AnimKey:
        return cls(frame=ar.read_int())


@dataclass(slots=True)
class VectorKey(AnimKey):
    value: list[float]

    @classmethod
    def from_archive(cls, ar: FArchiveReader, multiplier: float = 1.0, allows_mirror: bool = True) -> VectorKey:
        return cls(
            frame=ar.read_int(),
            value=ar.read_float_vector(3) 
                  * multiplier
                  * ((1, -1, 1) if allows_mirror and ar.file_version >= EUEFormatVersion.PreserveOriginalTransforms else (1, 1, 1)),
        )

    def get_vector(self) -> Vector:
        return Vector(self.value)


@dataclass(slots=True)
class QuatKey(AnimKey):
    value: tuple[float, float, float, float]

    @classmethod
    def from_archive(cls, ar: FArchiveReader) -> QuatKey:
        return cls(
            frame=ar.read_int(),
            value=ar.read_float_vector(4)
                  * ((1, -1, 1, -1) if ar.file_version >= EUEFormatVersion.PreserveOriginalTransforms else (1, 1, 1, 1)),
        )

    def get_quat(self) -> Quaternion:
        return Quaternion((self.value[3], self.value[0], self.value[1], self.value[2]))


@dataclass(slots=True)
class FloatKey(AnimKey):
    value: float

    @classmethod
    def from_archive(cls, ar: FArchiveReader) -> FloatKey:
        return cls(
            frame=ar.read_int(),
            value=ar.read_float(),
        )

@dataclass(slots=True)
class UEPose:
    poses: list[UEPoseData] = field(default_factory=list)
    curve_names: list[str] = field(default_factory=list)
    target_skeleton_identity: FModelTargetPoseIdentity | None = None
    
    @classmethod
    def from_archive(
        cls,
        ar: FArchiveReader,
        *,
        strict_fmodel_profile: bool = False,
    ) -> UEPose:
        data = cls()

        recognized_chunks: set[str] = set()
        section_sequence: list[str] = []
        while not ar.eof():
            section_name = ar.read_fstring()
            section_sequence.append(section_name)
            array_size = ar.read_int()
            byte_size = ar.read_int()

            if array_size < 0:
                raise ValueError(f"Invalid top-level array_size: {array_size}")
            extension_name = {
                "FMODEL_TARGET_SKELETON": "FModel target skeleton",
            }.get(section_name, f"Top-level chunk {section_name}")
            if byte_size < 0:
                raise ValueError(f"{extension_name} chunk declared negative byte_size")
            if byte_size > ar.remaining():
                raise ValueError(f"{extension_name} chunk declared byte_size exceeds available data")

            if section_name == "FMODEL_TARGET_SKELETON":
                if section_name in recognized_chunks:
                    raise ValueError(f"Invalid duplicate {section_name} chunk")
                recognized_chunks.add(section_name)

            chunk = ar.chunk(byte_size)

            match section_name:
                case "POSES":
                    data.poses = chunk.read_array(array_size, lambda ar: UEPoseData.from_archive(ar))
                case "CURVES":
                    data.curve_names = chunk.read_array(array_size, lambda ar: ar.read_fstring())
                case "FMODEL_TARGET_SKELETON":
                    _validate_fmodel_identity_chunk_count(array_size, section_name)
                    data.target_skeleton_identity = FModelTargetPoseIdentity.from_archive(chunk)
                case _:
                    Log.warn(f"Unknown Pose Data: {section_name}")
                    chunk.read_to_end()

            if not chunk.eof():
                raise ValueError(f"Trailing bytes in {extension_name} chunk")

        if strict_fmodel_profile:
            _validate_fmodel_animation_chunk_profile(POSE_IDENTIFIER, section_sequence)
        
        return data


@dataclass(slots=True)
class UEPoseData:
    name: str
    keys: list[UEPoseKey] = field(default_factory=list)
    curves: list[UEPoseCurveInfluence] = field(default_factory=list)

    @classmethod
    def from_archive(cls, ar: FArchiveReader) -> UEPoseData:
        return cls(
            name=ar.read_fstring(),
            keys=ar.read_serialized_array(lambda ar: UEPoseKey.from_archive(ar)),
            curves=ar.read_serialized_array(lambda ar: UEPoseCurveInfluence.from_archive(ar)),
        )

@dataclass(slots=True)
class UEPoseKey:
    bone_name: str

    position: list[float]
    rotation: tuple[float, float, float, float]
    scale: list[float] = field(default_factory=list)

    @classmethod
    def from_archive(cls, ar: FArchiveReader) -> UEPoseKey:
        return cls(
            bone_name=ar.read_fstring(),
            position=ar.read_float_vector(3) * ar.metadata["scale"] * (1, -1, 1),
            rotation=ar.read_float_vector(4) * (1, -1, 1, -1),
            scale=ar.read_float_vector(3)
        )

@dataclass(slots=True)
class UEPoseCurveInfluence:
    curve_index: int
    influence: float

    @classmethod
    def from_archive(cls, ar: FArchiveReader) -> UEPoseCurveInfluence:
        return cls(
            curve_index=ar.read_int(),
            influence=ar.read_float()
        )

"""
class UEModelPhysics:
    bodies = []

    def __init__(self, ar: FArchiveReader, scale):

        while not ar.eof():
            header_name = ar.read_fstring()
            array_size = ar.read_int()
            byte_size = ar.read_int()

            pos = ar.data.tell()
            if header_name == "BODIES":
                self.bodies = ar.read_array(array_size, lambda ar: BodySetup(ar, scale))
            else:
                Log.warn(f"Unknown Skeleton Data: {header_name}")
                ar.skip(byte_size)
            ar.data.seek(pos + byte_size, 0)

class EPhysicsType(IntEnum):
    PhysType_Default = 0
    PhysType_Kinematic = 1
    PhysType_Simulated = 2


class BodySetup:
    bone_name = ""
    physics_type = EPhysicsType.PhysType_Default

    sphere_elems = []
    box_elems = []
    capsule_elems = []
    tapered_capsule_elems = []
    convex_elems = []

    def __init__(self, ar: FArchiveReader, scale):
        self.bone_name = ar.read_fstring()
        self.physics_type = EPhysicsType(ar.read_uint8())

        self.sphere_elems = ar.read_bulk_array(lambda ar: SphereCollision(ar, scale))
        self.box_elems = ar.read_bulk_array(lambda ar: BoxCollision(ar, scale))
        self.capsule_elems = ar.read_bulk_array(lambda ar: CapsuleCollision(ar, scale))
        self.tapered_capsule_elems = ar.read_bulk_array(lambda ar: TaperedCapsuleCollision(ar, scale))
        self.convex_elems = ar.read_bulk_array(lambda ar: ConvexCollision(ar, scale))

class SphereCollision:
    name = ""
    center = []
    radius = 0

    def __init__(self, ar: FArchiveReader, scale):
        self.name = ar.read_fstring()
        self.center = [pos * scale for pos in ar.read_float_vector(3)]
        self.radius = ar.read_float()

class BoxCollision:
    name = ""
    center = []
    rotation = []
    x = 0
    y = 0
    z = 0

    def __init__(self, ar: FArchiveReader, scale):
        self.name = ar.read_fstring()
        self.center = [pos * scale for pos in ar.read_float_vector(3)]
        self.rotation = ar.read_float_vector(3)
        self.x = ar.read_float()
        self.y = ar.read_float()
        self.z = ar.read_float()

class CapsuleCollision:
    name = ""
    center = []
    rotation = []
    radius = 0
    length = 0

    def __init__(self, ar: FArchiveReader, scale):
        self.name = ar.read_fstring()
        self.center = [pos * scale for pos in ar.read_float_vector(3)]
        self.rotation = ar.read_float_vector(3)
        self.radius = ar.read_float()
        self.length = ar.read_float()

class TaperedCapsuleCollision:
    name = ""
    center = []
    rotation = []
    radius0 = 0
    radius1 = 0
    length = 0

    def __init__(self, ar: FArchiveReader, scale):
        self.name = ar.read_fstring()
        self.center = [pos * scale for pos in ar.read_float_vector(3)]
        self.rotation = ar.read_float_vector(3)
        self.radius0 = ar.read_float()
        self.radius1 = ar.read_float()
        self.length = ar.read_float()
"""
