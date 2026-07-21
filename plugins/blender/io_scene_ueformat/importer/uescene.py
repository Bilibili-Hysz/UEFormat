"""Strict, bpy-free reader and import planner for UEScene V1."""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from hashlib import sha256
from math import isfinite, sqrt
import os
from pathlib import Path
import struct
import zlib

_MAGIC=b"UESCENE\0"; _HEADER_SIZE=44; _ENTRY_SIZE=40; _MAX_FILE_SIZE=2*1024**3
_MAX_ENTRIES=64; _MAX_CHUNK_BYTES=1024**3; _MAX_FIXED_RECORDS=10_000_000; _MAX_STRINGS=1_000_000; _MAX_STRING_BYTES=65_536; _MAX_URI_BYTES=4096; _MAX_SOURCE_PATH_BYTES=16_384
_MAX_ASSETS=1_000_000; _MAX_ACTORS=1_000_000; _MAX_COMPONENTS=4_000_000; _MAX_TRANSFORMS=5_000_000; _MAX_LIGHTS=1_000_000
_MAX_LODS_PER_ASSET=64; _MAX_INSTANCE_SETS=1_000_000; _MAX_INSTANCE_TRANSFORMS=50_000_000
_MAX_OVERRIDES=4_000_000; _MAX_DIAGNOSTICS=1_000_000; _MAX_BLOB_BYTES=64*1024**2; _MAX_OPAQUE_BYTES=1024**2
_KNOWN={b"META",b"STRS",b"ASST",b"ACTR",b"COMP",b"XFRM",b"INST",b"LITE",b"MATO",b"DIAG",b"BNDX"}
_REQUIRED=_KNOWN-{b"INST",b"LITE"}

class UESceneError(ValueError): pass

@dataclass(frozen=True, slots=True)
class RequiredModelFailure:
    component_id:int; asset_id:int; code:str; uri:str|None; path:Path|None; detail:str

class RequiredModelError(UESceneError):
    def __init__(self, failures:tuple[RequiredModelFailure,...]):
        self.failures=failures
        super().__init__(f"{len(failures)} required model resource(s) failed validation")

class LightType(IntEnum):
    Directional=1; Point=2; Spot=3; Rect=4
    DIRECTIONAL=Directional; POINT=Point; SPOT=Spot; RECT=Rect
class LightColorEncoding(IntEnum):
    Rgba8=1; RGBA8=Rgba8
class LightUnits(IntEnum):
    Unitless=0; Candelas=1; Lumens=2; Ev=3; Lux=4
    UNITLESS=Unitless; CANDELAS=Candelas; LUMENS=Lumens; EV=Ev; LUX=Lux
class LightFlags:
    CastsShadows=1; HasAttenuation=2; HasSpotAngles=4; HasRectDimensions=8; UsesInverseSquaredFalloff=16
    CASTS_SHADOWS=CastsShadows; HAS_ATTENUATION=HasAttenuation; HAS_SPOT_ANGLES=HasSpotAngles; HAS_RECT_DIMENSIONS=HasRectDimensions; USES_INVERSE_SQUARED_FALLOFF=UsesInverseSquaredFalloff
    ALLOWED_MASK=CASTS_SHADOWS|HAS_ATTENUATION|HAS_SPOT_ANGLES|HAS_RECT_DIMENSIONS|USES_INVERSE_SQUARED_FALLOFF

_GAME_EXPORT_TREE_ROOT_BIT=1

def infer_bundle_root(scene_path:Path, configured_root:str, optional_features:int)->Path|None:
    if configured_root: return Path(configured_root)
    if optional_features&_GAME_EXPORT_TREE_ROOT_BIT:
        for ancestor in scene_path.parents:
            if ancestor.name.casefold()=="content": return ancestor.parent
    for ancestor in scene_path.parents:
        if ancestor.name.casefold()=="uescenes":
            relative=scene_path.relative_to(ancestor)
            if len(relative.parts)>=3 and relative.parts[0].casefold()=="content": return scene_path.parent
    return scene_path.parent.parent if scene_path.parent.name.casefold()=="scenes" else None

@dataclass(frozen=True, slots=True)
class UESceneMetadata:
    format_name:str; generator_name:str|None; generator_version:str|None; generator_build:str|None; source_world_path:str|None; persistent_level_path:str|None; source_game:str|None; source_engine:str|None; export_unix_time_ms_utc:int; required_features:int; optional_features:int
@dataclass(frozen=True, slots=True)
class Asset: id:int; kind:int; location:int; availability:int; path:str|None; name:str|None; uri:str|None; mime:str|None; sha256:bytes; byte_length:int; diagnostic_start:int; diagnostic_count:int; lod_start:int; lod_count:int
@dataclass(frozen=True, slots=True)
class MeshLod: index:int; uri:str; sha256:bytes; byte_length:int; bounds_min:tuple[float,float,float]; bounds_max:tuple[float,float,float]; source_screen_size:float
@dataclass(frozen=True, slots=True)
class Actor: id:int; parent_id:int; name:str|None; class_path:str|None; source_path:str|None; root_component_id:int; transform_id:int; component_start:int; component_count:int; diagnostic_start:int; diagnostic_count:int
@dataclass(frozen=True, slots=True)
class Component: id:int; actor_id:int; parent_id:int; kind:int; name:str|None; class_path:str|None; source_path:str; transform_id:int; mesh_asset_id:int; override_start:int; override_count:int; instance_set_id:int; visibility_flags:int; diagnostic_start:int; diagnostic_count:int
@dataclass(frozen=True, slots=True)
class Transform: id:int; translation:tuple[float,float,float]; rotation:tuple[float,float,float,float]; scale:tuple[float,float,float]
@dataclass(frozen=True, slots=True)
class LightRecord:
    id:int; actor_id:int; parent_component_id:int; transform_id:int; name:str; class_path:str; source_path:str; light_type:LightType; color_encoding:LightColorEncoding; light_units:LightUnits; color_rgba:tuple[int,int,int,int]; flags:int; intensity:float; attenuation_radius:float; falloff_exponent:float; inner_cone_angle_degrees:float; outer_cone_angle_degrees:float; rect_width:float; rect_height:float; light_source_angle:float; light_source_soft_angle:float; source_radius:float; soft_source_radius:float; source_length:float; barn_door_angle:float; barn_door_length:float
