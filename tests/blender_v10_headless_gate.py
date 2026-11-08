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

    # The two v9 mesh fixtures were never imported here, and a legacy-only crash lived in that
    # gap: the material builder read a field MaterialDto does not declare, so every v9 file
    # raised AttributeError while this gate stayed green - it only ever imported v10 meshes and
    # the one v9 armature above, none of which reach the material fallback. Import them and
    # require real geometry plus material slots.
    for fixture_name, expected_slots in (
        ("fmodel_golden.uemodel", 2),
        ("fmodel_v2_deployment.uemodel", 2),
    ):
        legacy_created, legacy_dto, legacy_importer = import_fixture(FIXTURES / fixture_name)
        if legacy_created is None or legacy_created.type != "MESH":
            raise AssertionError(f"{fixture_name} did not import as a mesh")
        if len(legacy_created.data.vertices) == 0:
            raise AssertionError(f"{fixture_name} imported with no vertices")
        if len(legacy_created.data.polygons) == 0:
            raise AssertionError(f"{fixture_name} imported with no polygons")
        if len(legacy_created.material_slots) != expected_slots:
            raise AssertionError(
                f"{fixture_name} produced {len(legacy_created.material_slots)} material "
                f"slots, expected {expected_slots}"
            )
    report["legacy_v9_meshes"] = "ok"

    # The core promise covers .ueanim and .uepose as much as .uemodel, but this gate only
    # imported meshes. Their unit tests stub bpy, so nothing proved the two formats reach a
    # real Blender scene with FMODEL_TARGET_SKELETON intact.
    #
    # Both need a skeleton to bind to: create_anim asserts on an armature and reads it from
    # options.override_skeleton or the active object. The armature imported just above is
    # the one the fixtures target, so reuse it rather than inventing a second path.
    from io_scene_ueformat.options import UEAnimOptions, UEPoseOptions  # noqa: E402

    armature = created
    if armature.type != "ARMATURE":
        raise AssertionError("fixture did not produce the armature the animation targets")
    bpy.context.view_layer.objects.active = armature

    anim_path = FIXTURES / "fmodel_target_skeleton.ueanim"
    if not anim_path.is_file():
        raise AssertionError(f"required animation fixture is missing: {anim_path}")
    anim_importer = UEFormatImport(
        UEAnimOptions(link=True, enable_fmodel_extensions=True)
    )
    # For animation and pose files the first return value is the created Action itself;
    # the DTO carries curve data, not the Blender datablock.
    anim_created, anim_dto = anim_importer.import_file_with_dto(anim_path)
    action = anim_created
    # A Blender Action has no `type` attribute, so the old check could never pass.
    # Blender 5.x Actions are layered and expose `id_type` instead of `fcurves`; test the
    # datablock by the identity field it does carry.
    if action is None or getattr(action, "id_type", None) not in {"ACTION", "UNSPECIFIED"}:
        raise AssertionError(
            f".ueanim import returned {action!r} instead of an Action"
        )
    encoded = action.get("fmodel_target_skeleton_identity")
    if encoded is None:
        raise AssertionError(
            "imported action lost fmodel_target_skeleton_identity; "
            "FMODEL_TARGET_SKELETON did not survive the .ueanim import"
        )
    anim_identity = json.loads(encoded)
    if not anim_identity.get("skeleton_path"):
        raise AssertionError(".ueanim identity carries no skeleton_path")
    report["ueanim_target_skeleton"] = "ok"
    report["ueanim_identity_path"] = anim_identity["skeleton_path"]

    pose_path = FIXTURES / "fmodel_target_skeleton.uepose"
    if not pose_path.is_file():
        raise AssertionError(f"required pose fixture is missing: {pose_path}")
    pose_importer = UEFormatImport(
        UEPoseOptions(link=True, enable_fmodel_extensions=True)
    )
    pose_created, pose_dto = pose_importer.import_file_with_dto(pose_path)
    # create_pose applies the pose directly to the armature's mesh and returns None by
    # design; the identity lands on the mesh object, not on an Action.
    if pose_created is not None:
        raise AssertionError(
            f".uepose import returned {pose_created!r}; it should apply in place"
        )
    from io_scene_ueformat.importer.utils import get_armature_mesh

    pose_mesh = get_armature_mesh(armature)
    if pose_mesh is None:
        raise AssertionError(".uepose import did not resolve the armature mesh")
    pose_encoded = pose_mesh.get("fmodel_target_pose_identity")
    if pose_encoded is None:
        raise AssertionError(
            "imported mesh lost fmodel_target_pose_identity; "
            "FMODEL_TARGET_SKELETON did not survive the .uepose import"
        )
    pose_identity = json.loads(pose_encoded)
    if not pose_identity.get("skeleton_path"):
        raise AssertionError(".uepose identity carries no skeleton_path")
    report["uepose_target_skeleton"] = "ok"
    report["uepose_identity_path"] = pose_identity["skeleton_path"]
finally:
    io_scene_ueformat.unregister()

print("V10_BLENDER_HEADLESS_GATE=" + json.dumps(report, sort_keys=True))
