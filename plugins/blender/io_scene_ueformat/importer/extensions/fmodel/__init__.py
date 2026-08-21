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
    "collect_fmodel_legacy_extensions",
    "collect_fmodel_extensions",
    "decode_identity_payload",
    "decode_legacy_identity_chunk",
    "parse_envelope",
    "promote_legacy_snapshot_to_v10_shape",
]