@dataclass(frozen=True, slots=True)
class InstanceTransform: translation:tuple[float,float,float]; rotation:tuple[float,float,float,float]; scale:tuple[float,float,float]
@dataclass(frozen=True, slots=True)
class InstanceSet: id:int; component_id:int; count:int; transforms:tuple[InstanceTransform,...]
@dataclass(frozen=True, slots=True)
class MaterialOverride: component_id:int; slot_ordinal:int; flags:int; base_material_asset_id:int; override_material_asset_id:int; parameter_payload:bytes; diagnostic_start:int; diagnostic_count:int
@dataclass(frozen=True, slots=True)
class Diagnostic: severity:int; subject_kind:int; code:int; flags:int; subject_id:int; source_path:str|None; message:str; details:bytes
@dataclass(frozen=True, slots=True)
class BundleEntry: kind:int; location:int; availability:int; uri:str|None; owner_asset_id:int; lod_index:int; sha256:bytes; byte_length:int; mime:str|None; diagnostic_start:int; diagnostic_count:int
@dataclass(frozen=True, slots=True)
class UEScenePlanComponent:
    id:int; actor_id:int; parent_id:int; name:str|None; kind:int; transform:Transform; mesh_asset:Asset; mesh_lods:tuple[MeshLod,...]; model_path:Path|None; instance_set_id:int; instance_count:int; instance_set:InstanceSet|None; material_overrides:tuple[MaterialOverride,...]; visibility_flags:int
@dataclass(frozen=True, slots=True)
class UESceneImportPlan:
    actors:tuple[Actor,...]; components:tuple[UEScenePlanComponent,...]; warnings:tuple[str,...]; provenance:UESceneMetadata; diagnostics:tuple[Diagnostic,...]; actor_transforms:tuple[Transform,...]; bundle_root:Path|None; lights:tuple[LightRecord,...]; light_transforms:tuple[Transform,...]; assets:tuple[Asset,...]; bundle_entries:tuple[BundleEntry,...]; outcome:str; required_model_failures:tuple[RequiredModelFailure,...]
    def actor_transform(self, actor:Actor)->Transform:
        return self.actor_transforms[self.actors.index(actor)]
    def light_transform(self, light:LightRecord)->Transform:
        return self.light_transforms[self.lights.index(light)]
@dataclass(frozen=True, slots=True)
class _Entry: name:bytes; version:int; flags:int; offset:int; stored_length:int; decoded_length:int; crc32:int; record_count:int

def _count(payload:bytes, entry:_Entry, extra:int=0)->int:
    if len(payload)<4: raise UESceneError("truncated chunk count")
    value=struct.unpack_from("<I",payload)[0]
    if value!=entry.record_count: raise UESceneError("chunk count does not match directory")
    return value
def _str(strings:tuple[str,...], ident:int, required:bool=False)->str|None:
    if not ident:
        if required: raise UESceneError("required string reference is absent")
        return None
    if ident>len(strings): raise UESceneError("string ID is out of range")
    return strings[ident-1]
def _required_uri(strings:tuple[str,...], ident:int)->str:
    value=_str(strings,ident,True)
    _validate_uri(value)
    return value
def _filesystem_path(path:Path)->str:
    value=os.fspath(path)
    if os.name!="nt" or not os.path.isabs(value) or value.startswith("\\\\?\\"): return value
    return "\\\\?\\UNC\\"+value[2:] if value.startswith("\\\\") else "\\\\?\\"+value
def _read_file(path:Path)->tuple[int,bytes]|None:
    try:
        value=_filesystem_path(path)
        with open(value,"rb") as stream: data=stream.read()
        return os.stat(value).st_size,data
    except OSError: return None
def _source_path(strings:tuple[str,...], ident:int, required:bool)->str|None:
    value=_str(strings,ident,required)
    if value is not None and len(value.encode("utf-8"))>_MAX_SOURCE_PATH_BYTES: raise UESceneError("source path exceeds 16384 UTF-8 bytes")
    return value
def _range(start:int,count:int,total:int,label:str)->None:
    if start+count<start or start+count>total: raise UESceneError(f"{label} range is out of bounds")
def _finite(values:tuple[float,...], label:str)->None:
    if not all(isfinite(v) for v in values): raise UESceneError(f"{label} contains non-finite values")
def _owned_ranges(ranges, total:int, label:str)->None:
    covered=[False]*total
    for start,count in ranges:
        _range(start,count,total,label)
        for index in range(start,start+count):
            if covered[index]: raise UESceneError(f"{label} ranges overlap")
            covered[index]=True
    if not all(covered): raise UESceneError(f"{label} table contains unowned records")
def _nonoverlapping_ranges(ranges, total:int, label:str)->None:
    covered=[False]*total
    for start,count in ranges:
        _range(start,count,total,label)
        for index in range(start,start+count):
            if covered[index]: raise UESceneError(f"{label} ranges overlap")
            covered[index]=True
