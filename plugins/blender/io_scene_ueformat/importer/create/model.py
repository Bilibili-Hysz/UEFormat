from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import bpy
import numpy as np
from bpy.types import ByteColorAttribute, Object
from mathutils import Vector

from ...options import UEModelOptions
from ...logging import Log
from ..dto.model import ModelDto
from ..fmodel_payload import FModelImportPayload
from ..runtime_fmodel import FModelResourceResolver, material_links_metadata, skeleton_identity_metadata
from ..runtime_material_builder import (
    FModelMaterialImportOptions,
    build_fmodel_materials,
    section_ordinal_mapping_for_lod_materials,
    source_slot_mapping_for_lod_materials,
)
from ..runtime_material_images import FModelImageRepository
from ..creation_tracker import UEFormatImportCapture
from ..mesh_batch import material_indices_for_sections
from ..morph_batch import morph_coordinates
from .skeleton import create_skeleton


def _material_options(options: UEModelOptions) -> FModelMaterialImportOptions:
    return FModelMaterialImportOptions(
        import_materials=options.import_fmodel_materials,
        build_nodes=options.build_fmodel_material_nodes,
        load_embedded=options.load_fmodel_embedded_textures,
        load_external=options.load_fmodel_external_textures,
        keep_unmapped=options.keep_fmodel_unmapped_parameters,
        ue_export_root=options.fmodel_ue_export_root or None,
        bundle_root=options.fmodel_bundle_root or None,
    )


def _build_fmodel_materials(
    mesh_data,
    lod,
    options: UEModelOptions,
    payload: FModelImportPayload | None,
    creation_tracker: UEFormatImportCapture | None = None,
) -> None:
    if payload is None or payload.material_links is None or not options.import_fmodel_materials:
        return
    if payload.model_path is None:
        # ``import_data`` has no owner path.  Never infer a filesystem root or
        # create a resolver for such data; embedded resources remain supported.
        if not payload.embedded_resources:
            return
        model_path = Path("unnamed.uemodel")
    else:
        model_path = payload.model_path
    material_options = _material_options(options)
    resolver = FModelResourceResolver(
        model_path=model_path,
        fmodel_textures=payload.embedded_resources,
        ue_export_root=material_options.ue_export_root,
        bundle_root=material_options.bundle_root,
    )
    images = FModelImageRepository(
        resolver,
        model_path,
        material_options.ue_export_root,
        material_options.bundle_root,
        creation_tracker=creation_tracker,
    )
    build_fmodel_materials(
        mesh_data,
        lod.materials,
        payload.material_links,
        images,
        Log.warn,
        material_options,
        source_slot_to_blender_index=(
            section_ordinal_mapping_for_lod_materials(lod.materials, payload.material_links)
            if payload.use_v10_section_mapping
            else source_slot_mapping_for_lod_materials(lod.materials, payload.material_links)
        ),
        creation_tracker=creation_tracker,
    )


