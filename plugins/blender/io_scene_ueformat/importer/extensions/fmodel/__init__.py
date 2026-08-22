"""FModel additive attributes for the UEFormat V10 importer."""

from .contract import (
    FModelV10Attribute,
    FModelV10FileKind,
    FModelV10ExtensionSnapshot,
)
from .envelope import FModelV10Envelope, parse_envelope
from .legacy import (
    FModelLegacyChunkError,
    FModelLegacyExtensionChunk,
    FModelLegacyExtensionSnapshot,
    collect_fmodel_legacy_extensions,
    promote_legacy_snapshot_to_v10_shape,
)
from .identity import (
    FModelIdentityRecord,
    decode_identity_payload,
    decode_legacy_identity_chunk,
    decode_v10_identity_chunk,
)
from .materials import (
    FModelMaterialDecodeError,
    FModelEmbeddedTextureRecord,
    FModelMaterialLinksSnapshot,
    FModelPBRInterface,
    FModelTextureSnapshot,
    decode_legacy_material_links,
    decode_legacy_pbr,
    decode_legacy_textures,
    decode_material_links_payload,
    decode_pbr_payload,
    decode_texture_payload,
    decode_v10_material_links,
    decode_v10_pbr,
    decode_v10_textures,
)
from .routing import collect_fmodel_extensions

__all__ = [
    "FModelV10Attribute",
    "FModelV10Envelope",
    "FModelV10ExtensionSnapshot",
    "FModelV10FileKind",
    "FModelLegacyChunkError",
    "FModelLegacyExtensionChunk",
    "FModelLegacyExtensionSnapshot",
    "FModelIdentityRecord",
    "FModelMaterialDecodeError",
    "FModelEmbeddedTextureRecord",
    "FModelMaterialLinksSnapshot",
    "FModelPBRInterface",
    "FModelTextureSnapshot",
    "collect_fmodel_legacy_extensions",
    "collect_fmodel_extensions",
    "decode_identity_payload",
    "decode_legacy_identity_chunk",
    "decode_v10_identity_chunk",
    "decode_legacy_material_links",
    "decode_legacy_pbr",
    "decode_legacy_textures",
    "decode_material_links_payload",
    "decode_pbr_payload",
    "decode_texture_payload",
    "decode_v10_material_links",
    "decode_v10_pbr",
    "decode_v10_textures",
    "parse_envelope",
    "promote_legacy_snapshot_to_v10_shape",
]