def _subject_id(domain:str, identity:bytes)->int:
    value=int.from_bytes(sha256(b"UESceneV1\0"+domain.encode("ascii")+b"\0"+identity).digest()[:8],"little")
    if not value: raise UESceneError("zero diagnostic subject ID")
    return value
def _mato_subject_id(component_id:int, slot:int)->int: return _subject_id("mato",struct.pack("<QI",component_id,slot))
def _bndx_subject_id(kind:int, uri:str|None, owner:int, lod:int)->int: return _subject_id("bndx",struct.pack("<BQI",kind,owner,lod)+(uri or "").encode("utf-8"))
def _validate_diagnostics(assets,actors,components,sets,overrides,entries,diagnostics)->None:
    owners=((assets,1,lambda a:a.id,lambda a:(a.diagnostic_start,a.diagnostic_count),"asset"),
            (actors,2,lambda a:a.id,lambda a:(a.diagnostic_start,a.diagnostic_count),"actor"),
            (components,3,lambda c:c.id,lambda c:(c.diagnostic_start,c.diagnostic_count),"component"),
            (overrides,5,lambda o:_mato_subject_id(o.component_id,o.slot_ordinal),lambda o:(o.diagnostic_start,o.diagnostic_count),"MATO"),
            (entries,6,lambda e:_bndx_subject_id(e.kind,e.uri,e.owner_asset_id,e.lod_index),lambda e:(e.diagnostic_start,e.diagnostic_count),"BNDX"))
    all_ranges=[]
    for table,kind,ident,rng,label in owners:
        all_ranges.extend(rng(item) for item in table)
        for item in table:
            subject=ident(item); start,count=rng(item)
            if any(d.subject_kind!=kind or d.subject_id!=subject for d in diagnostics[start:start+count]): raise UESceneError(f"{label} diagnostic range has foreign subject")
            if sum(d.subject_kind==kind and d.subject_id==subject for d in diagnostics)!=count: raise UESceneError(f"{label} diagnostic count mismatch")
    _nonoverlapping_ranges(all_ranges,len(diagnostics),"diagnostic")
    for asset in assets:
        if asset.availability!=1 and asset.diagnostic_count==0: raise UESceneError("unavailable asset requires an exact asset diagnostic")
    valid={1:{a.id for a in assets},2:{a.id for a in actors},3:{c.id for c in components},4:{s.id for s in sets},5:{_mato_subject_id(o.component_id,o.slot_ordinal) for o in overrides},6:{_bndx_subject_id(e.kind,e.uri,e.owner_asset_id,e.lod_index) for e in entries}}
    for d in diagnostics:
        if d.subject_kind==0:
            if d.subject_id: raise UESceneError("scene diagnostic has nonzero subject")
        elif d.subject_kind==4 and d.subject_id not in {s.id for s in sets}:
            raise UESceneError("instance set diagnostic subject is missing")
        elif d.subject_id not in valid[d.subject_kind]: raise UESceneError("diagnostic subject is missing")

