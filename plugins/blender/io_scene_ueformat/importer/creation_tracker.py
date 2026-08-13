"""Low-overhead tracking of Blender datablocks created by one UEFormat import."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _identity(value: Any) -> tuple[str, int]:
    as_pointer = getattr(value, "as_pointer", None)
    if callable(as_pointer):
        try:
            pointer = as_pointer()
        except (AttributeError, ReferenceError, RuntimeError):
            pointer = 0
        if pointer:
            return ("pointer", int(pointer))
    return ("python", id(value))


@dataclass(slots=True)
class UEFormatImportCapture:
    """Record created datablocks without scanning all Blender collections.

    The importer creates objects, meshes, armatures, materials and images through
    explicit ``bpy.data.*`` calls. Recording those return values lets an import
    materialization move and roll back one model without a component-wide
    snapshot of every Blender datablock.
    """

    _items: dict[str, list[Any]] = field(default_factory=dict)
    _identities: set[tuple[str, tuple[str, int]]] = field(default_factory=set)

    def track(self, kind: str, value: Any) -> Any:
        if value is None:
            return value
        token = (kind, _identity(value))
        if token not in self._identities:
            self._identities.add(token)
            self._items.setdefault(kind, []).append(value)
        return value

    def discard(self, kind: str, value: Any) -> None:
        if value is None:
            return
        token = (kind, _identity(value))
        if token not in self._identities:
            return
        self._identities.discard(token)
        values = self._items.get(kind)
        if values is None:
            return
        self._items[kind] = [item for item in values if _identity(item) != token[1]]

    def items(self, kind: str) -> tuple[Any, ...]:
        return tuple(self._items.get(kind, ()))

    @property
    def objects(self) -> tuple[Any, ...]:
        return self.items("objects")

    @property
    def counts(self) -> dict[str, int]:
        """Return counts for currently tracked, non-discarded datablocks."""
        return {
            kind: len(values)
            for kind, values in self._items.items()
            if values
        }

    def __bool__(self) -> bool:
        return bool(self._items)
