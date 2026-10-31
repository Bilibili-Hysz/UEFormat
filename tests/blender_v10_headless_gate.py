from __future__ import annotations

import json
import os
import sys
from hashlib import sha256
from pathlib import Path

import bpy


REPOSITORY = Path(__file__).parents[1]
PLUGIN_ROOT = REPOSITORY / "plugins" / "blender"
FIXTURES = REPOSITORY / "tests" / "fixtures"
DEFAULT_GENERATED = REPOSITORY / "tests" / "generated" / "fmodel-v10"
GENERATED = Path(os.environ.get("FMODEL_V10_GENERATED", DEFAULT_GENERATED)).expanduser().resolve()
REQUIRE_GENERATED = os.environ.get("FMODEL_V10_REQUIRE_GENERATED", "0") == "1"

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
    "generated_root": str(GENERATED),
    "tests": [],
}


def _record_fixture(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise AssertionError(f"required generated fixture is missing: {path}")
    payload = path.read_bytes()
    return {"path": path.name, "bytes": len(payload), "sha256": sha256(payload).hexdigest()}

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
    if REQUIRE_GENERATED or generated_model.is_file():
        generated_files = [
            GENERATED / "models" / filename
            for filename in (
                "fmodel_v10_none.uemodel",
                "fmodel_v10_gzip.uemodel",
                "fmodel_v10_zstd.uemodel",
            )
        ] + [
            GENERATED / "Textures" / filename
            for filename in (
                "T_FModelV10_BaseColor.png",
                "T_FModelV10_Normal.png",
                "T_FModelV10_Orm.png",
            )
        ]
        report["generated_files"] = [_record_fixture(path) for path in generated_files]
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
        # The imported material must carry the name the exporter wrote. Renaming it to
        # something importer-specific breaks round-trip identity: the same asset imported
        # through FModel and through a plain UEFormat build would then disagree on names.
        exported_material_names = [
            item.material_name for lod in dto.lods for item in lod.materials
        ]
        assert material.name in exported_material_names, (
            f"material name {material.name!r} is not the exported name "
            f"(expected one of {exported_material_names!r})"
        )
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
        texture_nodes = {
            node.label: node
            for node in material.node_tree.nodes
            if node.type == "TEX_IMAGE"
        }
        assert "FMODEL_TEXTURES.BaseColor" in texture_nodes
        assert "FMODEL_TEXTURES.Normal" in texture_nodes
        assert "FMODEL_TEXTURES.Orm" in texture_nodes
        assert all(node.image is not None for node in texture_nodes.values())
        assert all(node.image.filepath for node in texture_nodes.values())
        assert any(node.type == "NORMAL_MAP" for node in material.node_tree.nodes)
        assert any(node.type == "SEPARATE_COLOR" for node in material.node_tree.nodes)
        report["generated_mesh_materials"] = "ok"

        # A later import that meets the same material name must reuse that datablock,
        # not spawn a second one - this is the plain UEFormat behaviour we keep.
        reused_created, _, _ = import_fixture(generated_model, export_root=GENERATED)
        assert reused_created is not None
        assert reused_created.data.materials[0] is material, (
            "a same-named material must be reused instead of duplicated"
        )
        report["material_name_reuse"] = "ok"

        # Standard-only consumption of the same producer bytes: with the
        # extension gate off, the mesh must still import and must not carry
        # any FModel provenance (this is what a Linked-fallback export plus a
        # disabled gate looks like downstream).
        #
        # Material names are identities: a later import that meets the same name reuses
        # the existing datablock, which is the intended UEFormat behaviour. Drop the
        # materials this run created so this block measures a fresh scene instead of
        # reusing the FModel-stamped material above.
        for leftover in [item for item in bpy.data.materials if item.get("fmodel_material_link") is not None]:
            bpy.data.materials.remove(leftover)
        std_created, std_dto, std_importer = import_fixture(
            generated_model,
            export_root=GENERATED,
            extensions=False,
        )
        assert std_created is not None
        assert std_created.type == "MESH"
        assert len(std_created.data.vertices) == 3
        assert std_importer.last_fmodel_extensions is None
        assert std_created.get("fmodel_skeleton_identity") is None
        assert std_created.get("fmodel_material_links") is None
        assert all(
            slot.material is not None
            and slot.material.get("fmodel_material_link") is None
            for slot in std_created.material_slots
        )
        report["generated_standard_only"] = "ok"
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