@dataclass(frozen=True, slots=True)
class UEScene:
    metadata:UESceneMetadata; strings:tuple[str,...]; assets:tuple[Asset,...]; lods:tuple[MeshLod,...]; actors:tuple[Actor,...]; components:tuple[Component,...]; transforms:tuple[Transform,...]; instance_sets:tuple[InstanceSet,...]; lights:tuple[LightRecord,...]; material_overrides:tuple[MaterialOverride,...]; diagnostics:tuple[Diagnostic,...]; bundle_entries:tuple[BundleEntry,...]
    @classmethod
    def from_file(cls,path:Path)->"UEScene":
        try:
            if path.stat().st_size>_MAX_FILE_SIZE: raise UESceneError("physical file exceeds 2 GiB")
            return cls.from_bytes(path.read_bytes())
        except OSError as e: raise UESceneError(f"cannot read UEScene file: {path}") from e
    @classmethod
    def from_bytes(cls,data:bytes)->"UEScene":
        entries=_entries(data); chunks={}
        for e in entries:
            payload=data[e.offset:e.offset+e.stored_length]
            if zlib.crc32(payload)&0xffffffff!=e.crc32: raise UESceneError("chunk CRC does not match")
            if e.name not in _KNOWN:
                if e.flags: raise UESceneError("unknown chunk has required flags")
                continue
            if e.name in chunks: raise UESceneError("duplicate known chunk")
            if e.version!=1: raise UESceneError("unsupported known chunk version")
            chunks[e.name]=(e,payload)
        missing=_REQUIRED-set(chunks)
        if missing: raise UESceneError("missing required V1 chunk")
        strings=_strings(*chunks[b"STRS"]); meta=_meta(chunks[b"META"][1],strings)
        if meta.required_features&~3: raise UESceneError("unknown required feature bits")
        has_inst=b"INST" in chunks
        if bool(meta.required_features&1)!=has_inst: raise UESceneError("INST feature and chunk disagree")
        has_lite=b"LITE" in chunks
        if bool(meta.optional_features&2)!=has_lite: raise UESceneError("LITE feature and chunk disagree")
        assets,lods=_assets(*chunks[b"ASST"],strings); actors=_actors(*chunks[b"ACTR"],strings); comps=_components(*chunks[b"COMP"],strings); transforms=_transforms(*chunks[b"XFRM"]); sets=_instances(*chunks[b"INST"],strings) if has_inst else ()
        lights=_lights(*chunks[b"LITE"],strings) if has_lite else ()
        mato=_mato(*chunks[b"MATO"]); diagnostics=_diag(*chunks[b"DIAG"],strings); bndx=_bndx(*chunks[b"BNDX"],strings)
        scene=cls(meta,strings,assets,lods,actors,comps,transforms,sets,lights,mato,diagnostics,bndx); scene._validate(); return scene
    def _validate(self)->None:
        aset={x.id:x for x in self.assets}; actors={x.id:x for x in self.actors}; comps={x.id:x for x in self.components}; xfrms={x.id:x for x in self.transforms}; sets={x.id:x for x in self.instance_sets}
        ids=(tuple(x.id for x in self.assets),tuple(x.id for x in self.actors),tuple(x.id for x in self.components),tuple(x.id for x in self.transforms),tuple(x.id for x in self.instance_sets))
        if any(0 in values or len(values)!=len(set(values)) for values in ids): raise UESceneError("IDs must be nonzero and unique")
        for a in self.assets:
            _range(a.lod_start,a.lod_count,len(self.lods),"asset LOD");
            if a.lod_count>_MAX_LODS_PER_ASSET: raise UESceneError("asset LOD count exceeds 64")
            if a.kind==1 and [self.lods[a.lod_start+i].index for i in range(a.lod_count)]!=list(range(a.lod_count)): raise UESceneError("StaticMesh LODs must be contiguous")
            if a.kind!=1 and (a.lod_start or a.lod_count): raise UESceneError("non-mesh asset has LODs")
            _range(a.diagnostic_start,a.diagnostic_count,len(self.diagnostics),"asset diagnostic")
            if a.location==3: raise UESceneError("Embedded assets are unsupported in V1")
            if a.uri: _validate_uri(a.uri)
        _owned_ranges(((a.lod_start,a.lod_count) for a in self.assets if a.kind==1),len(self.lods),"asset LOD")
        for lod in self.lods:
            if lod.uri: _validate_uri(lod.uri)
        for entry in self.bundle_entries:
            if entry.uri: _validate_uri(entry.uri)
        def acyclic(table, parent, label):
            for item in table.values():
                seen=set(); current=item
                while parent(current):
                    ident=parent(current)
                    if ident in seen or ident not in table: raise UESceneError(f"invalid {label} graph")
                    seen.add(ident); current=table[ident]
        acyclic(actors,lambda a:a.parent_id,"actor")
        acyclic(comps,lambda c:c.parent_id,"component")
        for a in actors.values():
            if a.transform_id not in xfrms: raise UESceneError("actor transform reference is invalid")
            _range(a.component_start,a.component_count,len(self.components),"actor component")
            _range(a.diagnostic_start,a.diagnostic_count,len(self.diagnostics),"actor diagnostic")
            owned={self.components[a.component_start+i].id for i in range(a.component_count)}
            if any(c.actor_id!=a.id for c in (self.components[a.component_start+i] for i in range(a.component_count))): raise UESceneError("actor component ownership is invalid")
            if bool(a.root_component_id)!=bool(a.component_count): raise UESceneError("actor root component presence is invalid")
            if a.component_count:
                if a.root_component_id not in owned or comps[a.root_component_id].parent_id: raise UESceneError("actor root component is invalid")
                roots={c.id for c in (self.components[a.component_start+i] for i in range(a.component_count)) if not c.parent_id}
                if roots!={a.root_component_id}: raise UESceneError("actor must have exactly one root component")
                for component_id in owned:
                    current=comps[component_id]
                    while current.parent_id: current=comps[current.parent_id]
                    if current.id!=a.root_component_id: raise UESceneError("actor component does not reach declared root")
        for c in comps.values():
            if c.actor_id not in actors or c.transform_id not in xfrms: raise UESceneError("component reference is invalid")
            if c.parent_id and (c.parent_id not in comps or comps[c.parent_id].actor_id!=c.actor_id): raise UESceneError("component parent ownership is invalid")
            if c.kind not in (1,2,3) or c.mesh_asset_id not in aset or aset[c.mesh_asset_id].kind!=1: raise UESceneError("component mesh reference is invalid")
            if aset[c.mesh_asset_id].availability!=1: raise UESceneError("component mesh must be an available static mesh")
            _range(c.diagnostic_start,c.diagnostic_count,len(self.diagnostics),"component diagnostic")
            if (c.kind==1 and c.instance_set_id) or (c.kind!=1 and c.instance_set_id not in sets): raise UESceneError("component instance set is invalid")
        _validate_lights(self.lights,actors,comps,xfrms)
        has_instanced=any(c.kind in (2,3) for c in comps.values())
        if bool(self.metadata.required_features&1)!=has_instanced: raise UESceneError("INSTANCE_SETS feature must match ISM/HISM components")
        if has_instanced != bool(sets): raise UESceneError("ISM/HISM components require exactly one instance set")
        instanced={c.id:c for c in comps.values() if c.kind in (2,3)}
        if len({c.instance_set_id for c in instanced.values()})!=len(instanced) or set(s.component_id for s in sets.values())!=set(instanced) or any(s.component_id not in instanced or instanced[s.component_id].instance_set_id!=s.id for s in sets.values()): raise UESceneError("instance set ownership is invalid")
        overrides={(o.component_id,o.slot_ordinal):o for o in self.material_overrides}
        if len(overrides)!=len(self.material_overrides): raise UESceneError("duplicate material override")
        if bool(self.metadata.required_features&2)!=any(o.parameter_payload for o in self.material_overrides): raise UESceneError("MATO parameter provenance feature disagrees")
        for o in self.material_overrides:
            if o.component_id not in comps or (o.base_material_asset_id and (o.base_material_asset_id not in aset or aset[o.base_material_asset_id].kind!=2)) or (o.override_material_asset_id and (o.override_material_asset_id not in aset or aset[o.override_material_asset_id].kind!=2)) or (not o.override_material_asset_id and not o.parameter_payload): raise UESceneError("material override reference is invalid")
        for c in comps.values():
            _range(c.override_start,c.override_count,len(self.material_overrides),"component material override")
            if any(self.material_overrides[c.override_start+i].component_id!=c.id for i in range(c.override_count)): raise UESceneError("material override ownership is invalid")
        _owned_ranges(((a.component_start,a.component_count) for a in actors.values()),len(self.components),"actor component")
        _owned_ranges(((c.override_start,c.override_count) for c in comps.values()),len(self.material_overrides),"component MATO")
        for o in self.material_overrides: _range(o.diagnostic_start,o.diagnostic_count,len(self.diagnostics),"MATO diagnostic")
        required={}; optional={}
        for a in self.assets:
            if a.availability==1:
                target=optional if a.kind==2 else required
                target[(a.id,0xffffffff)]=({1:1,2:2,3:3}[a.kind],a.uri,a.sha256,a.byte_length,a.mime,a.location)
        actual={}
        for entry in self.bundle_entries:
            key=(entry.owner_asset_id,entry.lod_index)
            source=required.get(key) or optional.get(key)
            if source is None: raise UESceneError("BNDX does not reference an available semantic resource")
            if entry.availability!=1 or (entry.kind,entry.uri,entry.sha256,entry.byte_length,entry.mime,entry.location)!=source: raise UESceneError("BNDX does not match its semantic resource")
            _range(entry.diagnostic_start,entry.diagnostic_count,len(self.diagnostics),"BNDX diagnostic")
            if key in actual: raise UESceneError("duplicate BNDX semantic resource")
            actual[key]=entry
        if not set(required).issubset(actual): raise UESceneError("BNDX is missing a semantic resource")
        _validate_diagnostics(self.assets,self.actors,self.components,self.instance_sets,self.material_overrides,self.bundle_entries,self.diagnostics)
    def build_import_plan(self,scene_path:Path,bundle_root:Path|None,strict_required_models:bool=False)->UESceneImportPlan:
        warnings=[]; failures=[]; entries={(b.owner_asset_id,b.lod_index):b for b in self.bundle_entries}; sets={s.id:s for s in self.instance_sets}; xfrms={x.id:x for x in self.transforms}; plan=[]
        for asset in self.assets:
            if asset.location==1: _resolve_uri(asset.uri,asset.location,scene_path,bundle_root)
            for lod in self.lods[asset.lod_start:asset.lod_start+asset.lod_count]:
                if asset.location==1: _resolve_uri(lod.uri,asset.location,scene_path,bundle_root)
        for entry in self.bundle_entries:
            if entry.kind in (2,3) and entry.availability==1:
                resource=_resolve_uri(entry.uri,entry.location,scene_path,bundle_root)
                contents=_read_file(resource)
                if contents is None or contents[0]!=entry.byte_length or sha256(contents[1]).digest()!=entry.sha256:
                    if entry.kind==2: warnings.append("optional material sidecar does not match BNDX")
                    else: raise UESceneError("bundle sidecar does not match BNDX")
            elif entry.location==1: _resolve_uri(entry.uri,entry.location,scene_path,bundle_root)
        for c in self.components:
            model=None; a=next(a for a in self.assets if a.id==c.mesh_asset_id)
            if c.kind in (1,2,3) and a.availability==1:
                b=entries.get((a.id,0xffffffff))
                if not b or b.kind!=1:
                    detail=f"component {c.id}: no primary Model BNDX entry"; warnings.append(detail)
                    failures.append(RequiredModelFailure(c.id,a.id,"missing",None,None,detail))
                else:
                    try: model=_resolve_uri(b.uri,b.location,scene_path,bundle_root)
                    except UESceneError as e:
                        detail=f"component {c.id}: {e}"; warnings.append(detail)
                        failures.append(RequiredModelFailure(c.id,a.id,"missing",b.uri,None,detail))
                    else:
                        contents=_read_file(model)
                        if model.suffix.lower()!=".uemodel" or contents is None:
                            detail=f"component {c.id}: model resource is unavailable"; warnings.append(detail)
                            failures.append(RequiredModelFailure(c.id,a.id,"missing",b.uri,model,detail)); model=None
                        elif contents[0]!=b.byte_length or sha256(contents[1]).digest()!=b.sha256:
                            detail=f"component {c.id}: model resource does not match BNDX"; warnings.append(detail)
                            failures.append(RequiredModelFailure(c.id,a.id,"hash_mismatch",b.uri,model,detail)); model=None
            elif c.kind in (1,2,3):
                detail=f"component {c.id}: mesh asset is unavailable"; warnings.append(detail)
                failures.append(RequiredModelFailure(c.id,a.id,"missing",a.uri,None,detail))
            s=sets.get(c.instance_set_id); overrides=tuple(self.material_overrides[c.override_start:c.override_start+c.override_count]); lods=self.lods[a.lod_start:a.lod_start+a.lod_count]; plan.append(UEScenePlanComponent(c.id,c.actor_id,c.parent_id,c.name,c.kind,xfrms[c.transform_id],a,lods,model,c.instance_set_id,s.count if s else 0,s,overrides,c.visibility_flags))
        failures=tuple(failures)
        if strict_required_models and failures: raise RequiredModelError(failures)
        return UESceneImportPlan(self.actors,tuple(plan),tuple(warnings),self.metadata,self.diagnostics,tuple(xfrms[a.transform_id] for a in self.actors),bundle_root,self.lights,tuple(xfrms[light.transform_id] for light in self.lights),self.assets,self.bundle_entries,"partial" if failures else "success",failures)

