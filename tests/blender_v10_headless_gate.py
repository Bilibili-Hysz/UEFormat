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
    "tests": [],
    # Context lives here so the evaluator needs no heuristics: a top-level key is a verdict
    # and must equal "ok"; anything under "info" is reported for a human.
    "info": {
        "blender_version": bpy.app.version_string,
        "generated_root": str(GENERATED),
    },
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
        report["info"]["generated_files"] = [_record_fixture(path) for path in generated_files]
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
        # A node, a datablock and a path are not the same as pixels. Before this assertion the
        # gate passed while every image was 0x0 with has_data False, because the resolved
        # filepath used an 8.3 short name that three different textures share a prefix with.
        def _image_is_empty(node) -> bool:
            # bpy_prop_array is not a sequence for membership: `0 in node.image.size` does not
            # test the numbers, so convert before asking.
            size = list(node.image.size)
            return node.image.has_data is False or any(dimension == 0 for dimension in size)

        unloaded = [
            f"{label}={list(node.image.size)} has_data={node.image.has_data}"
            for label, node in texture_nodes.items()
            if _image_is_empty(node)
        ]
        assert not unloaded, (
            "texture nodes exist but carry no pixels: " + "; ".join(unloaded)
        )
        assert any(node.type == "NORMAL_MAP" for node in material.node_tree.nodes)
        assert any(node.type == "SEPARATE_COLOR" for node in material.node_tree.nodes)
        report["generated_mesh_materials"] = "ok"
        report["info"]["texture_pixels"] = str(
            len(texture_nodes)
        ) + " images loaded with data"

        # A later import that meets the same material name must reuse that datablock,
        # not spawn a second one - this is the plain UEFormat behaviour we keep.
        reused_created, _, _ = import_fixture(generated_model, export_root=GENERATED)
        assert reused_created is not None
        assert reused_created.data.materials[0] is material, (
            "a same-named material must be reused instead of duplicated"
        )
        report["material_name_reuse"] = "ok"
        # The requirement covers a later import of a DIFFERENT model that shares a material name,
        # not just re-importing the same file. Check both, plus that a datablock the importer did
        # not create is left alone - the importer owns what it built, not what a user made.
        cross_created, _, _ = import_fixture(FIXTURES / "fmodel_v2_deployment.uemodel")
        assert cross_created is not None, "cross-file reuse fixture did not import"
        cross_materials = [slot.material for slot in cross_created.material_slots if slot.material]
        assert cross_materials, "cross-file import produced no materials"
        golden = [item for item in bpy.data.materials if item.name.startswith("M_Golden")]
        assert len(golden) == 2, (
            f"a second file with the same material names produced {len(golden)} datablocks, "
            "expected 2 - materials are being duplicated instead of reused"
        )
        assert all(material in golden for material in cross_materials), (
            "cross-file import created a material that is not one of the shared datablocks"
        )

        # A collision has to use the name the fixture declares, otherwise the importer never looks
        # it up and the assertion proves nothing. Remove the importer-owned M_GoldenBody first, then
        # put a user-authored material under that exact name.
        for owned in [item for item in bpy.data.materials
                      if item.name.startswith("M_GoldenBody")]:
            bpy.data.materials.remove(owned)
        user_material = bpy.data.materials.new("M_GoldenBody")
        user_material["authored_by"] = "user"
        user_material.use_nodes = True
        user_material.node_tree.nodes.clear()
        marker_node = user_material.node_tree.nodes.new("ShaderNodeValue")
        marker_node.name = "USER_MARKER"
        user_node_count = len(user_material.node_tree.nodes)

        collided, _, _ = import_fixture(FIXTURES / "fmodel_golden.uemodel")
        assert collided is not None, "collision import failed"
        survivor = bpy.data.materials.get("M_GoldenBody")
        assert survivor is not None, "the importer removed a user-authored material"
        assert survivor.get("authored_by") == "user", (
            "the importer consumed the user's M_GoldenBody datablock instead of copying it"
        )
        assert survivor.node_tree.nodes.get("USER_MARKER") is not None, (
            "the importer destroyed a node in the user-authored M_GoldenBody"
        )
        assert len(survivor.node_tree.nodes) == user_node_count, (
            "the importer rebuilt the user-authored M_GoldenBody node tree"
        )
        imported = [slot.material for slot in collided.material_slots if slot.material]
        assert all(material is not survivor for material in imported), (
            "the imported mesh is bound to the user's datablock instead of its own copy"
        )
        assert all(material.get("fmodel_importer_owned") for material in imported), (
            "the imported materials are not marked as importer-owned"
        )

        report["material_reuse_scope"] = "ok"
        report["info"]["material_datablocks"] = str(len(golden))

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

    # The v9 mesh fixtures also carry scalar and vector parameters, and the v1 builder used to
    # drop them: only kind == 1 (texture) survived the loop, so Principled kept its default
    # roughness while fmodel_parameter_metadata recorded 0.25.
    # Only fmodel_golden takes the v1 builder: it has no FMODEL_PBR_INTERFACES, so the dispatch
    # selects _build_material. fmodel_v2_deployment carries the chunk and takes the v2 builder,
    # which emits a value node only when an interface role points at the parameter - it has no
    # Roughness interface, so checking it there would assert the wrong contract.
    for fixture_name, expected_roughness in (("fmodel_golden.uemodel", 0.25),):
        param_created, _, _ = import_fixture(FIXTURES / fixture_name)
        if param_created is None:
            raise AssertionError(f"{fixture_name} did not import")
        checked = False
        for slot in param_created.material_slots:
            material = slot.material
            if material is None or material.node_tree is None:
                continue
            principled = next(
                (node for node in material.node_tree.nodes
                 if node.type == "BSDF_PRINCIPLED"),
                None,
            )
            if principled is None:
                continue
            value_nodes = {
                node.name: node
                for node in material.node_tree.nodes
                if node.type in {"VALUE", "RGB"}
            }
            if "Roughness" not in value_nodes:
                continue
            checked = True
            observed = round(principled.inputs["Roughness"].default_value, 4)
            if observed != expected_roughness:
                raise AssertionError(
                    f"{fixture_name}: Roughness parameter is {expected_roughness}, but the "
                    f"Principled socket reads {observed}"
                )
            if "Tint" not in value_nodes:
                raise AssertionError(
                    f"{fixture_name}: Tint vector parameter produced no RGB node"
                )
        if not checked:
            raise AssertionError(
                f"{fixture_name}: no material received a Roughness value node; scalar and "
                "vector parameters are being dropped again"
            )
    report["material_parameters"] = "ok"
    report["info"]["parameter_roughness"] = "0.25 applied"



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

    # The one shipped .ueanim carries a single bone, a single frame and zero tracks, so it
    # proves the header and the FMODEL_TARGET_SKELETON chunk survive - nothing about curve
    # decoding. fmodel_animated_walk.ueanim carries three bone tracks with three keys each and
    # two float curves, so the track and curve paths are exercised for real.
    from io_scene_ueformat.options import UEAnimOptions, UEPoseOptions  # noqa: E402

    armature = created
    bpy.context.view_layer.objects.active = armature

    rich_path = FIXTURES / "fmodel_animated_walk.ueanim"
    if not rich_path.is_file():
        raise AssertionError(f"required animation fixture is missing: {rich_path}")
    rich_importer = UEFormatImport(UEAnimOptions(link=True, enable_fmodel_extensions=True))
    rich_action, rich_dto = rich_importer.import_file_with_dto(rich_path)
    if len(rich_dto.tracks) < 3:
        raise AssertionError(
            f"animated fixture decoded {len(rich_dto.tracks)} tracks, expected at least 3"
        )
    if len(rich_dto.curves) < 2:
        raise AssertionError(
            f"animated fixture decoded {len(rich_dto.curves)} curves, expected at least 2"
        )
    if rich_dto.metadata.num_frames < 2:
        raise AssertionError("animated fixture decoded fewer than two frames")
    start, end = rich_action.frame_range
    if end <= start:
        raise AssertionError(
            f"imported action has a flat frame range [{start}, {end}]; keys were dropped"
        )
    report["animated_tracks"] = "ok"
    report["info"]["animated_frames"] = f"{start:.0f}-{end:.0f}"

    # The shipped .uepose carries zero pose entries, so it proves the header and the identity
    # chunk survive and nothing about decoding pose keys or curve influences.
    # fmodel_pose_contact.uepose carries one entry with four bones, two curve influences and
    # three curve names.
    rich_pose_path = FIXTURES / "fmodel_pose_contact.uepose"
    if not rich_pose_path.is_file():
        raise AssertionError(f"required pose fixture is missing: {rich_pose_path}")
    rich_pose_importer = UEFormatImport(UEPoseOptions(link=True, enable_fmodel_extensions=True))
    rich_pose_created, rich_pose_dto = rich_pose_importer.import_file_with_dto(rich_pose_path)
    if rich_pose_created is not None:
        raise AssertionError(".uepose import should apply in place and return None")
    if len(rich_pose_dto.poses) < 1:
        raise AssertionError("pose fixture decoded no pose entries")
    if len(rich_pose_dto.poses[0].keys) < 4:
        raise AssertionError(
            f"pose entry carries {len(rich_pose_dto.poses[0].keys)} bone keys, expected at least 4"
        )
    if len(rich_pose_dto.poses[0].curves) < 2:
        raise AssertionError("pose entry carries fewer than two curve influences")
    if len(rich_pose_dto.curve_names) < 3:
        raise AssertionError("pose fixture decoded fewer than three curve names")
    if not rich_pose_importer.last_fmodel_identity_warnings == []:
        raise AssertionError(
            "pose identity was rejected: "
            + "; ".join(rich_pose_importer.last_fmodel_identity_warnings)
        )
    report["pose_payload"] = "ok"
    report["info"]["pose_bones"] = str(len(rich_pose_dto.poses[0].keys))

    # Every other mesh fixture is a single triangle, so the import path has never carried a
    # real vertex count. fmodel_large_grid.uemodel is a 130x130 grid: 16,900 vertices and
    # 33,282 triangles. Scale is where index handling, per-vertex arrays and mesh construction
    # actually fail, and none of the small fixtures can show it.
    large_path = FIXTURES / "fmodel_large_grid.uemodel"
    if not large_path.is_file():
        raise AssertionError(
            f"required large fixture is missing: {large_path} "
            "(regenerate with scripts/make_large_model_fixture.py)"
        )
    large_created, large_dto, large_importer = import_fixture(large_path)
    if large_created is None or large_created.type != "MESH":
        raise AssertionError("large fixture did not import as a mesh")
    large_vertices = len(large_created.data.vertices)
    large_polygons = len(large_created.data.polygons)
    if large_vertices != 16900:
        raise AssertionError(f"large fixture produced {large_vertices} vertices, expected 16900")
    if large_polygons != 33282:
        raise AssertionError(f"large fixture produced {large_polygons} polygons, expected 33282")
    if len(large_created.data.loops) != 99846:
        raise AssertionError(
            f"large fixture produced {len(large_created.data.loops)} loops, expected 99846"
        )
    if len(large_created.material_slots) != 2:
        raise AssertionError("large fixture did not keep both material slots")
    report["large_mesh"] = "ok"
    report["info"]["large_vertices"] = str(large_vertices)

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
    report["info"]["ueanim_identity_path"] = anim_identity["skeleton_path"]

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
    report["info"]["uepose_identity_path"] = pose_identity["skeleton_path"]
finally:
    io_scene_ueformat.unregister()

print("V10_BLENDER_HEADLESS_GATE=" + json.dumps(report, sort_keys=True))
