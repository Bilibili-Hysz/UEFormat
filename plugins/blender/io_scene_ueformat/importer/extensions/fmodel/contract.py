from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping


class FModelV10FileKind(StrEnum):
    MODEL = "UEMODEL"
    ANIM = "UEANIM"
    POSE = "UEPOSE"


class FModelV10Attribute(StrEnum):
    MATERIALS = "FMODEL_MATERIALS"
    TEXTURES = "FMODEL_TEXTURES"
    PBR_INTERFACES = "FMODEL_PBR_INTERFACES"
    SKELETON_IDENTITY = "FMODEL_SKELETON_IDENTITY"
    TARGET_SKELETON = "FMODEL_TARGET_SKELETON"


MODEL_ATTRIBUTES = frozenset({
    FModelV10Attribute.MATERIALS,
    FModelV10Attribute.TEXTURES,
    FModelV10Attribute.PBR_INTERFACES,
    FModelV10Attribute.SKELETON_IDENTITY,
})
ANIMATION_ATTRIBUTES = frozenset({FModelV10Attribute.TARGET_SKELETON})
POSE_ATTRIBUTES = frozenset({FModelV10Attribute.TARGET_SKELETON})


@dataclass(frozen=True, slots=True)
class FModelV10ExtensionSnapshot:
    """Raw, validated additive attributes discovered beside standard data.

    Payloads remain bytes until a feature-specific decoder is explicitly
    enabled.  This prevents standard-only imports from constructing material
    resolvers or touching the filesystem, and makes the V9 payload decoders
    reusable only after a V10 envelope adapter has validated them.
    """

    file_kind: FModelV10FileKind
    attributes: Mapping[str, bytes]
    unknown_fmodel_attributes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "attributes", MappingProxyType(dict(self.attributes)))

    @property
    def present(self) -> bool:
        return bool(self.attributes)

    def get(self, attribute: FModelV10Attribute) -> bytes | None:
        return self.attributes.get(attribute.value)