def _entries(data:bytes)->tuple[_Entry,...]:
    if len(data)>_MAX_FILE_SIZE: raise UESceneError("physical file exceeds 2 GiB")
    if len(data)<44: raise UESceneError("truncated header")
    magic,major,minor,flags,size,doff,count,fsize,crc=struct.unpack_from("<8sHHIIQIQI",data)
    if magic!=_MAGIC: raise UESceneError("bad magic")
    if (major,minor,flags,size)!=(1,0,0,44): raise UESceneError("unsupported UEScene header")
    if fsize!=len(data): raise UESceneError("file size does not match physical length")
    if zlib.crc32(data[:40])&0xffffffff!=crc: raise UESceneError("bad header CRC")
    dend=doff+count*40
    if count>_MAX_ENTRIES or dend<doff or doff<44 or dend>len(data): raise UESceneError("invalid directory")
    out=[]; ranges=[]
    for i in range(count):
        e=_Entry(*struct.unpack_from("<4sHHQQQII",data,doff+i*40))
        if any(x<32 or x>126 for x in e.name): raise UESceneError("directory FourCC must be printable ASCII")
        if e.flags or e.stored_length!=e.decoded_length: raise UESceneError("V1 chunks must be unflagged and uncompressed")
        if e.stored_length>_MAX_CHUNK_BYTES or e.decoded_length>_MAX_CHUNK_BYTES: raise UESceneError("chunk exceeds 1 GiB")
        end=e.offset+e.stored_length
        if e.offset%8 or e.offset<44 or (e.offset<dend and end>doff) or end<e.offset or end>len(data): raise UESceneError("chunk overlaps header or directory")
        out.append(e); ranges.append((e.offset,end))
    ranges.sort()
    if any(a[1]>b[0] for a,b in zip(ranges,ranges[1:])): raise UESceneError("chunk ranges overlap")
    total=0
    for e in out:
        if e.name==b"STRS": continue
        if e.record_count>_MAX_FIXED_RECORDS-total: raise UESceneError("total fixed records exceeds limit")
        total+=e.record_count
    return tuple(out)
