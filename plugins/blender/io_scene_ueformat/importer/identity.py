from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, Sequence

from .protocol import (
    MAX_FMODEL_IDENTITY_PATH_BYTES,
    _read_strict_identity_fstring,
    _validate_logical_unreal_path,
)

if TYPE_CHECKING:
    from io_scene_ueformat.importer.reader import FArchiveReader


class _BoneLike(Protocol):
    name: str
    parent_index: int


@dataclass(frozen=True, slots=True)
class FModelSkeletonIdentity:
    version: int
    skeleton_path: str
    skeleton_guid: bytes | None
    bone_layout_hash: bytes

    @classmethod
    def from_archive(cls, ar: FArchiveReader) -> FModelSkeletonIdentity:
        version = ar.read_uint8()
        if version != 1:
            raise ValueError(f"Unsupported FModel skeleton identity version: {version}")
        skeleton_path = _validate_logical_unreal_path(
            _read_strict_identity_fstring(
                ar,
                MAX_FMODEL_IDENTITY_PATH_BYTES,
                "FModel skeleton identity skeleton_path",
            ),
            "FModel skeleton identity skeleton_path",
        )
        has_skeleton_guid = ar.read_uint8()
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
        version = ar.read_uint8()
        if version != 1:
            raise ValueError(f"Unsupported FModel skeleton identity version: {version}")
        source_animation_path = _validate_logical_unreal_path(
            _read_strict_identity_fstring(
                ar,
                MAX_FMODEL_IDENTITY_PATH_BYTES,
                "FModel target skeleton source_animation_path",
            ),
            "FModel target skeleton source_animation_path",
        )
        skeleton_path = _validate_logical_unreal_path(
            _read_strict_identity_fstring(
                ar,
                MAX_FMODEL_IDENTITY_PATH_BYTES,
                "FModel skeleton identity skeleton_path",
            ),
            "FModel skeleton identity skeleton_path",
        )
        has_guid = ar.read_uint8()
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
        version = ar.read_uint8()
        if version != 1:
            raise ValueError(f"Unsupported FModel skeleton identity version: {version}")
        source_pose_path = _validate_logical_unreal_path(
            _read_strict_identity_fstring(
                ar,
                MAX_FMODEL_IDENTITY_PATH_BYTES,
                "FModel target skeleton source_pose_path",
            ),
            "FModel target skeleton source_pose_path",
        )
        skeleton_path = _validate_logical_unreal_path(
            _read_strict_identity_fstring(
                ar,
                MAX_FMODEL_IDENTITY_PATH_BYTES,
                "FModel skeleton identity skeleton_path",
            ),
            "FModel skeleton identity skeleton_path",
        )
        has_guid = ar.read_uint8()
        if has_guid not in (0, 1):
            raise ValueError(f"Invalid FModel skeleton identity has_skeleton_guid: {has_guid}")
        guid = ar.read(16) if has_guid else None
        layout_hash = ar.read(32)
        if not ar.eof():
            raise ValueError("Trailing FModel skeleton identity data")
        return cls(version, skeleton_path, guid, layout_hash, source_pose_path)


def compute_skeleton_layout_hash(bones: Sequence[tuple[str, int] | _BoneLike]) -> bytes:
    """Match SkeletonIdentityV1: strict UTF-8 names and little-endian fields."""
    pairs = [(bone[0], bone[1]) if isinstance(bone, tuple) else (bone.name, bone.parent_index) for bone in bones]
    buffer = bytearray(b"fmodel-skeleton-layout-v1\0")
    buffer.extend(struct.pack("<i", len(pairs)))
    seen_names: set[str] = set()
    for index, (name, parent_index) in enumerate(pairs):
        if not name or name.isspace() or "\0" in name:
            raise ValueError("Skeleton bone names must be non-empty and contain no NUL")
        if name in seen_names:
            raise ValueError("Skeleton bone names must be unique")
        if parent_index < -1 or parent_index >= index:
            raise ValueError("Skeleton parents must be -1 or reference an earlier bone")
        seen_names.add(name)
        try:
            name_bytes = name.encode("utf-8", "strict")
        except UnicodeEncodeError as error:
            raise ValueError("Skeleton bone names must be strict UTF-8") from error
        buffer.extend(struct.pack("<i", len(name_bytes)))
        buffer.extend(name_bytes)
        buffer.extend(struct.pack("<i", parent_index))
    return hashlib.sha256(buffer).digest()


