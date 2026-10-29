from __future__ import annotations

from collections.abc import Iterable

from .attributes import payload_for_span, scan_attribute_spans
from .contract import (
    ANIMATION_ATTRIBUTES,
    MODEL_ATTRIBUTES,
    POSE_ATTRIBUTES,
    FModelV10Attribute,
    FModelV10ExtensionSnapshot,
    FModelV10FileKind,
)


def _allowed_attributes(file_kind: FModelV10FileKind) -> frozenset[FModelV10Attribute]:
    if file_kind is FModelV10FileKind.MODEL:
        return MODEL_ATTRIBUTES
    if file_kind is FModelV10FileKind.ANIM:
        return ANIMATION_ATTRIBUTES
    if file_kind is FModelV10FileKind.POSE:
        return POSE_ATTRIBUTES
    raise ValueError(f"unsupported FModel V10 file kind: {file_kind}")


def collect_fmodel_extensions(
    file_kind: FModelV10FileKind,
    body: bytes,
    *,
    known_standard_attributes: Iterable[str] = (),
    tolerate_identity_errors=False,
) -> FModelV10ExtensionSnapshot:
    """Collect recognized FModel attributes while preserving skip semantics.

    Standard attributes and unknown future attributes are not copied into the
    snapshot. Unknown ``FMODEL_*`` names are reported, not rejected, so a
    newer producer remains readable by an older importer. Duplicate
    recognized extension names fail closed because silently choosing one would
    make material/skeleton identity nondeterministic.
    """

    allowed = {attribute.value for attribute in _allowed_attributes(file_kind)}
    standard = set(known_standard_attributes)
    values: dict[str, bytes] = {}
    unknown: list[str] = []
    identity_warnings: list[str] = []

    for span in scan_attribute_spans(body):
        if span.name in standard:
            continue
        if not span.name.startswith("FMODEL_"):
            continue
        if span.name not in allowed:
            unknown.append(span.name)
            continue
        if span.name in values:
            if tolerate_identity_errors and span.name in {"FMODEL_SKELETON_IDENTITY", "FMODEL_TARGET_SKELETON"}:
                identity_warnings.append(f"duplicate V10 optional identity attribute: {span.name}")
                continue
            raise ValueError(f"duplicate FModel V10 extension attribute: {span.name}")
        values[span.name] = payload_for_span(body, span)

    return FModelV10ExtensionSnapshot(file_kind, values, tuple(sorted(set(unknown))), tuple(identity_warnings))