def _strings(e:_Entry,p:bytes)->tuple[str,...]:
    n=_count(p,e);
    if n>_MAX_STRINGS: raise UESceneError("invalid STRS count")
    at=4; out=[]
    for _ in range(n):
        if at+4>len(p): raise UESceneError("truncated string length")
        size=struct.unpack_from("<I",p,at)[0]; at+=4
        if size>_MAX_STRING_BYTES or at+size>len(p): raise UESceneError("invalid string length")
        raw=p[at:at+size]; at+=size
        if b'\0' in raw: raise UESceneError("string contains NUL")
        if raw.startswith(b'\xef\xbb\xbf'): raise UESceneError("UTF-8 BOM prefix is forbidden")
        try: out.append(raw.decode("utf-8"))
        except UnicodeDecodeError as x: raise UESceneError("invalid UTF-8 string") from x
    if at!=len(p): raise UESceneError("trailing STRS data")
    return tuple(out)
def _meta(p:bytes,s:tuple[str,...])->UESceneMetadata:
    if len(p)!=68: raise UESceneError("META payload must be exactly 68 bytes")
    n,*v=struct.unpack("<I8IQ4BIQQ",p)
    if n!=1 or tuple(v[9:14])!=(1,1,1,1,0): raise UESceneError("invalid META constants")
    name=_str(s,v[0],True)
    if name!="uescene": raise UESceneError("invalid META format name")
    return UESceneMetadata(name,*(_str(s,x) for x in v[1:8]),v[8],v[14],v[15])
def _assets(e:_Entry,p:bytes,s:tuple[str,...]):
    n=_count(p,e);
    if len(p)<8: raise UESceneError("truncated ASST")
    lodn=struct.unpack_from("<I",p,4)[0]
    if n>_MAX_ASSETS or lodn>_MAX_LODS_PER_ASSET*n: raise UESceneError("ASST count exceeds limit")
    if len(p)!=8+n*84+lodn*104: raise UESceneError("invalid ASST size")
    assets=[]; at=8
    for _ in range(n):
        v=struct.unpack_from("<QBBBBIIII32sQIIII",p,at); at+=84
        if v[4] or v[1] not in (1,2,3) or v[2] not in (1,2,3) or v[3] not in (1,2,3,4): raise UESceneError("invalid asset record")
        assets.append(Asset(v[0],v[1],v[2],v[3],_str(s,v[5]),_str(s,v[6]),_required_uri(s,v[7]),_str(s,v[8]),v[9],v[10],*v[11:]))
    lods=[]
    for _ in range(lodn):
        v=struct.unpack_from("<II32sQ6dfI",p,at)
        screen_bits=struct.unpack_from("<I",p,at+96)[0]; at+=104
        if v[-1] or not all(isfinite(x) for x in v[4:10]) or any(v[4+i]>v[7+i] for i in range(3)) or (not isfinite(v[10]) and screen_bits!=0x7fc00000): raise UESceneError("invalid LOD bounds or screen size")
        lods.append(MeshLod(v[0],_required_uri(s,v[1]),v[2],v[3],v[4:7],v[7:10],v[10]))
    return tuple(assets),tuple(lods)
def _actors(e,p,s):
    n=_count(p,e)
    if n>_MAX_ACTORS: raise UESceneError("ACTR count exceeds limit")
    if len(p)!=4+n*64: raise UESceneError("invalid ACTR size")
    out=[]
    for i in range(n):
        v=struct.unpack_from("<QQIIIQQIIIII",p,4+i*64)
        if v[9]: raise UESceneError("actor flags are reserved")
        out.append(Actor(v[0],v[1],_str(s,v[2]),_str(s,v[3]),_source_path(s,v[4],True),v[5],v[6],v[7],v[8],v[10],v[11]))
    return tuple(out)
