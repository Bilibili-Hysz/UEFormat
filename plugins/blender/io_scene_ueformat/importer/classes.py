from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, auto
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt
from mathutils import Quaternion, Vector

from ..logging import Log

if TYPE_CHECKING:
    from io_scene_ueformat.importer.reader import FArchiveReader


MAGIC = "UEFORMAT"
MODEL_IDENTIFIER = "UEMODEL"
ANIM_IDENTIFIER = "UEANIM"
POSE_IDENTIFIER = "UEPOSE"


class EUEFormatVersion(IntEnum):
    BeforeCustomVersionWasAdded = 0
    SerializeBinormalSign = 1
    AddMultipleVertexColors = 2
    AddConvexCollisionGeom = 3
    LevelOfDetailFormatRestructure = 4
    SerializeVirtualBones = 5
    SerializeMaterialPath = 6
    SerializeAssetMetadata = 7
    PreserveOriginalTransforms = 8
    AddPoseExport = 9

    VersionPlusOne = auto()
    LatestVersion = VersionPlusOne - 1


@dataclass(slots=True)
class UEModel:
    lods: list[UEModelLOD] = field(default_factory=list)
    collisions: list[ConvexCollision] = field(default_factory=list)
    skeleton: UEModelSkeleton | None = None
    material_links: FModelMaterialLinks = field(default_factory=lambda: FModelMaterialLinks())
    textures: list[TextureResource] = field(default_factory=list)
    skeleton_identity: FModelSkeletonIdentity | None = None
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
                    data.textures = TextureResource.read_all(chunk, array_size)
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
        slots_by_ordinal: dict[int, FModelMaterialSlot] = {}
        for slot in data.material_links.slots:
            if slot.slot_index < 0:
                raise ValueError(f"Invalid FModel material slot_index: {slot.slot_index}")
            if slot.slot_index in slots_by_ordinal:
                raise ValueError(f"Invalid FModel material duplicate slot_index: {slot.slot_index}")
            slots_by_ordinal[slot.slot_index] = slot
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
                ar.skip(array_size * 3 * 3)
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


def _read_non_negative_count(ar: FArchiveReader, name: str) -> int:
    count = ar.read_int()
    if count < 0:
        msg = f"Invalid FModel {name}: {count}"
        raise ValueError(msg)
    return count


# Stable per-list safety ceilings: 65,536 remains far above normal exports
# while preventing attacker-controlled extension counts from driving allocations.
MAX_FMODEL_SLOTS = 65_536
MAX_FMODEL_PARAMETERS = 65_536
MAX_FMODEL_TEXTURES = 65_536
MAX_FMODEL_PBR_INTERFACES = 65_536
MAX_FMODEL_IDENTITY_PATH_BYTES = 16 * 1024


def _validate_max_count(count: int, maximum: int, name: str) -> None:
    if count > maximum:
        raise ValueError(f"Invalid FModel {name} exceeds maximum {maximum}: {count}")


def _validate_fmodel_identity_chunk_count(array_size: int, section_name: str) -> None:
    if array_size != 1:
        raise ValueError(f"Invalid {section_name} chunk array_size: {array_size}")


@dataclass(frozen=True, slots=True)
class FModelSkeletonIdentity:
    version: int
    skeleton_path: str
    skeleton_guid: bytes | None
    bone_layout_hash: bytes

    @classmethod
    def from_archive(cls, ar: FArchiveReader) -> FModelSkeletonIdentity:
        version = int.from_bytes(ar.read_byte(), byteorder="big")
        if version != 1:
            raise ValueError(f"Unsupported FModel skeleton identity version: {version}")
        skeleton_path = ar.read_bounded_fstring(
            MAX_FMODEL_IDENTITY_PATH_BYTES,
            "FModel skeleton identity skeleton_path",
        )
        if not skeleton_path:
            raise ValueError("Invalid FModel skeleton identity skeleton_path")
        has_skeleton_guid = int.from_bytes(ar.read_byte(), byteorder="big")
        if has_skeleton_guid not in (0, 1):
            raise ValueError(f"Invalid FModel skeleton identity has_skeleton_guid: {has_skeleton_guid}")
        skeleton_guid = ar.read(16) if has_skeleton_guid else None
        bone_layout_hash = ar.read(32)
        if not ar.eof():
            raise ValueError("Trailing FModel skeleton identity data")
        return cls(version, skeleton_path, skeleton_guid, bone_layout_hash)


