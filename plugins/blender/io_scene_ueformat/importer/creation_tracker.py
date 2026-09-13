"""Low-overhead tracking of Blender datablocks created by one UEFormat import."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _identity(value: Any) -> tuple[str, int]:
    """Return an identity even when Blender has invalidated the wrapper."""
    try:
        as_pointer = getattr(value, "as_pointer", None)
        pointer = as_pointer() if callable(as_pointer) else 0
    except (AttributeError, ReferenceError, RuntimeError):
        pointer = 0
    if pointer:
        return ("pointer", int(pointer))
    return ("python", id(value))


def _same_identity(value: Any, token: tuple[str, int]) -> bool:
    """Compare a tracked value without letting stale StructRNA abort cleanup."""
    try:
        return _identity(value) == token
    except (AttributeError, ReferenceError, RuntimeError):
        return False


def _is_invalidated(value: Any) -> bool:
    """Return whether a Blender wrapper no longer references a datablock."""
    try:
        as_pointer = getattr(value, "as_pointer", None)
    except (ReferenceError, RuntimeError):
        return True
    if not callable(as_pointer):
        return False
    try:
        as_pointer()
    except (ReferenceError, RuntimeError):
        return True
    return False


@dataclass(slots=True)
class UEFormatImportCapture:
    """Record created datablocks without scanning all Blender collections.

    The importer creates datablocks through explicit ``bpy.data.*`` calls.
    Recording those return values gives the host a precise ownership list for
    reporting and rollback, without taking a snapshot of every datablock in a
    Blender file.
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
        if token in self._identities:
            self._identities.discard(token)
            values = self._items.get(kind)
            if values is not None:
                self._items[kind] = [
                    item for item in values if not _same_identity(item, token[1])
                ]
            return
        # A wrapper tracked by Blender pointer cannot re-derive that pointer
        # once the datablock was removed; identity lookups then fall back to
        # the python id and never match. Untrack already-invalidated wrappers
        # of this kind so rollback does not retry deleting dead data.
        if not _is_invalidated(value):
            return
        values = self._items.get(kind)
        if not values:
            return
        surviving = [item for item in values if not _is_invalidated(item)]
        if len(surviving) == len(values):
            return
        self._items[kind] = surviving
        self._identities = {
            entry for entry in self._identities
            if entry[0] != kind
            or entry in {(kind, _identity(item)) for item in surviving}
        }

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

    def clear(self) -> None:
        """Forget ownership after a committed import without touching Blender data."""
        self._items.clear()
        self._identities.clear()

    def rollback(self) -> None:
        """Remove datablocks created by this import, in dependency-safe order.

        The tracker only records values returned by explicit ``bpy.data``
        creation calls, so pre-existing user data is never selected for
        deletion. Removal is best effort per datablock: one stale Blender
        wrapper must not prevent the remaining import output from being
        cleaned up.
        """
        import bpy

        collections = {
            "objects": bpy.data.objects,
            "actions": bpy.data.actions,
            "materials": bpy.data.materials,
            "images": bpy.data.images,
            "meshes": bpy.data.meshes,
            "armatures": bpy.data.armatures,
        }
        for kind in ("objects", "actions", "meshes", "armatures", "materials", "images"):
            collection = collections[kind]
            for value in reversed(self._items.get(kind, ())):
                try:
                    collection.remove(value, do_unlink=True)
                except TypeError:
                    try:
                        collection.remove(value)
                    except (ReferenceError, RuntimeError, ValueError):
                        pass
                except (ReferenceError, RuntimeError, ValueError):
                    pass
        self.clear()

    def __bool__(self) -> bool:
        return bool(self._items)