def _components(e,p,s):
    n=_count(p,e)
    if n>_MAX_COMPONENTS: raise UESceneError("COMP count exceeds limit")
    if len(p)!=4+n*88: raise UESceneError("invalid COMP size")
    out=[]
    for i in range(n):
        v=struct.unpack_from("<QQQB3xIIIQQIIQIIII",p,4+i*88)
        if v[-1]: raise UESceneError("component reserved field is nonzero")
        out.append(Component(v[0],v[1],v[2],v[3],_str(s,v[4]),_str(s,v[5]),_source_path(s,v[6],True),v[7],v[8],v[9],v[10],v[11],v[12],v[13],v[14]))
    return tuple(out)
def _transforms(e,p):
    n=_count(p,e)
    if n>_MAX_TRANSFORMS: raise UESceneError("XFRM count exceeds limit")
    if len(p)!=4+n*64: raise UESceneError("invalid XFRM size")
    out=[]
    for i in range(n):
        v=struct.unpack_from("<Q3d4f3fI",p,4+i*64)
        if v[-1]: raise UESceneError("transform reserved field is nonzero")
        _finite(v[1:-1],"transform"); q=v[4:8]
        if abs(sqrt(sum(x*x for x in q))-1)>1e-4: raise UESceneError("transform quaternion is not normalized")
        out.append(Transform(v[0],v[1:4],q,v[8:11]))
    return tuple(out)
def _lights(e,p,s):
    n=_count(p,e)
    if not n: raise UESceneError("LITE must be nonempty when present")
    if n>_MAX_LIGHTS: raise UESceneError("LITE count exceeds limit")
    if len(p)!=4+n*116: raise UESceneError("invalid LITE size")
    out=[]
    for i in range(n):
        at=4+i*116; v=struct.unpack_from("<QQQQIIIBBBB4sI7fI7f",p,at)
        float_bits=tuple(struct.unpack_from("<I",p,at+offset)[0] for offset in (56,60,64,68,72,76,80,88,92,96,100,104,108,112))
        if v[10] or v[20]: raise UESceneError("LITE reserved field is nonzero")
        if any(bits==0x80000000 for bits in float_bits): raise UESceneError("LITE negative zero float is noncanonical")
        try: light_type=LightType(v[7]); color_encoding=LightColorEncoding(v[8]); light_units=LightUnits(v[9])
        except ValueError as x: raise UESceneError("invalid LITE enum") from x
        out.append(LightRecord(v[0],v[1],v[2],v[3],_str(s,v[4],True),_str(s,v[5],True),_source_path(s,v[6],True),light_type,color_encoding,light_units,tuple(v[11]),v[12],*v[13:20],*v[21:]))
    return tuple(out)
def _validate_lights(lights,actors,comps,xfrms):
    ids=tuple(light.id for light in lights)
    if 0 in ids or len(ids)!=len(set(ids)): raise UESceneError("light IDs must be nonzero and unique")
    actor_order={ident:index for index,ident in enumerate(actors)}; component_order={ident:index for index,ident in enumerate(comps)}; transform_order={ident:index for index,ident in enumerate(xfrms)}
    previous=(-1,-1,-1,0)
    for light in lights:
        if light.actor_id not in actors: raise UESceneError("light actor reference is invalid")
        if light.parent_component_id and (light.parent_component_id not in comps or comps[light.parent_component_id].actor_id!=light.actor_id): raise UESceneError("light parent component reference is invalid")
        if light.transform_id not in xfrms: raise UESceneError("light transform reference is invalid")
        if light.flags&~LightFlags.ALLOWED_MASK: raise UESceneError("invalid LITE flags")
        values=(light.intensity,light.attenuation_radius,light.falloff_exponent,light.inner_cone_angle_degrees,light.outer_cone_angle_degrees,light.rect_width,light.rect_height,light.light_source_angle,light.light_source_soft_angle,light.source_radius,light.soft_source_radius,light.source_length,light.barn_door_angle,light.barn_door_length)
        if not all(isfinite(value) and value>=0 for value in values): raise UESceneError("invalid LITE physical value")
        attenuation=bool(light.flags&LightFlags.HAS_ATTENUATION); spot=bool(light.flags&LightFlags.HAS_SPOT_ANGLES); rect=bool(light.flags&LightFlags.HAS_RECT_DIMENSIONS); inverse_squared=bool(light.flags&LightFlags.USES_INVERSE_SQUARED_FALLOFF)
        point_or_spot=light.light_type in (LightType.POINT,LightType.SPOT)
        if (attenuation and light.light_type not in (LightType.POINT,LightType.SPOT,LightType.RECT)) or (not attenuation and point_or_spot) or spot!=(light.light_type==LightType.SPOT) or rect!=(light.light_type==LightType.RECT) or (inverse_squared and not point_or_spot): raise UESceneError("LITE flags/type mismatch")
        if not attenuation and (light.attenuation_radius or light.falloff_exponent): raise UESceneError("LITE attenuation values are invalid")
        if (not spot and (light.inner_cone_angle_degrees or light.outer_cone_angle_degrees)) or (spot and light.inner_cone_angle_degrees>light.outer_cone_angle_degrees): raise UESceneError("LITE spot angles are invalid")
        if not rect and (light.rect_width or light.rect_height or light.barn_door_angle or light.barn_door_length): raise UESceneError("LITE rect values are invalid")
        if light.light_type!=LightType.DIRECTIONAL and (light.light_source_angle or light.light_source_soft_angle): raise UESceneError("LITE directional values are invalid")
        if not point_or_spot and (light.source_radius or light.soft_source_radius or light.source_length): raise UESceneError("LITE point/spot values are invalid")
        current=(actor_order[light.actor_id],-1 if not light.parent_component_id else component_order[light.parent_component_id],transform_order[light.transform_id],light.id)
        if current<previous: raise UESceneError("LITE records are not in canonical order")
        previous=current