def create_model(
    dto: ModelDto,
    options: UEModelOptions,
    name: str,
    fmodel_payload: FModelImportPayload | None = None,
    creation_tracker: UEFormatImportCapture | None = None,
) -> Object | None:
    return_object = None
    target_lod = min(options.target_lod, len(dto.lods) - 1) if dto.lods else 0
    created_lods: list[Object] = []

    for index, lod in enumerate(dto.lods):
        if index != target_lod:
            continue

        lod_name = f"{name}_{lod.name}"
        mesh_data = bpy.data.meshes.new(lod_name)
        if creation_tracker is not None:
            creation_tracker.track("meshes", mesh_data)
        mesh_data.from_pydata(lod.vertices, [], lod.indices)

        mesh_object = bpy.data.objects.new(lod_name, mesh_data)
        if creation_tracker is not None:
            creation_tracker.track("objects", mesh_object)
        return_object = mesh_object
        if options.link:
            bpy.context.collection.objects.link(mesh_object)

        if fmodel_payload is not None and fmodel_payload.material_links is not None:
            mesh_data["fmodel_material_links"] = json.dumps(
                material_links_metadata(fmodel_payload.material_links),
                separators=(",", ":"),
                sort_keys=True,
            )

        if len(lod.normals) > 0:
            mesh_data.polygons.foreach_set("use_smooth", [True] * len(mesh_data.polygons))
            mesh_data.normals_split_custom_set_from_vertices(lod.normals)
            if bpy.app.version < (4, 1, 0):
                mesh_data.use_auto_smooth = True

        if lod.weights and dto.skeleton and dto.skeleton.bones:
            for weight in lod.weights:
                bone_name = dto.skeleton.bones[weight.bone_index].name
                vertex_group = mesh_object.vertex_groups.get(bone_name)
                if not vertex_group:
                    vertex_group = mesh_object.vertex_groups.new(name=bone_name)
                vertex_group.add([weight.vertex_index], weight.weight, "ADD")

        if options.import_morph_targets and lod.morphs:
            if not mesh_object.data.shape_keys:
                mesh_object.shape_key_add(name="Basis", from_mix=False)
            base_coordinates = np.empty(len(mesh_data.vertices) * 3, dtype=np.float32)
            can_batch_coordinates = (
                hasattr(mesh_data.vertices, "foreach_get")
                and hasattr(mesh_object.data.shape_keys.key_blocks[0].data, "foreach_set")
            )
            if can_batch_coordinates:
                mesh_data.vertices.foreach_get("co", base_coordinates)
            for morph in lod.morphs:
                key = mesh_object.shape_key_add(from_mix=False)
                key.name = morph.name
                key.interpolation = "KEY_LINEAR"
                if can_batch_coordinates:
                    key.data.foreach_set(
                        "co",
                        morph_coordinates(base_coordinates, morph.deltas),
                    )
                else:
                    for delta in morph.deltas:
                        key.data[delta.vertex_index].co += Vector(delta.position)
                key.value = 0

        def squish(array):
            return array.reshape(array.size)

        vertices = [vertex for polygon in mesh_data.polygons for vertex in polygon.vertices]
        for color_info in lod.colors:
            remapped = color_info.data[vertices]
            vertex_color = cast(
                ByteColorAttribute,
                mesh_data.color_attributes.new(domain="CORNER", type="BYTE_COLOR", name=color_info.name),
            )
            vertex_color.data.foreach_set("color", squish(remapped))

        for i, uvs in enumerate(lod.uvs):
            remapped = uvs[vertices]
            uv_layer = mesh_data.uv_layers.new(name="UV" + str(i))
            uv_layer.data.foreach_set("uv", squish(remapped))

        if lod.materials:
            for i, material in enumerate(lod.materials):
                mat = bpy.data.materials.get(material.material_name)
                if mat is None:
                    mat = bpy.data.materials.new(name=material.material_name)
                    if creation_tracker is not None:
                        creation_tracker.track("materials", mat)
                    mat["fmodel_importer_owned"] = True
                mesh_data.materials.append(mat)
            mesh_data.polygons.foreach_set(
                "material_index",
                material_indices_for_sections(lod.materials, len(mesh_data.polygons)),
            )

            _build_fmodel_materials(
                mesh_data,
                lod,
                options,
                fmodel_payload,
                creation_tracker,
            )

        created_lods.append(mesh_object)

    skeleton_object = create_skeleton(
        dto,
        options,
        name,
        created_lods,
        fmodel_payload,
        creation_tracker,
    )
    if skeleton_object is not None:
        return_object = skeleton_object

    if options.import_collision and dto.collisions:
        for index, collision in enumerate(dto.collisions):
            collision_name = index if collision.name == "None" else collision.name
            collision_object_name = f"UCX_{name}_{collision_name}"
            collision_mesh_data = bpy.data.meshes.new(collision_object_name)
            if creation_tracker is not None:
                creation_tracker.track("meshes", collision_mesh_data)
            collision_mesh_data.from_pydata(collision.vertices, [], collision.indices)
            collision_mesh_object = bpy.data.objects.new(collision_object_name, collision_mesh_data)
            if creation_tracker is not None:
                creation_tracker.track("objects", collision_mesh_object)
            collision_mesh_object.display_type = "WIRE"
            if created_lods:
                collision_mesh_object.parent = created_lods[0]
            if options.link:
                bpy.context.collection.objects.link(collision_mesh_object)

    return return_object
