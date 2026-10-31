"""Build conservative Blender materials from FModel material-link records."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import bpy

from .runtime_materials import FModelMaterialLinks
from .runtime_fmodel import material_links_metadata

if TYPE_CHECKING:
    from .dto.model import MaterialDto as Material
    from .runtime_materials import FModelMaterialLinks, FModelMaterialParameter
    from .runtime_material_images import FModelImageRepository


_COLOR_SEMANTICS = {"base_color", "emissive"}
_DATA_SEMANTICS = {"normal", "roughness", "metallic", "orm"}
_EXTERNAL_LOCATION_MODES = {1, 2, 4}
# V2 wire roles have distinct source contracts. Color roles can be sampled or
# supplied as vectors, Normal/AO are sampled textures, and value paths consume
# scalar or vector parameters.
_V2_COLOR_ROLES = {1, 4}
_V2_TEXTURE_ROLES = {3, 10}
_V2_VALUE_ROLES = {2, 5, 6, 7, 8, 9, 11, 12}


def _socket_by_identifier(sockets, identifier):
    """Resolve Blender shader sockets by stable identifier, not UI position/label."""
    for socket in sockets:
        if socket.identifier == identifier:
            return socket
    raise KeyError(f"Blender socket identifier {identifier!r} was not found")


@dataclass(frozen=True, slots=True)
class FModelMaterialImportOptions:
    """Controls Blender-side construction without changing material-link metadata."""

    import_materials: bool = True
    build_nodes: bool = True
    load_embedded: bool = True
    load_external: bool = True
    keep_unmapped: bool = True
    ue_export_root: str | None = None
    bundle_root: str | None = None


def build_fmodel_materials(
    mesh: bpy.types.Mesh,
    lod_materials: Sequence[Material],
    material_links: FModelMaterialLinks,
    images: FModelImageRepository,
    warn: Callable[[str], None],
    options: FModelMaterialImportOptions | None = None,
    source_slot_to_blender_index: Mapping[int, int] | None = None,
    creation_tracker=None,
    material_cache=None,
) -> None:
    """Build materials using the explicit source-slot to Blender-index mapping."""
    options = options or FModelMaterialImportOptions()
    if not options.import_materials:
        return
    if source_slot_to_blender_index is None:
        source_slot_to_blender_index = {
            material.source_slot_index: blender_index
            for blender_index, material in enumerate(lod_materials)
            if material.source_slot_index is not None
        }
    source_slot_to_blender_index = _source_slot_to_blender_indices(
        material_links.slots,
        source_slot_to_blender_index,
    )
    for slot in material_links.slots:
        material_index = source_slot_to_blender_index[slot.slot_index]
        if material_index < 0 or material_index >= len(lod_materials):
            raise ValueError(
                f"Invalid FModel material mapping: source slot {slot.slot_index} maps to "
                f"Blender material index {material_index}, but the LOD has {len(lod_materials)} materials"
            )
        if material_index >= len(mesh.materials) or mesh.materials[material_index] is None:
            raise ValueError(
                f"Invalid FModel material mapping: source slot {slot.slot_index} maps to "
                f"missing Blender material index {material_index}"
            )
        interfaces = [
            interface
            for interface in material_links.pbr_interfaces
            if interface.slot_index == slot.slot_index
        ]
        material = mesh.materials[material_index]
        if not options.build_nodes:
            _attach_provenance(material, slot, interfaces)
            continue
        try:
            existing = _find_matching_material(slot, interfaces, images, options, material_cache, mesh)
        except FileNotFoundError as exc:
            # A texture that is absent must not block dedupe lookup from falling through
            # to building the material; integrity errors still propagate.
            warn(str(exc))
            existing = None
        if existing is not None:
            mesh.materials[material_index] = existing
            if material.get("fmodel_importer_owned") and material.users == 0:
                bpy.data.materials.remove(material)
            continue
        # The material itself is the exported result and always gets built. Texture
        # linking is auxiliary: an unresolvable or unusable texture warns and drops that
        # one source, it never removes the material or aborts the import.
        material = _isolated_build_material(mesh, material_index, material, creation_tracker)
        try:
            _attach_provenance(material, slot, interfaces, options, images, mesh)
        except FileNotFoundError as exc:
            # Provenance identity needs the declared texture bytes for its cache key.
            # When they are absent, record the link metadata without the cache identity.
            warn(str(exc))
            _attach_provenance(material, slot, interfaces)
        if interfaces:
            _build_v2_material(
                material, mesh, slot, interfaces, images, warn, options
            )
        else:
            _build_material(material, mesh, slot, images, warn, options)
        if material_cache is not None:
            material_cache[
                (_material_build_profile(options), _material_cache_identity(slot, interfaces, images, options, mesh))
            ] = material


def _source_slot_to_blender_indices(slots, mapping: Mapping[int, int]) -> dict[int, int]:
    """Resolve links against the dense LOD mapping without rejecting unlinked LOD slots.

    The standard ``MATERIALS`` array describes every LOD material slot, while the
    optional ``FMODEL_MATERIALS`` chunk may contain only the slots for which a
    material link was produced.  Therefore ``mapping`` is allowed to contain
    source slots that are absent from ``slots``; those entries simply have no
    FModel material-link work to perform.  Every serialized link still requires a
    valid mapping and duplicate Blender indices remain invalid.
    """
    resolved: dict[int, int] = {}
    mapped_indices: dict[int, tuple[int, int | None]] = {}
    for slot in slots:
        source_slot = slot.slot_index
        if source_slot not in mapping:
            raise ValueError(f"Invalid FModel material mapping: source slot {source_slot} has no Blender material mapping")
        blender_index = mapping[source_slot]
        if isinstance(blender_index, bool) or not isinstance(blender_index, int) or blender_index < 0:
            raise ValueError(
                f"Invalid FModel material mapping: source slot {source_slot} has invalid "
                f"Blender material index {blender_index!r}"
            )
        previous = mapped_indices.get(blender_index)
        if previous is not None:
            previous_slot, previous_source_material = previous
            same_source_material = (
                isinstance(slot.source_material_index, int)
                and not isinstance(slot.source_material_index, bool)
                and isinstance(previous_source_material, int)
                and not isinstance(previous_source_material, bool)
                and slot.source_material_index == previous_source_material
            )
            if not same_source_material:
                raise ValueError(
                    f"Invalid FModel material mapping: source slot {source_slot} and source slot "
                    f"{previous_slot} both map to duplicate Blender material index {blender_index}"
                )
        mapped_indices[blender_index] = (source_slot, slot.source_material_index)
        resolved[source_slot] = blender_index
    return resolved


def source_slot_mapping_for_lod_materials(lod_materials, material_links) -> dict[int, int]:
    """Map serialized material-link slots to dense Blender LOD material indices.

    ``FModelMaterialSlot.slot_index`` identifies the exported section/link slot.
    In extension v2, ``source_material_index`` identifies the original Unreal
    material slot and must be used to locate the corresponding dense Blender
    ``MATERIALS`` entry.  Falling back to ``slot_index`` preserves v1 and v2
    records that do not carry an original source index.
    """
    dense_by_source = {
        material.source_slot_index: blender_index
        for blender_index, material in enumerate(lod_materials)
        if material.source_slot_index is not None
    }
    mapping: dict[int, int] = {}
    for slot in material_links.slots:
        source_index = slot.source_material_index
        if source_index is not None and source_index in dense_by_source:
            mapping[slot.slot_index] = dense_by_source[source_index]
        elif slot.slot_index in dense_by_source:
            mapping[slot.slot_index] = dense_by_source[slot.slot_index]
    return mapping


def section_ordinal_mapping_for_lod_materials(lod_materials, material_links) -> dict[int, int]:
    """Map V10 link slots to the standard ``MATERIALS`` section ordinal.

    UEFormat V10's standard MATERIALS record does not serialize the original
    Unreal source-slot field.  For V10, FModel slot_index is therefore the
    dense standard MATERIALS section ordinal; source_material_index remains
    provenance only and must never be used as the Blender material index.
    """

    mapping: dict[int, int] = {}
    for slot in material_links.slots:
        if slot.slot_index < 0 or slot.slot_index >= len(lod_materials):
            raise ValueError(
                f"Invalid FModel V10 material mapping: slot {slot.slot_index} is outside "
                f"the LOD MATERIALS section range 0..{max(-1, len(lod_materials) - 1)}",
            )
        mapping[slot.slot_index] = slot.slot_index
    return mapping


def _material_link_metadata(slot, interfaces) -> dict[str, object]:
    slot_links = FModelMaterialLinks(slots=[slot], pbr_interfaces=list(interfaces))
    metadata = material_links_metadata(slot_links)
    return {
        "version": 2 if interfaces else 1,
        **metadata["slots"][0],
        **({"pbr_interfaces": metadata["pbr_interfaces"]} if interfaces else {}),
    }


def _material_build_profile(options) -> str:
    return json.dumps({
        "implementation_version": 3,
        "build_nodes": options.build_nodes,
        "load_embedded": options.load_embedded,
        "load_external": options.load_external,
        "keep_unmapped": options.keep_unmapped,
    }, separators=(",", ":"), sort_keys=True)


def _texture_is_output(parameter, roles, invalid_roles, interfaces, options, mesh=None):
    """Share the actual texture selection rule between cache and node builders."""
    if not options.keep_unmapped:
        if interfaces and not roles and not invalid_roles:
            return False
        if not interfaces and parameter.semantic.lower() not in _COLOR_SEMANTICS | _DATA_SEMANTICS:
            return False
    mode = parameter.texture.location_mode
    if (mode == 3 and not options.load_embedded) or (mode in _EXTERNAL_LOCATION_MODES and not options.load_external):
        return False
    return mesh is None or mesh.uv_layers.get(f"UV{parameter.texture.uv_set}") is not None


def _material_cache_identity(slot, interfaces, images, options=None, mesh=None) -> str:
    options = options or FModelMaterialImportOptions()
    roles_by_parameter, invalid_roles_by_parameter = _v2_roles_by_parameter(slot, interfaces)
    parameters = []
    for index, parameter in enumerate(slot.parameters):
        if parameter.kind != 1:
            parameters.append((parameter.name, parameter.kind, parameter.semantic, parameter.value))
            continue
        roles = roles_by_parameter.get(index, [])
        if not _texture_is_output(parameter, roles, invalid_roles_by_parameter.get(index, []), interfaces, options, mesh):
            continue
        interpretations = (
            ("COLOR", "DATA")
            if any(role in {1, 4} for role in roles) and any(role not in {1, 4} for role in roles)
            else ("COLOR",) if any(role in {1, 4} for role in roles) or (not interfaces and parameter.semantic.lower() in _COLOR_SEMANTICS)
            else ("DATA",)
        )
        parameters.append((
            parameter.name,
            parameter.kind,
            parameter.semantic,
            parameter.texture.color_space,
            parameter.texture.packing,
            parameter.texture.uv_set,
            tuple(images.resource_cache_key(parameter, interpretation) for interpretation in interpretations),
        ))
    return json.dumps({
        "material": slot.unreal_material_path.strip().replace("\\", "/"),
        "parameters": parameters,
        "interfaces": [(item.parameter_index, item.role) for item in interfaces],
    }, separators=(",", ":"), sort_keys=True)


def _find_matching_material(slot, interfaces, images, options, material_cache=None, mesh=None):
    # Declared resources requested by the user must pass integrity checks even
    # when a previously built material could be reused. Never downgrade an
    # invalid URI/missing file/hash failure into a material-cache miss.
    expected_identity = _material_cache_identity(slot, interfaces, images, options, mesh)
    expected_profile = _material_build_profile(options)
    cache_key = (expected_profile, expected_identity)
    if material_cache is not None:
        cached = material_cache.get(cache_key)
        if cached is not None:
            try:
                getattr(cached, "name")
            except (AttributeError, ReferenceError, RuntimeError):
                material_cache.pop(cache_key, None)
            else:
                return cached
    for material in bpy.data.materials:
        if not material.get("fmodel_importer_owned") or material.get("fmodel_material_build_profile") != expected_profile:
            continue
        if material.get("fmodel_material_cache_identity") == expected_identity:
            if material_cache is not None:
                material_cache[cache_key] = material
            return material
    return None


def _attach_provenance(material, slot, interfaces, options=None, images=None, mesh=None) -> None:
    material["fmodel_material_link"] = json.dumps(_material_link_metadata(slot, interfaces))
    if options is not None:
        material["fmodel_material_build_profile"] = _material_build_profile(options)
    if images is not None:
        material["fmodel_material_cache_identity"] = _material_cache_identity(slot, interfaces, images, options, mesh)
    metadata = material_links_metadata(FModelMaterialLinks(slots=[slot], pbr_interfaces=list(interfaces)))
    material["fmodel_parameter_metadata"] = json.dumps([
        parameter for parameter in metadata["slots"][0]["parameters"] if parameter["kind"] in {2, 3}
    ])


def _isolated_build_material(mesh, material_index, material, creation_tracker=None):
    """Rebuild this importer's own material in place; never overwrite user work.

    A same-named material that this importer created earlier is the same Unreal
    material, so it is reused as one datablock even when several meshes reference it,
    and its graph is rebuilt from the newest import. A material the user authored is
    copied first so an import can never silently destroy hand-made nodes.
    """
    if not material.get("fmodel_importer_owned"):
        material = material.copy()
        if creation_tracker is not None:
            creation_tracker.track("materials", material)
        mesh.materials[material_index] = material
    material["fmodel_importer_owned"] = True
    return material


def _v2_role_accepts_parameter(role: int, kind: int) -> bool:
    """Return whether a V2 role can use this serialized parameter wire kind."""
    if role in _V2_COLOR_ROLES:
        return kind in {1, 3}
    if role in _V2_TEXTURE_ROLES:
        return kind == 1
    if role in _V2_VALUE_ROLES:
        return kind in {2, 3}
    return False


def _v2_roles_by_parameter(slot, interfaces, warn=None):
    """Classify interfaces before graph construction and retain invalid sources."""
    roles_by_parameter = {}
    invalid_roles_by_parameter = {}
    for interface in interfaces:
        parameter = slot.parameters[interface.parameter_index]
        if _v2_role_accepts_parameter(interface.role, parameter.kind):
            roles_by_parameter.setdefault(interface.parameter_index, []).append(interface.role)
            continue
        invalid_roles_by_parameter.setdefault(interface.parameter_index, []).append(interface.role)
        if warn is not None:
            warn(
                f"FModel parameter {parameter.name!r} role {interface.role} is incompatible with parameter kind {parameter.kind}; preserving it as Custom"
            )
    return roles_by_parameter, invalid_roles_by_parameter


def _build_material(material, mesh, slot, images, warn, options) -> None:
    """Build the V1/direct material graph with the legacy readable layout.

    The UEFormat-only identity path can provide semantic texture bindings without
    emitting ``FMODEL_PBR_INTERFACES``.  It must still receive the old node graph
    organization: UV nodes on the left, texture sources in the middle, channel
    processors before Principled, and unmapped sources in a separate frame.
    Keeping this layout here prevents the identity-only path from silently falling
    back to an unstructured pile of nodes.
    """
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()

    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (660, 0)
    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (360, 0)
    links.new(principled.outputs["BSDF"], output.inputs["Surface"])

    pbr_frame = None
    unmapped_frame = None
    uv_nodes = {}
    pbr_y = 200
    custom_y = 200
    source_spacing = 260
    pbr_semantics = {"normal", "base_color", "emissive", "roughness", "metallic", "orm"}

    def frame_for(semantic: str):
        nonlocal pbr_frame, unmapped_frame
        if semantic in pbr_semantics:
            if pbr_frame is None:
                pbr_frame = nodes.new("NodeFrame")
                pbr_frame.label = "FModel PBR Inputs"
                pbr_frame.name = pbr_frame.label
                pbr_frame.location = (-420, 0)
                pbr_frame.shrink = False
                pbr_frame.use_custom_color = True
                pbr_frame.color = (0.18, 0.36, 0.22)
            return pbr_frame
        if unmapped_frame is None:
            unmapped_frame = nodes.new("NodeFrame")
            unmapped_frame.label = "FModel Unmapped Parameters"
            unmapped_frame.name = unmapped_frame.label
            unmapped_frame.location = (-1100, 0)
            unmapped_frame.shrink = False
            unmapped_frame.use_custom_color = True
            unmapped_frame.color = (0.16, 0.24, 0.34)
        return unmapped_frame

    def link_uv(parameter, frame, y):
        uv_name = f"UV{parameter.texture.uv_set}"
        if mesh.uv_layers.get(uv_name) is None:
            return False
        key = (frame, uv_name)
        uv_node = uv_nodes.get(key)
        if uv_node is None:
            uv_node = nodes.new("ShaderNodeUVMap")
            uv_node.uv_map = uv_name
            uv_node.label = uv_name
            uv_node.parent = frame
            uv_node.location = (-260, y)
            uv_nodes[key] = uv_node
        return uv_node

    for parameter in slot.parameters:
        if parameter.kind != 1:
            continue
        semantic = parameter.semantic.lower()
        if not _texture_is_output(parameter, [], [], [], options, mesh):
            continue
        location_mode = parameter.texture.location_mode
        if location_mode == 3 and not options.load_embedded:
            warn(f"FModel texture parameter {parameter.name!r} skipped: embedded textures disabled")
            continue
        if location_mode in _EXTERNAL_LOCATION_MODES and not options.load_external:
            warn(f"FModel texture parameter {parameter.name!r} skipped: external textures disabled")
            continue
        if semantic not in pbr_semantics and not options.keep_unmapped:
            continue
        try:
            image = _load_image(parameter, semantic, images, warn)
        except FileNotFoundError as exc:
            # Linking is auxiliary: a texture that is simply absent warns and drops that
            # one source; it never removes the material.
            warn(str(exc))
            continue
        if image is None:
            continue

        frame = frame_for(semantic)
        source_y = pbr_y if frame == pbr_frame else custom_y
        uv_node = link_uv(parameter, frame, source_y)
        if uv_node is False:
            warn(
                f"FModel texture parameter {parameter.name!r} declares UV{parameter.texture.uv_set}, "
                "which is missing; skipping texture node to avoid implicit active-UV sampling"
            )
            continue

        texture = nodes.new("ShaderNodeTexImage")
        texture.label = parameter.name
        texture.name = parameter.name
        texture.image = image
        texture.parent = frame
        texture.location = (0, source_y)
        links.new(uv_node.outputs["UV"], texture.inputs["Vector"])

        if semantic == "normal":
            normal_map = nodes.new("ShaderNodeNormalMap")
            normal_map.parent = frame
            normal_map.location = (260, source_y)
            links.new(texture.outputs["Color"], normal_map.inputs["Color"])
            links.new(normal_map.outputs["Normal"], principled.inputs["Normal"])
        elif semantic == "orm":
            separate = nodes.new("ShaderNodeSeparateColor")
            separate.label = "ORM Channels (R=AO, G=Roughness, B=Metallic)"
            separate.name = "ORM Channels"
            separate.parent = frame
            separate.location = (260, source_y)
            links.new(texture.outputs["Color"], separate.inputs["Color"])
            links.new(separate.outputs["Green"], principled.inputs["Roughness"])
            links.new(separate.outputs["Blue"], principled.inputs["Metallic"])
        elif semantic in {"base_color", "emissive", "roughness", "metallic"}:
            target_socket = {
                "base_color": "Base Color",
                "emissive": "Emission Color",
                "roughness": "Roughness",
                "metallic": "Metallic",
            }[semantic]
            links.new(texture.outputs["Color"], principled.inputs[target_socket])

        if frame == pbr_frame:
            pbr_y -= source_spacing
        else:
            custom_y -= source_spacing

    # Re-enable frame auto-sizing only after all absolute/local node positions have
    # been assigned. Setting shrink=True before parenting is what caused older
    # Blender builds to collapse the recovered layout into overlapping nodes.
    for frame in (pbr_frame, unmapped_frame):
        if frame is not None:
            frame.shrink = True


def _load_image(parameter: FModelMaterialParameter, semantic: str, images: FModelImageRepository, warn):
    interpretation = "COLOR" if semantic in _COLOR_SEMANTICS else "DATA"
    try:
        return images.get_image(parameter, interpretation)
    except FileNotFoundError:
        # Plain unavailability: the texture simply is not on disk. Linking is auxiliary,
        # so callers may warn and keep the material.
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        # Anything else is an integrity or contract failure (hash mismatch, path escape,
        # unsupported mode). Those must fail closed and never be downgraded.
        raise ValueError(
            f"FModel texture parameter {parameter.name!r} could not be resolved: {exc}"
        ) from exc


def _link_uv(mesh, parameter: FModelMaterialParameter, texture, nodes, links) -> bool:
    uv_name = f"UV{parameter.texture.uv_set}"
    if mesh.uv_layers.get(uv_name) is None:
        return False
    if texture is None:
        return True
    uv_map = nodes.new("ShaderNodeUVMap")
    uv_map.uv_map = uv_name
    uv_map.label = uv_name
    uv_map.location = (texture.location.x - 220, texture.location.y)
    links.new(uv_map.outputs["UV"], texture.inputs["Vector"])
    return True


def _build_v2_material(material, mesh, slot, interfaces, images, warn, options) -> None:
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()

    custom_frame = nodes.new("NodeFrame")
    custom_frame.label = "Custom / Unmapped (Preserved)"
    custom_frame.name = custom_frame.label
    custom_frame.location = (-1100, 0)
    custom_frame.shrink = False
    custom_frame.use_custom_color = True
    custom_frame.color = (0.16, 0.24, 0.34)
    pbr_frame = nodes.new("NodeFrame")
    pbr_frame.label = "PBR Inputs"
    pbr_frame.name = pbr_frame.label
    pbr_frame.location = (-420, 0)
    pbr_frame.shrink = False
    pbr_frame.use_custom_color = True
    pbr_frame.color = (0.18, 0.36, 0.22)

    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (360, 0)
    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (660, 0)
    links.new(principled.outputs["BSDF"], output.inputs["Surface"])

    roles_by_parameter, invalid_roles_by_parameter = _v2_roles_by_parameter(slot, interfaces)

    role_order = {1: 0, 3: 1, 10: 2, 4: 3, 9: 4}
    ordered_indices = sorted(
        range(len(slot.parameters)),
        key=lambda index: (
            0 if roles_by_parameter.get(index) else 1,
            min((role_order.get(role, 5) for role in roles_by_parameter.get(index, [])), default=5),
            index,
        ),
    )
    parameter_nodes = {}
    uv_nodes = {}
    SOURCE_ROW_SPACING = 320
    PROCESSING_ROW_SPACING = 160
    pbr_y = 0
    custom_y = 0
    for parameter_index in ordered_indices:
        parameter = slot.parameters[parameter_index]
        roles = roles_by_parameter.get(parameter_index, [])
        has_invalid_roles = parameter_index in invalid_roles_by_parameter
        if parameter.kind == 1:
            if not _texture_is_output(parameter, roles, [True] if has_invalid_roles else [], interfaces, options, mesh):
                continue
            location_mode = parameter.texture.location_mode
            if location_mode == 3 and not options.load_embedded:
                warn(f"FModel texture parameter {parameter.name!r} skipped: embedded textures disabled")
                continue
            if location_mode in _EXTERNAL_LOCATION_MODES and not options.load_external:
                warn(f"FModel texture parameter {parameter.name!r} skipped: external textures disabled")
                continue
            uv_name = f"UV{parameter.texture.uv_set}"
            frame = pbr_frame if roles else custom_frame
            if mesh.uv_layers.get(uv_name) is None:
                warn(
                    f"FModel texture parameter {parameter.name!r} declares UV{parameter.texture.uv_set}, "
                    "which is missing; skipping texture node to avoid implicit active-UV sampling"
                )
                continue
            interpretations = (
                ("COLOR", "DATA")
                if any(role in {1, 4} for role in roles)
                and any(role not in {1, 4} for role in roles)
                else ("COLOR",) if any(role in {1, 4} for role in roles) else ("DATA",)
            )
            for interpretation in interpretations:
                try:
                    image = images.get_image(parameter, interpretation)
                except FileNotFoundError as exc:
                    # Linking is auxiliary: an absent texture warns and drops this source.
                    warn(str(exc))
                    continue
                source_y = pbr_y if roles else custom_y
                node = nodes.new("ShaderNodeTexImage")
                node.name = f"{parameter.name} ({interpretation})"
                node.label = f"{parameter.name} [{interpretation}]" if len(interpretations) > 1 else parameter.name
                node.image = image
                node.parent = frame
                node.location = (0, source_y)
                uv_node = uv_nodes.get((frame, uv_name))
                if uv_node is None:
                    uv_node = nodes.new("ShaderNodeUVMap")
                    uv_node.uv_map = uv_name
                    uv_node.label = uv_name
                    uv_node.parent = frame
                    uv_node.location = (-260, source_y)
                    uv_nodes[(frame, uv_name)] = uv_node
                links.new(uv_node.outputs["UV"], node.inputs["Vector"])
                parameter_nodes[(parameter_index, interpretation)] = node
                if node.parent == pbr_frame:
                    pbr_y -= SOURCE_ROW_SPACING
                else:
                    custom_y -= SOURCE_ROW_SPACING
            continue
        elif roles or has_invalid_roles:
            frame = pbr_frame if roles else custom_frame
            source_y = pbr_y if roles else custom_y
            node = nodes.new("ShaderNodeValue" if parameter.kind == 2 else "ShaderNodeRGB")
            node.name = parameter.name
            node.label = parameter.name
            node.parent = frame
            node.location = (0, source_y)
            if parameter.kind == 2:
                _socket_by_identifier(node.outputs, "Value").default_value = parameter.value
            else:
                _socket_by_identifier(node.outputs, "Color").default_value = parameter.value
        else:
            continue
        parameter_nodes[(parameter_index, None)] = node
        if node.parent == pbr_frame:
            pbr_y -= SOURCE_ROW_SPACING
        else:
            custom_y -= SOURCE_ROW_SPACING

    if parameter_nodes:
        base_index = next(
            (parameter_nodes.get((interface.parameter_index, "COLOR")) for interface in interfaces if interface.role == 1 and (interface.parameter_index, "COLOR") in parameter_nodes),
            next((node for (index, interpretation), node in parameter_nodes.items() if interpretation in {"COLOR", "DATA"} and roles_by_parameter.get(index)), None),
        )
        if base_index is not None:
            nodes.active = base_index

    def source(role, *, red=False):
        for interface in interfaces:
            parameter = slot.parameters[interface.parameter_index]
            interpretation = (
                ("COLOR" if interface.role in {1, 4} else "DATA")
                if parameter.kind == 1 else None
            )
            if (
                interface.role == role
                and interface.role in roles_by_parameter.get(interface.parameter_index, [])
                and (interface.parameter_index, interpretation) in parameter_nodes
            ):
                node = parameter_nodes[(interface.parameter_index, interpretation)]
                if red and node.type == "RGB":
                    value = nodes.new("ShaderNodeValue")
                    value.label = node.label
                    _socket_by_identifier(value.outputs, "Value").default_value = slot.parameters[interface.parameter_index].value[0]
                    value.parent = pbr_frame
                    helper_y = node.location.y
                    while any(abs(helper_y - used_y) < PROCESSING_ROW_SPACING for used_y in processing_y):
                        helper_y -= PROCESSING_ROW_SPACING
                    processing_y.append(helper_y)
                    value.location = (260, helper_y)
                    return _socket_by_identifier(value.outputs, "Value")
                output_identifier = "Color" if node.type in {"RGB", "TEX_IMAGE"} else "Value"
                return _socket_by_identifier(node.outputs, output_identifier)
        return None

    def multiply(a, b, *, factor=None, label=None):
        if a is None:
            return b
        if b is None:
            return a
        mix = nodes.new("ShaderNodeMix")
        mix.data_type = "RGBA"
        mix.blend_type = "MULTIPLY"
        if label is not None:
            mix.label = label
            mix.name = label
        _socket_by_identifier(mix.inputs, "Factor_Float").default_value = 1.0 if factor is None else factor
        mix.parent = pbr_frame
        source_y = [socket.node.location.y for socket in (a, b)]
        mix_y = sum(source_y) / len(source_y)
        while any(abs(mix_y - used_y) < PROCESSING_ROW_SPACING for used_y in processing_y):
            mix_y -= PROCESSING_ROW_SPACING
        processing_y.append(mix_y)
        mix.location = (260, mix_y)
        links.new(a, _socket_by_identifier(mix.inputs, "A_Color"))
        links.new(b, _socket_by_identifier(mix.inputs, "B_Color"))
        return _socket_by_identifier(mix.outputs, "Result_Color")

    processing_y = []
    base = multiply(source(1), source(2), label="Base Factor Multiply")
    ao = source(10)
    if ao is not None:
        ao = multiply(ao, source(11), label="AO Factor Multiply")
        ao_strength = source(12, red=True)
        if ao_strength is not None:
            base = multiply(base, ao, factor=0.0, label="AO Multiply")
            links.new(ao_strength, _socket_by_identifier(base.node.inputs, "Factor_Float"))
        else:
            base = multiply(base, ao)
    if base is not None:
        links.new(base, _socket_by_identifier(principled.inputs, "Base Color"))

    normal = source(3)
    if normal is not None:
        normal_map = nodes.new("ShaderNodeNormalMap")
        normal_map.label = "Normal Map"
        normal_map.name = "Normal Map"
        normal_map.parent = pbr_frame
        normal_y = normal.node.location.y
        while any(abs(normal_y - used_y) < PROCESSING_ROW_SPACING for used_y in processing_y):
            normal_y -= PROCESSING_ROW_SPACING
        processing_y.append(normal_y)
        normal_map.location = (260, normal_y)
        links.new(normal, _socket_by_identifier(normal_map.inputs, "Color"))
        links.new(
            _socket_by_identifier(normal_map.outputs, "Normal"),
            _socket_by_identifier(principled.inputs, "Normal"),
        )

    emissive = multiply(source(4), source(5), label="Emissive Factor Multiply")
    if emissive is not None:
        links.new(emissive, _socket_by_identifier(principled.inputs, "Emission Color"))
    strength = source(6, red=True)
    if strength is not None:
        links.new(strength, _socket_by_identifier(principled.inputs, "Emission Strength"))

    roughness = source(9, red=True)
    if roughness is None:
        minimum = source(7, red=True)
        maximum = source(8, red=True)
        if minimum is not None and maximum is not None:
            midpoint = nodes.new("ShaderNodeValue")
            midpoint.label = "Roughness Midpoint"
            midpoint.parent = pbr_frame
            midpoint_y = (minimum.node.location.y + maximum.node.location.y) / 2
            while any(abs(midpoint_y - used_y) < PROCESSING_ROW_SPACING for used_y in processing_y):
                midpoint_y -= PROCESSING_ROW_SPACING
            processing_y.append(midpoint_y)
            midpoint.location = (260, midpoint_y)
            _socket_by_identifier(midpoint.outputs, "Value").default_value = (
                minimum.default_value + maximum.default_value
            ) * 0.5
            roughness = _socket_by_identifier(midpoint.outputs, "Value")
    if roughness is not None:
        links.new(roughness, _socket_by_identifier(principled.inputs, "Roughness"))

    # Keep the old readable frame layout while avoiding Blender's early frame
    # auto-shrink pass changing child coordinates during graph construction.
    custom_frame.shrink = True
    pbr_frame.shrink = True