@dataclass(frozen=True, slots=True)
class FModelTargetSkeletonIdentity(FModelSkeletonIdentity):
    source_animation_path: str

    @classmethod
    def from_archive(cls, ar: FArchiveReader) -> FModelTargetSkeletonIdentity:
        version = int.from_bytes(ar.read_byte(), byteorder="big")
        if version != 1:
            raise ValueError(f"Unsupported FModel skeleton identity version: {version}")
        source_animation_path = ar.read_bounded_fstring(
            MAX_FMODEL_IDENTITY_PATH_BYTES,
            "FModel target skeleton source_animation_path",
        )
        if not source_animation_path:
            raise ValueError("Invalid FModel target skeleton source_animation_path")
        skeleton_path = ar.read_bounded_fstring(MAX_FMODEL_IDENTITY_PATH_BYTES, "FModel skeleton identity skeleton_path")
        if not skeleton_path:
            raise ValueError("Invalid FModel skeleton identity skeleton_path")
        has_guid = int.from_bytes(ar.read_byte(), byteorder="big")
        if has_guid not in (0, 1):
            raise ValueError(f"Invalid FModel skeleton identity has_skeleton_guid: {has_guid}")
        guid = ar.read(16) if has_guid else None
        layout_hash = ar.read(32)
        if not ar.eof():
            raise ValueError("Trailing FModel skeleton identity data")
        return cls(
            version,
            skeleton_path,
            guid,
            layout_hash,
            source_animation_path,
        )


@dataclass(frozen=True, slots=True)
class FModelTargetPoseIdentity(FModelSkeletonIdentity):
    source_pose_path: str

    @classmethod
    def from_archive(cls, ar: FArchiveReader) -> FModelTargetPoseIdentity:
        version = int.from_bytes(ar.read_byte(), byteorder="big")
        if version != 1:
            raise ValueError(f"Unsupported FModel skeleton identity version: {version}")
        source_pose_path = ar.read_bounded_fstring(
            MAX_FMODEL_IDENTITY_PATH_BYTES,
            "FModel target skeleton source_pose_path",
        )
        if not source_pose_path:
            raise ValueError("Invalid FModel target skeleton source_pose_path")
        skeleton_path = ar.read_bounded_fstring(MAX_FMODEL_IDENTITY_PATH_BYTES, "FModel skeleton identity skeleton_path")
        if not skeleton_path:
            raise ValueError("Invalid FModel skeleton identity skeleton_path")
        has_guid = int.from_bytes(ar.read_byte(), byteorder="big")
        if has_guid not in (0, 1):
            raise ValueError(f"Invalid FModel skeleton identity has_skeleton_guid: {has_guid}")
        guid = ar.read(16) if has_guid else None
        layout_hash = ar.read(32)
        if not ar.eof():
            raise ValueError("Trailing FModel skeleton identity data")
        return cls(version, skeleton_path, guid, layout_hash, source_pose_path)


def _validate_top_level_count(count: int, array_size: int, name: str) -> None:
    if count != array_size:
        raise ValueError(
            f"Invalid FModel {name} count {count} does not match top-level array_size {array_size}"
        )


@dataclass(slots=True)
class FModelMaterialLinks:
    slots: list[FModelMaterialSlot] = field(default_factory=list)
    source_material_mapping_present: bool = False
    pbr_interfaces: list[FModelPBRInterface] = field(default_factory=list)
    pbr_interfaces_present: bool = False

    @property
    def source_slot_to_blender_index(self) -> dict[int, int]:
        """Map serialized source slots to the dense MATERIALS record order."""
        return {slot.slot_index: index for index, slot in enumerate(self.slots)}

    @classmethod
    def from_archive(cls, ar: FArchiveReader, array_size: int) -> FModelMaterialLinks:
        extension_version = int.from_bytes(ar.read_byte(), byteorder="little")
        if extension_version not in (1, 2):
            msg = f"Unsupported FModel material extension version: {extension_version}"
            raise ValueError(msg)
        count = _read_non_negative_count(ar, "slot count")
        _validate_top_level_count(count, array_size, "slot")
        _validate_max_count(count, MAX_FMODEL_SLOTS, "slot count")
        if count > ar.remaining() // (24 if extension_version == 2 else 20):
            raise ValueError("Invalid FModel slot count cannot fit minimum records")
        return cls(
            slots=ar.read_array(count, lambda record: FModelMaterialSlot.from_archive(record, extension_version)),
            source_material_mapping_present=extension_version == 2,
        )


