from __future__ import annotations

import json
import sys
from pathlib import Path

import bpy


REPOSITORY = Path(__file__).parents[1]
PLUGIN_ROOT = REPOSITORY / "plugins" / "blender"
FIXTURES = REPOSITORY / "tests" / "fixtures"
GENERATED = REPOSITORY / "tests" / "generated" / "fmodel-v10"

sys.dont_write_bytecode = True
sys.path.insert(0, str(PLUGIN_ROOT))

import io_scene_ueformat  # noqa: E402
from io_scene_ueformat.importer.import_context import UEFormatImport  # noqa: E402
from io_scene_ueformat.options import UEModelOptions  # noqa: E402


def import_fixture(
    path: Path,
    *,
    extensions: bool = True,
    export_root: Path | None = None,
):
    importer = UEFormatImport(
        UEModelOptions(
            link=True,
            enable_fmodel_extensions=extensions,
            fmodel_ue_export_root=str(export_root) if export_root is not None else "",
        ),
    )
    created, dto = importer.import_file_with_dto(path)
    return created, dto, importer


report: dict[str, object] = {
    "blender_version": bpy.app.version_string,
    "tests": [],
}

io_scene_ueformat.register_headless()
try:
    settings = bpy.context.scene.uf_settings
    assert settings.enable_fmodel_extensions is True
    assert not hasattr(bpy.types, "UEFORMAT_PT_Panel")

    for filename in (
        "fmodel_v10/fmodel_v10_none.uemodel",
        "fmodel_v10/fmodel_v10_gzip.uemodel",
    ):
        created, dto, importer = import_fixture(FIXTURES / filename)
        assert created is None
        assert dto.lods == []
        assert importer.last_fmodel_extensions is not None
        assert importer.last_fmodel_extensions.present
        report["tests"].append({"fixture": filename, "result": "ok"})

    created, dto, importer = import_fixture(
        FIXTURES / "fmodel_v10/fmodel_v10_none.uemodel",
        extensions=False,
    )
    assert created is None
    assert dto.lods == []
    assert importer.last_fmodel_extensions is None
    report["tests"].append({"fixture": "standard-only", "result": "ok"})

    try:
        import zstandard  # noqa: F401
    except ModuleNotFoundError:
        report["zstd"] = "unavailable in Blender Python"
    else:
        created, dto, importer = import_fixture(
            FIXTURES / "fmodel_v10/fmodel_v10_zstd.uemodel",
        )
        assert created is None
        assert dto.lods == []
        assert importer.last_fmodel_extensions is not None
        report["zstd"] = "ok"

    generated_model = GENERATED / "models" / "fmodel_v10_none.uemodel"
    if generated_model.is_file():
        created, dto, importer = import_fixture(
            generated_model,
            export_root=GENERATED,
        )
        assert created is not None
        assert created.type == "MESH"
        assert len(created.data.vertices) == 3
        assert len(created.data.polygons) == 1
        assert len(created.data.materials) == 1
        material = created.data.materials[0]
        assert material.get("fmodel_material_link") is not None
        assert material.node_tree is not None
        principled = next(
            node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED"
        )
        output = next(
            node for node in material.node_tree.nodes if node.type == "OUTPUT_MATERIAL"
        )
        assert any(
            link.from_node == principled and link.to_node == output
            for link in material.node_tree.links
        )
        assert any(
            node.type == "TEX_IMAGE"
            and node.label == "FMODEL_TEXTURES.BaseColor"
            for node in material.node_tree.nodes
        )
        assert any(node.type == "NORMAL_MAP" for node in material.node_tree.nodes)
        assert any(node.type == "SEPARATE_COLOR" for node in material.node_tree.nodes)
        report["generated_mesh_materials"] = "ok"
    else:
        report["generated_mesh_materials"] = "not generated"

    created, dto, importer = import_fixture(
        FIXTURES / "fmodel_skeleton_identity.uemodel",
    )
    assert created is not None
    assert created.type == "ARMATURE"
    assert created.get("fmodel_skeleton_identity") is not None
    report["legacy_v9_identity"] = "ok"
finally:
    io_scene_ueformat.unregister()

print("V10_BLENDER_HEADLESS_GATE=" + json.dumps(report, sort_keys=True))
