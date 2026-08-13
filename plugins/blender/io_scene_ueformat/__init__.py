import zstandard as zstd

from . import op

bl_info = {
    "name": "UE Format (.uemodel / .ueanim / .uepose)",
    "author": "Half",
    "version": (1, 2, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > UE Format",
    "category": "Import",
}


global zstd_decompressor
zstd_decompressor = zstd.ZstdDecompressor()

def register(*, headless: bool = False) -> None:
    global zstd_decompressor  # noqa: PLW0603
    zstd_decompressor = zstd.ZstdDecompressor()

    op.register(headless=headless)

def register_headless() -> None:
    """Register without canonical UEFormat panels or import-menu entries."""

    register(headless=True)


def unregister() -> None:
    op.unregister()

    global zstd_decompressor  # noqa: PLW0603
    del zstd_decompressor


if __name__ == "__main__":
    register()