def _instances(e,p,s):
    n=_count(p,e)
    if len(p)<8: raise UESceneError("truncated INST")
    tn=struct.unpack_from("<I",p,4)[0]
    if n>_MAX_INSTANCE_SETS or tn>_MAX_INSTANCE_TRANSFORMS: raise UESceneError("INST count exceeds limit")
    if len(p)!=8+n*32+tn*40: raise UESceneError("invalid INST size")
    records=[]
    for i in range(n):
        v=struct.unpack_from("<QQIIII",p,8+i*32)
        if v[3] or v[2]!=v[5]: raise UESceneError("invalid instance set")
        _range(v[4],v[5],tn,"instance transform"); records.append(v)
    _owned_ranges(((v[4],v[5]) for v in records),tn,"instance transform")
    transforms=[]
    for i in range(tn):
        v=struct.unpack_from("<3f4f3f",p,8+n*32+i*40); _finite(v,"instance transform")
        q=v[3:7]
        if abs(sqrt(sum(x*x for x in q))-1)>1e-4: raise UESceneError("instance transform quaternion is not normalized")
        transforms.append(InstanceTransform(v[:3],q,v[7:]))
    return tuple(InstanceSet(v[0],v[1],v[2],tuple(transforms[v[4]:v[4]+v[5]])) for v in records)
def _blob_chunk(e,p,record_size,label):
    n=_count(p,e)
    if len(p)<8: raise UESceneError(f"truncated {label}")
    blob=struct.unpack_from("<I",p,4)[0]
    if blob>_MAX_BLOB_BYTES: raise UESceneError(f"{label} blob exceeds limit")
    if len(p)!=8+n*record_size+blob: raise UESceneError(f"invalid {label} size")
def _mato(e,p):
    _blob_chunk(e,p,56,"MATO"); n=struct.unpack_from("<I",p)[0]; blob=p[8+n*56:]; out=[]
    if n>_MAX_OVERRIDES: raise UESceneError("MATO count exceeds limit")
    for i in range(n):
        v=struct.unpack_from("<QIIQQQIIII",p,8+i*56)
        if v[-1] or v[2]&~1 or (not v[2] and (v[5] or v[6])): raise UESceneError("invalid MATO record")
        _range(v[5],v[6],len(blob),"MATO payload"); payload=blob[v[5]:v[5]+v[6]]
        if v[2] and (len(payload)<8 or len(payload)>_MAX_OPAQUE_BYTES+8 or struct.unpack_from("<HHI",payload)!=(1,0,len(payload)-8)): raise UESceneError("invalid MATO payload")
        out.append(MaterialOverride(v[0],v[1],v[2],v[3],v[4],payload,v[7],v[8]))
    return tuple(out)
def _diag(e,p,s):
    _blob_chunk(e,p,40,"DIAG"); n=struct.unpack_from("<I",p)[0]; blob=p[8+n*40:]; out=[]
    if n>_MAX_DIAGNOSTICS: raise UESceneError("DIAG count exceeds limit")
    for i in range(n):
        v=struct.unpack_from("<BBHIQIIQII",p,8+i*40)
        if v[-1] or v[0] not in (1,2,3) or v[1] not in range(7) or not 1<=v[2]<=10 or v[3]&~1 or (not v[3] and (v[7] or v[8])): raise UESceneError("invalid DIAG record")
        _range(v[7],v[8],len(blob),"DIAG details"); details=blob[v[7]:v[7]+v[8]]
        if v[3] and (len(details)<8 or len(details)>_MAX_OPAQUE_BYTES+8 or struct.unpack_from("<HHI",details)!=(1,0,len(details)-8)): raise UESceneError("invalid DIAG details")
        out.append(Diagnostic(v[0],v[1],v[2],v[3],v[4],_source_path(s,v[5],False),_str(s,v[6],True),details))
    return tuple(out)
def _bndx(e,p,s):
    n=_count(p,e)
    if len(p)!=4+n*72: raise UESceneError("invalid BNDX size")
    out=[]
    for i in range(n):
        v=struct.unpack_from("<BBBBIQI32sQIII",p,4+i*72)
        if v[3] or v[0] not in (1,2,3) or v[1] not in (1,2,3) or v[2] not in (1,2,3,4): raise UESceneError("invalid BNDX record")
        out.append(BundleEntry(v[0],v[1],v[2],_required_uri(s,v[4]),v[5],v[6],v[7],v[8],_str(s,v[9]),v[10],v[11]))
    if len({(x.kind,x.uri,x.owner_asset_id,x.lod_index) for x in out})!=len(out): raise UESceneError("duplicate BNDX entry")
    return tuple(out)
def _resolve_uri(uri:str|None,location:int,scene:Path,bundle:Path|None)->Path:
    if not uri: raise UESceneError("model URI is absent")
    if location==2:
        if bundle is None: raise UESceneError("Bundle URI requires bundle root")
        root=bundle
    elif location==1: root=scene.parent
    else: raise UESceneError("Embedded URI is unsupported")
    _validate_uri(uri)
    candidate=(root / Path(*uri.split("/"))).resolve(); resolved=root.resolve()
    try: candidate.relative_to(resolved)
    except ValueError as e: raise UESceneError("model URI escapes its root") from e
    return candidate
def _validate_uri(uri:str)->None:
    if len(uri.encode("utf-8"))>_MAX_URI_BYTES or "\0" in uri or "\\" in uri or ":" in uri or uri.startswith(("/","//")) or any(p in ("", ".", "..") for p in uri.split("/")): raise UESceneError("unsafe model URI")
