"""FModel additive attributes for the UEFormat V10 importer."""

from .contract import (
    FModelV10Attribute,
    FModelV10FileKind,
    FModelV10ExtensionSnapshot,
)
from .envelope import FModelV10Envelope, parse_envelope
from .routing import collect_fmodel_extensions

__all__ = [
    "FModelV10Attribute",
    "FModelV10Envelope",
    "FModelV10ExtensionSnapshot",
    "FModelV10FileKind",
    "collect_fmodel_extensions",
    "parse_envelope",
]