@dataclass(slots=True)
class FModelPBRInterface:
    slot_index: int
    parameter_index: int
    role: int

    @classmethod
    def read_all(cls, ar: FArchiveReader, array_size: int) -> list[FModelPBRInterface]:
        extension_version = int.from_bytes(ar.read_byte(), byteorder="little")
        if extension_version != 2:
            msg = f"Unsupported FModel PBR interface extension version: {extension_version}"
            raise ValueError(msg)
        count = _read_non_negative_count(ar, "PBR interface count")
        _validate_top_level_count(count, array_size, "PBR interface")
        _validate_max_count(count, MAX_FMODEL_PBR_INTERFACES, "PBR interface count")
        if count > ar.remaining() // 9:
            raise ValueError("Invalid FModel PBR interface count cannot fit minimum records")
        return ar.read_array(count, cls.from_archive)

    @classmethod
    def from_archive(cls, ar: FArchiveReader) -> FModelPBRInterface:
        slot_index = ar.read_int()
        parameter_index = ar.read_int()
        role = int.from_bytes(ar.read_byte(), byteorder="little")
        if slot_index < 0:
            raise ValueError(f"Invalid FModel PBR interface slot_index: {slot_index}")
        if parameter_index < 0:
            raise ValueError(f"Invalid FModel PBR interface parameter_index: {parameter_index}")
        if role < 1 or role > 12:
            raise ValueError(f"Invalid FModel PBR role: {role}")
        return cls(slot_index, parameter_index, role)


@dataclass(slots=True)
class FModelMaterialSlot:
    slot_index: int
    slot_name: str
    unreal_material_path: str
    material_json_uri: str
    source_material_index: int | None = None
    parameters: list[FModelMaterialParameter] = field(default_factory=list)

    @classmethod
    def from_archive(cls, ar: FArchiveReader, extension_version: int = 1) -> FModelMaterialSlot:
        slot_index = ar.read_int()
        if slot_index < 0:
            raise ValueError(f"Invalid FModel material slot_index: {slot_index}")
        source_material_index = ar.read_int() if extension_version == 2 else None
        if source_material_index is not None and source_material_index < -1:
            raise ValueError(f"Invalid FModel material source_material_index: {source_material_index}")
        if source_material_index == -1:
            source_material_index = None
        slot_name = ar.read_fstring()
        unreal_material_path = ar.read_fstring()
        material_json_uri = ar.read_fstring()
        parameter_count = _read_non_negative_count(ar, "parameter count")
        _validate_max_count(parameter_count, MAX_FMODEL_PARAMETERS, "parameter count")
        if parameter_count > ar.remaining() // 13:
            raise ValueError("Invalid FModel parameter count cannot fit minimum records")
        return cls(
            slot_index=slot_index,
            source_material_index=source_material_index,
            slot_name=slot_name,
            unreal_material_path=unreal_material_path,
            material_json_uri=material_json_uri,
            parameters=ar.read_array(
                parameter_count,
                FModelMaterialParameter.from_archive,
            ),
        )


@dataclass(slots=True)
class FModelMaterialParameter:
    name: str
    kind: int
    semantic: str
    value: TextureResource | float | tuple[float, float, float, float]

    @classmethod
    def from_archive(cls, ar: FArchiveReader) -> FModelMaterialParameter:
        name = ar.read_fstring()
        kind = int.from_bytes(ar.read_byte(), byteorder="little")
        semantic = ar.read_fstring()
        if kind == 1:
            value: TextureResource | float | tuple[float, float, float, float] = TextureResource.from_parameter_archive(ar)
        elif kind == 2:
            value = ar.read_float()
        elif kind == 3:
            value = tuple(ar.read_float_vector(4))
        else:
            msg = f"Invalid FModel material parameter kind: {kind}"
            raise ValueError(msg)
        return cls(name=name, kind=kind, semantic=semantic, value=value)

    @property
    def texture(self) -> TextureResource:
        assert isinstance(self.value, TextureResource)  # noqa: S101
        return self.value

    @property
    def location_mode(self) -> int:
        return self.texture.location_mode

    @property
    def uri(self) -> str:
        return self.texture.uri

    @property
    def texture_id(self) -> int:
        return self.texture.id

    @property
    def unreal_object_path(self) -> str:
        return self.texture.unreal_object_path

    @property
    def color_space(self) -> int:
        return self.texture.color_space

    @property
    def packing(self) -> int:
        return self.texture.packing

    @property
    def uv_set(self) -> int:
        return self.texture.uv_set


