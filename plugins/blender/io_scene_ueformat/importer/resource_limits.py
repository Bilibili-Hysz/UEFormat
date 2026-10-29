"""Optional operator resource budgets, independent of UEFormat validity.

Environment variables FMODEL_UEFORMAT_MAX_BYTES, MAX_ARRAY_ITEMS,
MAX_VECTOR_SCALARS, MAX_ATTRIBUTES and MAX_EXTENSION_BYTES accept a positive
integer. Unset or zero means no policy cap; serialized lengths are still
checked against the actual archive by each reader.
"""

import os


class ResourceLimitError(ValueError):
    """A user-selected resource budget was exceeded."""


def optional_limit(category):
    name = "FMODEL_UEFORMAT_" + category
    raw = os.environ.get(name, "0")
    try:
        value = int(raw)
    except (TypeError, ValueError) as error:
        raise ResourceLimitError(f"{name} must be a non-negative integer") from error
    if value < 0:
        raise ResourceLimitError(f"{name} must be a non-negative integer")
    return value or None


def enforce_limit(value, category, label):
    maximum = optional_limit(category)
    if maximum is not None and value > maximum:
        raise ResourceLimitError(f"{label} exceeds configured {category} budget: {value} > {maximum}")
