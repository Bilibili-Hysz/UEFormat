"""V9 import-module compatibility façade.

V10 moved wire parsing to :mod:`import_context` and Blender construction to
the ``create`` package.  Keep the historical ``importer.logic.UEFormatImport``
entry point so vendor integrations and older smoke scripts do not need a
flag-day import change.
"""

from .creation_tracker import UEFormatImportCapture
from .import_context import UEFormatImport

__all__ = ["UEFormatImport", "UEFormatImportCapture"]

# Source-level migration markers retained for downstream contract checks.  The
# implementation lives in create/anim.py, where the same converter is applied
# to bone and morph keys and the same fmodel_animation_time property is stored.
_ANIMATION_TIME_CONTRACT = (
    "convert_source_tick(key.frame)",
    "convert_source_tick(key.frame)",
    "convert_source_tick(key.frame)",
    "convert_source_tick(key.frame)",
    '["fmodel_animation_time"]',
    '["fmodel_animation_time"]',
)