@dataclass(slots=True)
class TextureResource:
    id: int
    unreal_object_path: str
    mime: str = ""
    data: bytes = b""
    location_mode: int = 0
    uri: str = ""
    color_space: int = 0
    packing: int = 0
    uv_set: int = 0

    @property
    def image_bytes(self) -> bytes:
        """Raw bytes from an FMODEL_TEXTURES embedded resource."""
        return self.data

    @classmethod
    def from_parameter_archive(cls, ar: FArchiveReader) -> TextureResource:
        location_mode = int.from_bytes(ar.read_byte(), byteorder="little")
        uri = ar.read_fstring()
        texture_id = ar.read_int()
        unreal_object_path = ar.read_fstring()
        return cls(
            id=texture_id,
            unreal_object_path=unreal_object_path,
            location_mode=location_mode,
            uri=uri,
            color_space=int.from_bytes(ar.read_byte(), byteorder="little"),
            packing=int.from_bytes(ar.read_byte(), byteorder="little"),
            uv_set=ar.read_int(),
        )

    @classmethod
    def read_all(cls, ar: FArchiveReader, array_size: int) -> list[TextureResource]:
        version = int.from_bytes(ar.read_byte(), byteorder="little")
        if version != 1:
            msg = f"Unsupported FModel texture extension version: {version}"
            raise ValueError(msg)
        count = _read_non_negative_count(ar, "texture count")
        _validate_top_level_count(count, array_size, "texture")
        _validate_max_count(count, MAX_FMODEL_TEXTURES, "texture count")
        if count > ar.remaining() // 16:
            raise ValueError("Invalid FModel texture count cannot fit minimum records")
        resources = ar.read_array(count, cls.from_archive)
        ids: set[int] = set()
        for resource in resources:
            if resource.id in ids:
                raise ValueError(f"Invalid FModel duplicate embedded texture id: {resource.id}")
            ids.add(resource.id)
        return resources

    @classmethod
    def from_archive(cls, ar: FArchiveReader) -> TextureResource:
        texture_id = ar.read_int()
        unreal_object_path = ar.read_fstring()
        mime = ar.read_fstring()
        byte_length = _read_non_negative_count(ar, "texture byte length")
        if byte_length > ar.remaining():
            msg = f"Truncated FModel texture data: expected {byte_length} bytes, got {ar.remaining()}"
            raise ValueError(msg)
        data = ar.read(byte_length)
        if len(data) != byte_length:
            msg = f"Truncated FModel texture data: expected {byte_length} bytes, got {len(data)}"
            raise ValueError(msg)
        return cls(
            id=texture_id,
            unreal_object_path=unreal_object_path,
            mime=mime,
            data=data,
        )


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

    @classmethod
    def from_archive(
        cls,
        ar: FArchiveReader
    ) -> UEModelLOD:
        data = cls(name=ar.read_fstring())

        lod_size = ar.read_int()
        ar = ar.chunk(lod_size)

        while not ar.eof():
            header_name = ar.read_fstring()
            array_size = ar.read_int()
            byte_size = ar.read_int()

            pos = ar.data.tell()

            if header_name == "VERTICES":
                flattened = ar.read_float_vector(array_size * 3)
                data.vertices = (np.array(flattened) * ar.metadata["scale"]).reshape(array_size, 3)
                
                if ar.file_version >= EUEFormatVersion.PreserveOriginalTransforms:
                    data.vertices *= (1, -1, 1)
                    
            elif header_name == "INDICES":
                data.indices = np.array(
                    ar.read_int_vector(array_size),
                    dtype=np.int32,
                ).reshape(array_size // 3, 3)
            elif header_name == "NORMALS":
                # W XYZ  # TODO: change to XYZ W 
                flattened = np.array(ar.read_float_vector(array_size * 4))
                data.normals = flattened.reshape(-1, 4)[:, 1:]

                if ar.file_version >= EUEFormatVersion.PreserveOriginalTransforms:
                    data.normals *= (1, -1, 1)
                    
            elif header_name == "TANGENTS":
                ar.skip(array_size * 3 * 3)
            elif header_name == "VERTEXCOLORS":
                data.colors = [VertexColor.from_archive(ar) for _ in range(array_size)]
            elif header_name == "TEXCOORDS":
                data.uvs = []
                for _ in range(array_size):
                    count = ar.read_int()
                    uvs = np.array(ar.read_float_vector(count * 2)).reshape(count, 2)

                    if ar.file_version >= EUEFormatVersion.PreserveOriginalTransforms:
                        uvs *= (1, -1)
                        uvs += (0, 1)
                
                    data.uvs.append(uvs)
            elif header_name == "MATERIALS":
                data.materials = [
                    Material.from_archive(ar, source_slot_index)
                    for source_slot_index in range(array_size)
                ]
            elif header_name == "WEIGHTS":
                data.weights = ar.read_array(
                    array_size,
                    lambda ar: Weight.from_archive(ar),
                )
            elif header_name == "MORPHTARGETS":
                data.morphs = ar.read_array(
                    array_size,
                    lambda ar: MorphTarget.from_archive(ar),
                )
            else:
                Log.warn(f"Unknown LOD Data: {header_name}")
                ar.skip(byte_size)

            ar.data.seek(pos + byte_size, 0)
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
        
        while not ar.eof():
            section_name = ar.read_fstring()
            array_size = ar.read_int()
            byte_size = ar.read_int()

            pos = ar.data.tell()
            match section_name:
                case "METADATA":
                    data.metadata = UEModelSkeletonMetadata.from_archive(ar)
                case "BONES":
                    data.bones = ar.read_array(
                        array_size,
                        lambda ar: Bone.from_archive(ar),
                    )
                case "SOCKETS":
                    data.sockets = ar.read_array(
                        array_size,
                        lambda ar: Socket.from_archive(ar),
                    )
                case "VIRTUALBONES":
                    data.virtual_bones = ar.read_array(
                        array_size,
                        lambda ar: VirtualBone.from_archive(ar),
                    )
                case _:
                    Log.warn(f"Unknown Skeleton Data: {section_name}")
                    ar.skip(byte_size)
            ar.data.seek(pos + byte_size, 0)

        return data


@dataclass(slots=True)
class UEModelSkeletonMetadata:
    skeleton_path: str
    
    @classmethod
    def from_archive(cls, ar: FArchiveReader) -> UEModelSkeletonMetadata:
        return cls(skeleton_path=ar.read_fstring())

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
            bone_index=ar.read_short(),
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


@dataclass(slots=True)
class UEAnim:
    metadata: UEAnimMetadata = None
    tracks: list[Track] = field(default_factory=list)
    curves: list[Curve] = field(default_factory=list)
    target_skeleton_identity: FModelTargetSkeletonIdentity | None = None

    @classmethod
    def from_archive(cls, ar: FArchiveReader) -> UEAnim:
        data = cls()
        recognized_chunks: set[str] = set()
        
        if ar.file_version < EUEFormatVersion.SerializeAssetMetadata:
            data.metadata = UEAnimMetadata(num_frames=ar.read_int(), frames_per_second=ar.read_float())

        while not ar.eof():
            section_name = ar.read_fstring()
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
            additive_anim_type=EAdditiveAnimationType(int.from_bytes(ar.read_byte())),
            ref_pose_type=EAdditiveBasePoseType(int.from_bytes(ar.read_byte())),
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
    def from_archive(cls, ar: FArchiveReader) -> UEPose:
        data = cls()

        recognized_chunks: set[str] = set()
        while not ar.eof():
            section_name = ar.read_fstring()
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

            match section_name:
                case "POSES":
                    data.poses = ar.read_array(array_size, lambda ar: UEPoseData.from_archive(ar))
                case "CURVES":
                    data.curve_names = ar.read_array(array_size, lambda ar: ar.read_fstring())
                case "FMODEL_TARGET_SKELETON":
                    if section_name in recognized_chunks:
                        raise ValueError(f"Invalid duplicate {section_name} chunk")
                    _validate_fmodel_identity_chunk_count(array_size, section_name)
                    chunk = ar.chunk(byte_size)
                    data.target_skeleton_identity = FModelTargetPoseIdentity.from_archive(chunk)
                case _:
                    Log.warn(f"Unknown Pose Data: {section_name}")
                    ar.skip(byte_size)

            if section_name == "FMODEL_TARGET_SKELETON":
                recognized_chunks.add(section_name)
        
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
        self.physics_type = EPhysicsType(int.from_bytes(ar.read_byte(), byteorder="big"))

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
