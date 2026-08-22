"""Explicit FModel sidecar payloads passed from wire parsing to Blender creation.

The standard UEFormat DTOs deliberately remain free of FModel-specific state.
This module is the one-way, opt-in boundary where already validated V10
attributes become runtime material and skeleton provenance records.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .runtime_identity import (
    FModelSkeletonIdentity,
    FModelTargetPoseIdentity,
    FModelTargetSkeletonIdentity,
)
from .runtime_materials import FModelMaterialLinks
from .runtime_resources import TextureResource

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(slots=True)
class FModelImportPayload:
    """Additive import state; absent for public standard-only UEFormat files."""

    model_path: Path | None = None
    material_links: FModelMaterialLinks | None = None
    embedded_resources: dict[int, TextureResource] = field(default_factory=dict)
    skeleton_identity: FModelSkeletonIdentity | None = None
    skeleton_identity_source: str | None = None
    target_skeleton_identity: FModelTargetSkeletonIdentity | None = None
    target_pose_identity: FModelTargetPoseIdentity | None = None
    use_v10_section_mapping: bool = False

    @property
    def has_materials(self) -> bool:
        return self.material_links is not None
