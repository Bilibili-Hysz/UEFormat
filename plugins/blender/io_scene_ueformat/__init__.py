from __future__ import annotations

try:
    import zstandard as zstd
except ModuleNotFoundError:  # Blender loads the bundled wheel during install.
    zstd = None  # type: ignore[assignment]

bl_info = {
    "name": "UEFormat (.uemodel / .ueanim / .uepose)",
    "author": "Half",
    "version": (0, 10, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > UE Format",
    "category": "Import",
}


zstd_decompressor = None


def _require_zstd():
    global zstd_decompressor
    if zstd is None:
        raise RuntimeError("UEFormat V10 ZSTD support requires the bundled zstandard wheel")
    if zstd_decompressor is None:
        zstd_decompressor = zstd.ZstdDecompressor()
    return zstd_decompressor

def register(*, headless: bool = False) -> None:
    # ZSTD is resolved lazily by the wire reader.  This keeps registration
    # usable for NONE/GZIP-only environments and avoids importing Blender
    # operators before the add-on is actually registered.
    from . import op

    op.register(headless=headless)


def register_headless() -> None:
    register(headless=True)


def unregister() -> None:
    from . import op

    op.unregister()

    global zstd_decompressor
    zstd_decompressor = None


if __name__ == "__main__":
    register()
