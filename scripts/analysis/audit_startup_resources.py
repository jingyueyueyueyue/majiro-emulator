"""Isolated original/local startup resource audit through the independent Lua VM.

No Android/device operations, Windows EXE execution, old runtime, or game writes.
Graphics transport is a metadata model, so reaching a source UI wait proves only
control/resource startup, never visual title correctness or complete gameplay.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import io
import json
from pathlib import Path
import re
import struct
import sys
import time
import uuid
from functools import lru_cache

import soundfile
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"build"))
from deploy_player_android import payload
from majiro_arc import Archive
from majiro_mjo import Script
from majiro_patch import Patch
from majiro_image import Decoder,image_key
from port_common import ROOT,archive_dir,read_json,sha256,timestamp,write_json
from lua_bridge import create_lua,table_value


@lru_cache(maxsize=1)
def production_text_plans():
    """Compile the real Java manifest/mode/isolation policy once for this run."""
    from run_text_resource_candidate import java_build
    samples=read_json(ROOT/"config/player_samples.json")["games"]+read_json(ROOT/"config/player_new_samples.json")["games"]
    folder=archive_dir("startup_text_policy")
    plans,_=java_build(ROOT,folder,read_json(ROOT/"reports/android_toolchain.json"),samples)
    return {str(Path(row["root"]).resolve()):row for row in plans["games"]}


def key(value):
    return value.replace("\\","/").translate(str.maketrans(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"+"\uff21\uff22\uff23\uff24\uff25\uff26\uff27\uff28\uff29\uff2a\uff2b\uff2c\uff2d\uff2e\uff2f\uff30\uff31\uff32\uff33\uff34\uff35\uff36\uff37\uff38\uff39\uff3a",
        "abcdefghijklmnopqrstuvwxyz"+"\uff41\uff42\uff43\uff44\uff45\uff46\uff47\uff48\uff49\uff4a\uff4b\uff4c\uff4d\uff4e\uff4f\uff50\uff51\uff52\uff53\uff54\uff55\uff56\uff57\uff58\uff59\uff5a"))


class Resource:
    def __init__(self,path=None,member=None):
        self.path,self.member=path,member
        self.name=member.name if member else path.name
        self.size=member.size if member else path.stat().st_size
    def read(self):
        return self.member.read()if self.member else self.path.read_bytes()
    @property
    def extension(self):
        return Path(self.name).suffix.lower()
    @property
    def identity(self):
        return self.member.identity if self.member else "loose:"+str(self.path)
    def head(self,count=64):
        if self.member:
            with self.member.archive.open("rb")as stream:
                stream.seek(self.member.offset);return stream.read(min(count,self.size))
        with self.path.open("rb")as stream:return stream.read(count)


class Startup:
    def __init__(self,game,files,mode,maximum=1500000,seconds=25):
        self.game,self.files,self.requested_mode=game,files,mode
        self.maximum,self.seconds=maximum,seconds
        self.lua=create_lua();self.clock=0;self.system=None;self.vm=None
        self.base={};self.resources={};self.loose={};self.archive_rows=[]
        policy=production_text_plans()[str(Path(game["directory"]).resolve())]
        self.local_unavailable=mode=="local"and policy["auto"]!="local"
        # Existing local preferences use original scripts when no local layer
        # exists. Keep that effective mode explicit in the startup evidence.
        self.mode="original"if self.local_unavailable else mode
        selected=next(row for row in policy["modes"]if row["mode"]==self.mode)
        self.manifest=dict(selected["configuration"])
        self.manifest["script_layers"]=selected["layers"]
        self.manifest["archives"]=[row["path"]for row in selected["archives"]if row["path"].startswith("patches/")]
        self.selected_archives=selected["archives"]
        self.archive_scopes={key(row["path"]):row["scope"]for row in selected["archives"]}
        self.duplicate_archives=set(map(key,policy["duplicates"]))
        self.image_only_archives={name for name,scope in self.archive_scopes.items()if scope=="images"}
        self.patch=Patch()if self.manifest.get("raw_metadata")else None
        self.counts=Counter();self.resource_reads=[];self.host_events=[];self.derived=set();self.writes={}
        self.audio_cache={};self.image_cache={};self.durations={};self.dialogs=[]
        self.active_archives=[];self.profile_hashes=[]
        self.script_cache={}
        self.decoded_images={}
        self.translation_cache={}
        self.translation_submits=0
        self.text_snapshot={};self.text_sealed=False;self.text_identity=None
        self.vm_encoding=self.manifest.get("vm_encoding","cp932")
        self._index()

    def _index(self):
        archives=[row["path"]for row in self.selected_archives]
        seen=set()
        for name in archives:
            if key(name)in seen:continue
            seen.add(key(name))
            if name not in self.files:raise ValueError("declared archive missing from deployment: "+name)
            self.archive_rows.append((name,Archive(self.files[name])))
        self.active_archives=list(self.archive_rows)
        self._refresh()
        recognized={".mjo",".cfg",".env",".png",".rct",".rc8",".wav",".ogg",".wma",".dat",".txt"}
        for name,path in self.files.items():
            if "/"not in name and path.suffix.lower()in recognized:
                item=Resource(path=path);item.scope="original";item.source_path="loose/"+key(name)
                self.loose[key(name)]=item
        self.resources.update(self.loose)
        self._overlays()
        for name,path in self.files.items():
            if path.suffix.lower()==".exe"and("/"not in key(name)or(key(name).startswith("bin/")and key(name).count("/")==1)):
                if path.stat().st_size<=128*1024*1024:self.profile_hashes.append(sha256(path))

    def _overlays(self):
        if self.mode=="local":
            for layer in reversed(self.manifest.get("script_layers",[])):
                prefix=layer["directory"]+"/"
                for name,path in self.files.items():
                    if name.startswith(prefix):
                        basename=name[len(prefix):]
                        if "/"not in basename and(layer["kind"]=="pinned_raw_patch"or basename.lower().endswith(".mjo")or
                                                  layer["kind"]=="encoded_text"and Path(basename).suffix.lower()in(".cfg",".env",".txt")):
                            item=Resource(path=path);item.pinned=layer["kind"]=="pinned_raw_patch"
                            item.scope=layer["encoding"]if layer["kind"]=="encoded_text"else layer["kind"]
                            item.source_path=prefix+key(basename)
                            self.resources[key(basename)]=item

    def _refresh(self):
        self.base={}
        for name,archive in self.active_archives:
            for member in archive.members:
                if key(name)in self.image_only_archives and member.extension not in(".png",".bmp",".rct",".rc8"):continue
                item=Resource(member=member);item.scope=self.archive_scopes.get(key(name),"original")
                item.source_path=name+"/"+key(member.name)
                self.base.setdefault(key(member.name),item)
        self.resources=dict(self.base)

    def archive(self,action,name):
        self.host_events.append({"kind":"archive","action":action,"name":name})
        if action=="release":
            self.active_archives=[row for row in self.active_archives if key(row[0])!=key(name)]
        elif action=="register":
            if key(name)in self.duplicate_archives:return 0
            if key(name).startswith("patches/")and key(name)not in self.archive_scopes:
                raise ValueError("dynamic archive outside active text mode")
            existing=next((row for row in self.archive_rows if key(row[0])==key(name)),None)
            if not existing:
                source=next((path for filename,path in self.files.items()if key(filename)==key(name)),None)
                if not source:
                    # Current production openArchive returns ABSENT; its native
                    # caller treats that as an optional registration no-op.
                    native=(ROOT/"player/android/game_host.cpp").read_text(encoding="utf8")
                    if 'if(row=="ABSENT"){lua_pushinteger(state,0);return 1;}'not in native:
                        raise ValueError("dynamic archive resource missing from deployment: "+name)
                    self.host_events.append({"kind":"optional_archive_absent","name":name,
                                             "production_rule":"openArchive ABSENT -> register result 0"})
                    return 0
                existing=(name,Archive(source));self.archive_rows.append(existing)
            self.active_archives=[row for row in self.active_archives if key(row[0])!=key(name)]
            self.active_archives.insert(0,existing)
        else:raise RuntimeError("unmodeled archive operation: "+action)
        self._refresh();self.resources.update(self.loose);self._overlays()
        self.image_cache.clear();self.audio_cache.clear()
        self.decoded_images.clear()
        return 0

    def module(self,path):
        actual=ROOT/("player/"+path if path.startswith("runtime/")else"player/tables/"+Path(path).name)
        return actual.read_text(encoding="utf8")

    def engine_requirements(self):
        from production_engine_features import features
        detected=features(path for name,path in self.files.items()
                          if path.suffix.lower()==".exe"and("/"not in key(name)or(key(name).startswith("bin/")and key(name).count("/")==1)))
        return self.lua.table_from({"family":"Majiro",
            "archive_version":max(arc.version for name,arc in self.archive_rows if key(name)not in self.image_only_archives),
            "script_signature":self.resource("start.mjo").read()[:16].rstrip(b"\0").decode("ascii"),
            "features":detected},recursive=True)

    def game_text(self,name):
        name=key(name)
        path=next((path for candidate,path in self.files.items()if key(candidate)==name),None)
        return path.read_bytes()if path else None

    def resource(self,name):
        try:return self.resources[key(name)]
        except KeyError:raise ValueError("resource missing: "+name)from None

    def raw(self,name):
        item=self.resource(name)
        raw=item.read()
        if item.extension in(".mjo",".cfg",".env",".txt"):
            sha=hashlib.sha256(raw).hexdigest()
            stamp=item.scope+"\n"+item.source_path+"\n"+sha
            normalized=key(name)
            if self.text_sealed or normalized in self.text_snapshot:
                if self.text_snapshot.get(normalized)!=stamp:raise ValueError("text resource changed after preflight: "+name)
            self.lua.globals()["PLAYER_VERIFY_TEXT_RESOURCE"](normalized,sha,item.scope,item.source_path)
            self.text_snapshot[normalized]=stamp
        if getattr(item,"pinned",False):
            expected=next((m.sha256 for m in self.patch.members if key(m.name)==key(name)),None)
            if hashlib.sha256(raw).hexdigest()!=expected:raise ValueError("raw patch source hash changed: "+name)
        return item,raw

    def resource_text(self,name):
        item,raw=self.raw(name)
        codec={"original":"cp932","source_mjo":"cp932","cp932":"cp932","cp936":"gbk","utf-8":"utf8"}.get(item.scope)
        return self.patch.decode(raw)if getattr(item,"pinned",False)else raw.decode(codec or "untyped-local")

    def text_resource_names(self):
        return self.lua.table_from(sorted(name for name,item in self.resources.items()
                                         if item.extension in(".mjo",".cfg",".env",".txt")))

    def patch_resource_names(self):
        rows=[]
        for layer in self.manifest.get("script_layers",[]):
            prefix=layer["directory"]+"/"
            for name in self.files:
                if name.startswith(prefix)and"/"not in name[len(prefix):]and Path(name).suffix.lower()in(".mjo",".cfg",".env",".txt"):
                    rows.append(layer["directory"]+"\t"+key(name[len(prefix):]))
        return self.lua.table_from(rows)

    def seal_text_resources(self):
        assert self.text_snapshot and "start.mjo"in self.text_snapshot
        self.text_sealed=True

    def set_text_identity(self,identity,encoding):
        assert self.text_sealed and re.fullmatch("[0-9a-f]{64}",identity)
        assert self.text_identity is None or self.text_identity==identity
        self.text_identity=identity;self.vm_encoding=encoding

    def script(self,name):
        normalized=key(name)
        item,raw=self.raw(name)
        pinned=getattr(item,"pinned",False)
        sha=hashlib.sha256(raw).hexdigest()
        codec={"original":"cp932","source_mjo":"cp932","cp932":"cp932","cp936":"gbk","utf-8":"utf8"}.get(item.scope)
        parsed=Script(item.name,raw,string_decoder=self.patch.decode if pinned else lambda b:b.decode(codec or "untyped-local"),
                      address_repairs=self.patch.repairs(item.name,sha)if pinned else None)
        data=parsed.runtime_data()
        if pinned:data["patch_source"]=True
        data.update(text_origin=item.scope,text_encoding="cp932-tunnel"if pinned else codec,resource_path=item.source_path)
        result=self.lua.table_from(data,recursive=True)
        self.script_cache[normalized]=result
        self.resource_reads.append({"kind":"script","name":item.name,"sha256":sha})
        return result

    def image(self,requested):
        stem=key(requested)
        for extension in(".png",".bmp",".rct",".rc8"):
            if stem.endswith(extension):
                stem=stem[:-4];break
        for candidate in(stem+".png",stem+".bmp",stem+".rct",stem+".rc8",requested):
            if key(candidate)in self.resources:return self._image(candidate)
        if stem.endswith("_")and stem[:-1]+".png"in self.resources:
            image=dict(self._image(stem[:-1]+".png"));image.update(name=stem+".rc8",file=stem+".rc8",depth=8,source_depth=8)
            self.derived.add(image["name"]);return image
        # Original page_create_file uses a 1x1 empty page for missing optional
        # UI art. This is distinct from a corrupt existing resource.
        indexed=stem.endswith("_")
        return{"name":stem+(".rc8"if indexed else".rct"),"file":stem+(".rc8"if indexed else".rct"),
               "width":1,"height":1,"depth":8 if indexed else 32,"source_depth":8 if indexed else 32}

    def _image(self,name):
        name=key(name)
        if name in self.image_cache:return self.image_cache[name]
        item=self.resource(name);raw=item.head()
        if raw.startswith(b"\x89PNG\r\n\x1a\n"):
            if len(raw)<33:raise ValueError("truncated PNG: "+name)
            w,h=struct.unpack_from(">II",raw,16);kind="PNG"
        elif raw.startswith(b"\x98\x5a\x92\x9a"):
            w,h=struct.unpack_from("<II",raw,8);kind=raw[4:8].decode("ascii")
            if kind not in("8_00","TC00","TC01","TS00","TS01"):raise ValueError("unsupported image format: "+kind)
        else:raise ValueError("unsupported image format: "+name)
        if not(0<w<=32768 and 0<h<=32768):raise ValueError("image dimensions: "+name)
        result={"name":name,"file":name,"width":w,"height":h,"depth":8 if name.endswith(".rc8")else 32,
                "source_depth":8 if name.endswith(".rc8")else 32}
        self.image_cache[name]=result;return result

    def member_names(self):
        names=[item.name for item in self.resources.values()]
        for name,item in list(self.resources.items()):
            if name.endswith(".png"):
                raw=item.head()
                # Same IHDR alpha-family rule; tRNS-only PNG aliases are still
                # checked lazily by image() if requested.
                if len(raw)>=26 and raw[25]in(4,6):
                    alias=name[:-4]+"_.rc8"
                    if alias not in self.resources:names.append(alias)
        return self.lua.table_from(names)

    def media(self,name):
        if name in self.audio_cache:return self.audio_cache[name]
        item=self.resource(name)
        try:
            header=item.head(256)
            if header[:4]==b"RIFF"and header[8:12]==b"WAVE":
                at=12;rate=None;size=None
                while at+8<=len(header):
                    kind=header[at:at+4];length=struct.unpack_from("<I",header,at+4)[0]
                    if kind==b"fmt "and at+24<=len(header):rate=struct.unpack_from("<I",header,at+16)[0]
                    if kind==b"data":size=length;break
                    at+=8+length+(length%2)
                if rate and size is not None:
                    result={"name":name,"file":name,"duration_ms":size*1000/rate,
                            "sample_rate":struct.unpack_from("<I",header,24)[0]}
                    self.audio_cache[name]=self.lua.table_from(result);return self.audio_cache[name]
            info=soundfile.info(io.BytesIO(item.read()))
        except Exception as error:raise RuntimeError("PC fixture audio metadata unsupported (Android may decode): "+name+" "+str(error))from error
        result={"name":name,"file":name,"duration_ms":info.duration*1000,"sample_rate":info.samplerate}
        self.audio_cache[name]=self.lua.table_from(result);return self.audio_cache[name]

    def audio(self,action,spec):
        self.counts["audio:"+action]+=1
        if action=="playing":return bool(spec["loop"]or self.clock<(spec["started"]or 0)+(spec["duration_ms"]or 0))
        if action=="completed":return self.clock>=(spec["started"]or 0)+(spec["duration_ms"]or 0)
        if action in("play","stop","fadeout","volume","pan","pause","resume","replace","stat_reset","frequency"):return True
        raise RuntimeError("PC startup fixture does not implement audio action: "+action)

    def movies(self):
        result={}
        header=uuid.UUID("75b22630-668e-11cf-a6d9-00aa0062ce6c").bytes_le
        properties=uuid.UUID("8cabdca1-a947-11cf-8ee4-00c00c205365").bytes_le
        for name,path in self.files.items():
            if not name.startswith("movie/"):continue
            duration=None
            if path.suffix.lower()==".wmv":
                with path.open("rb")as stream:
                    data=stream.read(30)
                    if data[:16]!=header:continue
                    total,count=struct.unpack_from("<QI",data,16)
                    if total>16*1024*1024:continue
                    for _ in range(count):
                        offset=stream.tell();obj=stream.read(24)
                        if len(obj)<24:break
                        size=struct.unpack_from("<Q",obj,16)[0]
                        if size<24 or size>total-offset:break
                        if obj[:16]==properties:
                            payload=stream.read(min(size-24,80))
                            duration=struct.unpack_from("<Q",payload,40)[0]/10000-struct.unpack_from("<Q",payload,56)[0]
                        stream.seek(offset+size)
            if not duration and path.suffix.lower()in(".wmv",".mpg",".mpeg",".avi",".mp4"):
                try:
                    import av
                    with av.open(str(path))as movie:
                        if movie.duration is not None:duration=movie.duration/1000
                        else:
                            spans=[float(s.duration*s.time_base)*1000 for s in movie.streams
                                   if s.duration is not None and s.time_base is not None]
                            if spans:duration=max(spans)
                    if duration and duration>0:
                        self.host_events.append({"kind":"ffmpeg_movie_metadata","file":name,"duration_ms":duration})
                except Exception as error:
                    self.host_events.append({"kind":"fixture_movie_metadata_error","file":name,
                                             "error":str(error)[:300]})
            if duration and duration>0:result[path.name.lower()]={"file":name,"duration_ms":duration}
            elif path.suffix.lower()in(".wmv",".mpg",".mpeg",".avi",".mp4"):
                self.host_events.append({"kind":"fixture_movie_metadata_not_available","name":name})
        return self.lua.table_from(result,recursive=True)

    def movie(self,action,spec):
        self.counts["movie:"+action]+=1
        if action=="status":return int(self.clock<(spec["started"]or 0)+(spec["duration_ms"]or 0))
        if action.startswith("texture"):raise RuntimeError("PC startup fixture does not emulate decoded movie texture")
        if action in("play","play_repeat","play_fromto","stop","volume"):return 1
        raise RuntimeError("PC startup fixture does not implement movie action: "+action)

    def graphics(self,request):
        op=request.split(" ",1)[0].split("\n",1)[0];self.counts["graphics:"+op]+=1
        if op=="batch":
            data=request.encode("utf8");at=data.index(b"\n")+1
            while at<len(data):
                end=data.index(b"\n",at);size=int(data[at:end]);at=end+1
                self.graphics(data[at:at+size].decode("utf8"));at+=size
        elif op in("load","load8"):
            parts=request.split(" ",3);name=parts[3];metadata=self.image(name)
            self.decode_image(metadata["name"],bool(int(parts[2])))
            self.resource_reads.append({"kind":"image","name":name,"indexed":op=="load8",
                                        "alpha":bool(int(parts[2])),"pixels_decoded":True})
        elif op in("glyphs","text"):
            lines=request.split("\n",3);params=lines[0].split()
            height=int(params[4])
            if op=="glyphs":width=sum(int(v)for v in lines[2].split())
            else:
                half=int(params[5])or max(1,height//2);text=lines[-1]
                width=sum(half if ord(c)<128 else half*2 for c in text)
                if int(params[7])>0:width=min(width,int(params[7]))
            return"OK "+str(max(1,width))+" "+str(height)
        elif op=="pixel":return"OK 0"
        elif op=="mosaic":return"OK 1"
        elif op=="cachebmp":raise RuntimeError("PC metadata fixture does not encode BMP picture cache")
        if op in("batch","load","load8","create","release","alpha","alphatable","antidata","copy","extcopy",
                  "swap","fill","extfill","mulfill","set","sepia","paste","drawmode","reverse","revmulcopy",
                  "mulcopy","overlayfill","mosaic","thumbrect","fuchidori","tablecopy","smoothframe","modify",
                  "thumbnail","presentview","presentzoom","present","png","reset","reset_pages",
                  "checkpoint_begin","checkpoint_commit","checkpoint_rollback"):
            return"OK"
        raise RuntimeError("PC metadata fixture does not implement graphics action: "+op)

    def decode_image(self,name,alpha=False):
        marker=(name,alpha)
        if marker in self.decoded_images:return
        if name not in self.resources:
            # Independently checked against original page_create_file x86 in
            # test_page_image_loading; corrupt existing files are not optional.
            self.decoded_images[marker]=(1,1);return
        item=self.resource(name);raw=item.head()
        if raw.startswith(b"\x89PNG")or raw.startswith(b"BM"):
            with Image.open(io.BytesIO(item.read()))as image:
                image.load();dimensions=image.size
        else:
            owner=self
            class CatalogueView:
                @property
                def lookup(self):return owner.resources
                key=staticmethod(key)
                def get(self,name):return owner.resource(name)
                def resolve(self,name,extensions=()):
                    for candidate in(name,*(name+ext for ext in extensions)):
                        if key(candidate)in owner.resources:return owner.resource(candidate)
                    raise ValueError("source image missing: "+name)
            decoder=Decoder(CatalogueView())
            password=self.system["state"]["picture_key"]if self.system else None
            if password:decoder.encryption_key=image_key(password.encode("cp932"))
            image,_=decoder.pixels(name);dimensions=image.size
            if alpha and name.endswith(".rct"):
                mask=name[:-4]+"_.rc8"
                if mask in self.resources:
                    mask_image,_=decoder.pixels(mask)
                    if mask_image.size!=image.size:raise ValueError("alpha image mask dimensions mismatch: "+name)
        self.decoded_images[marker]=dimensions

    def translation(self,action,payload):
        if action=="config":return json.dumps({"mode":self.mode,"ready":self.mode=="online",
                                               "context":20,"generation":"local-startup-mock"})
        if action=="clear":return"cleared"
        if action=="waiting":return"ok"
        query=json.loads(payload)
        if action=="display_status":return"ok"
        if action=="lookup":
            value=self.translation_cache.get(query["id"])
            return value[1]if value and self.clock>=value[0]else""
        if action=="submit":
            self.translation_submits+=1
            for line in query["lines"]:
                self.translation_cache.setdefault(line["id"],(self.clock+100,"\u8bd1\u6587\uff1a"+line["text"]))
            return"queued"
        raise ValueError("unexpected translation fixture action: "+action)

    def storage(self,request):
        if request.startswith("sha256\n"):return"OK "+hashlib.sha256(request[7:].encode("utf8")).hexdigest()
        if request=="save_legacy_identity":
            value="audit:"+self.game["id"]+"".join(":"+str(archive.path.stat().st_size)
                for name,archive in self.archive_rows if key(name)not in self.image_only_archives)
            return"OK "+hashlib.sha256(value.encode()).hexdigest()
        if request=="save_legacy_catalog":
            rows=[{"root":0,"name":name}for name in sorted(self.writes)
                  if name=="index.mjs"or(name.startswith("index_")and name.endswith(".mjs"))]
            return"OK data\n"+json.dumps(rows)
        if request.startswith("save_legacy_read "):
            _,root,name=request.split(" ")
            assert root=="0"
            return"OK data\n"+self.writes[name]if name in self.writes else"OK absent"
        if request.startswith("save_legacy_copy "):
            _,root,name=request.split(" ")
            assert root=="0"and name in self.writes
            return"OK"
        action,_,rest=request.partition(" ");name,_,data=rest.partition("\n")
        if action=="save_read":return"OK data\n"+self.writes[name]if name in self.writes else"OK absent"
        if action=="save_exists":return"OK 1"if name in self.writes else"OK 0"
        if action=="save_write":self.writes[name]=data;return"OK"
        if action=="save_delete":self.writes.pop(name,None);return"OK"
        raise RuntimeError("unexpected storage action: "+action)

    def identity(self):
        assert self.text_sealed
        value="audit:"+self.game["id"]+"\ntext-resources-v2\n"+("local"if self.mode=="local"else"original")
        value+="".join("\n"+name+"\n"+stamp for name,stamp in sorted(self.text_snapshot.items()))
        return hashlib.sha256(value.encode()).hexdigest()

    def platform(self,action,args):
        self.counts["platform:"+action]+=1
        if action=="fullscreen_mode":return self.lua.table_from([max(1280,args[1]),max(720,args[2])])
        if action in("caps_get","caps_set","display_scale","cursor_move","service_events"):return 0
        raise RuntimeError("unmodeled platform action: "+action)

    def service(self,action,value):
        self.counts["service:"+action]+=1
        if action=="system_lcid":return"1041"
        if action=="font_quality":return"OK"
        if action=="checker_begin":raise RuntimeError("original external authentication companion is unavailable")
        raise RuntimeError("startup crossed unmodeled Android service: "+action)

    def dialog(self,*args):
        if args[0]=="begin":self.dialogs.append({"flags":args[2],"message":args[3],"caption":args[4]})
        return"pending"

    def cursor(self,name):
        path=self.files.get("movie/"+name)
        if not path:raise RuntimeError("cursor resource missing: "+name)
        raw=path.read_bytes()
        if len(raw)<22:raise RuntimeError("cursor header truncated: "+name)
        size,offset=struct.unpack_from("<II",raw,14)
        if offset+size>len(raw)or size<40:raise RuntimeError("cursor resource range: "+name)
        width,height=struct.unpack_from("<II",raw,offset+4);depth=struct.unpack_from("<H",raw,offset+14)[0]
        if not(0<width<=256 and 0<height//2<=256 and depth==32):
            raise RuntimeError("Android cursor supports only <=256 uncompressed 32-bit DIB: "+name)
        return True

    def cp932(self,text):
        # Match GameBridge.convert: malformed/unmappable source conversions
        # are errors. Replacing Chinese characters with '?' here concealed
        # real-device failures in string comparisons after online translation.
        return text.encode("cp932")

    def run(self):
        began=time.monotonic();error=None;stop="budget";initial_hashes={}
        for name in("majiro_vm.lua","majiro_syscalls.lua","player_boot.lua","player_configuration.lua"):
            initial_hashes[name]=sha256(ROOT/"player/runtime"/name)
        host=self.lua.table()
        callbacks={"asset_text":self.module,"game_text":self.game_text,"resource_text":self.resource_text,
            "text_mode":lambda:self.mode,"patch_configuration":lambda:json.dumps(dict(self.manifest,legacy=False,
                raw_metadata=self.manifest.get("raw_metadata",""),text_adapter=self.manifest.get("text_adapter",{}))),
            "identity":self.identity,
            "text_resource_names":self.text_resource_names,"patch_resource_names":self.patch_resource_names,
            "pin_text_resource":lambda name:self.raw(name)and None,"seal_text_resources":self.seal_text_resources,
            "set_text_identity":self.set_text_identity,
            "encode_text":lambda text,encoding:text.encode({"cp936":"gbk"}.get(encoding,encoding)),
            "engine_requirements":self.engine_requirements,"member_names":self.member_names,
            "image_metadata":lambda name:self.lua.table_from(self.image(name)),
            "image_asset":lambda name,*args:self.lua.table_from(self.image(name)),
            "script":self.script,"graphics":self.graphics,"media_asset":self.media,"audio":self.audio,
            "translation":self.translation,
            "archive_control":self.archive,
            "movie":self.movie,"movies":self.movies,"storage":self.storage,"service":self.service,
            "dialog":self.dialog,"platform":self.platform,"screen_mode":lambda value:value,
            "screen_fit":lambda *args:True,"font_dialog":lambda face,*args:face,"font_filter":lambda *args:None,
            "resize_window":lambda *args:True,"change_cursor":self.cursor,"wallpaper":lambda name,*args:self.image(name)and 0,
            "load_font":lambda name:bool(self.files.get(name)or self.files.get("font/"+name)),
            "debug_output":lambda text,*args:self.host_events.append({"kind":"script_debug","text":text[:1000]}),
            "cp932":self.cp932,"decode_bytes":lambda raw:raw.encode("utf8").decode("cp932"),
            "script_file_read":lambda name:self.resource_text(name)if key(name)in self.resources else self.writes.get(name),
            "script_file_write":lambda name,text:self.writes.update({name:text}),
            "external_save":lambda *args:"CANCEL","set_clock":lambda value:setattr(self,"clock",value)}
        for name,value in callbacks.items():host[name]=value
        # Lupa's UTF-8 Python string boundary cannot carry arbitrary MJO/bin
        # bytes. Hash and decode binary strings through hex, preserving bytes.
        host["storage"]=self.lua.eval("""function(hash,other)return function(request)
          if request:sub(1,7)=='sha256\\n' then
            return 'OK '..hash((request:sub(8):gsub('.',function(c)return string.format('%02x',c:byte())end)))
          end
          return other(request)
        end end""")(lambda value:hashlib.sha256(bytes.fromhex(value)).hexdigest(),self.storage)
        decode=self.lua.eval("""function(convert)return function(raw,encoding)
          return convert((raw:gsub('.',function(c)return string.format('%02x',c:byte())end)),encoding or'cp932')
        end end""")(lambda value,encoding:bytes.fromhex(value).decode({"cp936":"gbk"}.get(encoding,encoding)))
        host["decode_bytes"]=decode;host["decode_text"]=decode
        self.lua.globals()["HOST"]=host
        boot=(ROOT/"player/runtime/player_boot.lua").read_text(encoding="utf8")
        # Observe lexical state without changing any executed instruction or PC.
        observed=boot+"\nfunction STARTUP_AUDIT_STATE()return vm,system,configuration end\n"
        try:
            self.lua.execute(observed)
            self.vm,self.system,configuration=self.lua.globals()["STARTUP_AUDIT_STATE"]()
            while self.vm["steps"]<self.maximum and time.monotonic()-began<self.seconds:
                self.lua.globals()["PLAYER_TICK"](self.clock,self.lua.table())
                state=self.system["state"]
                if self.vm["modal_dialog"]:stop="dialog_boundary";break
                if self.vm["external_checker"]:stop="authentication_boundary";break
                if state["ui_waiting"]and not state["reading_wait"]:stop="source_ui_wait";break
                if state["reading_wait"]:stop="first_text_wait";break
                if self.vm["status"]=="finished":stop="finished";break
                deadline=self.vm["next_deadline"](self.vm)
                self.clock=max(self.clock+16,deadline or 0)
                if self.clock>180000:stop="simulated_time_limit";break
                main=self.vm["threads"][1]
                if self.vm["status"]=="waiting"and main["waiting"]and main["waiting"]["kind"]=="input":
                    stop="input_boundary";break
        except Exception as exc:
            error=str(exc)[:5000];stop="blocked"if self.vm and self.vm["status"]=="blocked"else"fixture_or_configuration_error"
        problem=table_value(self.vm["problem"])if self.vm and self.vm["problem"]else None
        state=self.system["state"]if self.system else None
        missing_audio=table_value(state["media"]["missing_requests"])if state and state["media"]["missing_requests"]else[]
        aliases=[]
        if problem and"picture dimensions missing:"in problem["reason"]:
            requested=problem["reason"].split("picture dimensions missing:",1)[1].strip()
            stem=requested.rsplit(".",1)[0]if"."in requested else requested
            aliases=[{"requested":requested,"same_stem_member":self.resources[key(stem+ext)].name}
                     for ext in(".rct",".rc8",".png")if key(stem+ext)in self.resources]
        return{"game":self.game["id"],"mode":self.requested_mode,"effective_mode":self.mode,
            "local_unavailable_auto_fallback":self.local_unavailable,"text_preflight_sealed":self.text_sealed,
            "verified_text_resources":len(self.text_snapshot),"text_identity":self.text_identity,
            "stop":stop,"error":error,"problem":problem,
            "steps":int(self.vm["steps"])if self.vm else 0,"simulated_ms":self.clock,"seconds":round(time.monotonic()-began,3),
            "loaded_scripts":sorted(self.vm["scripts"].keys())if self.vm else sorted(self.script_cache),
            "ui_area_count":len(state["ui_areas"])if state and state["ui_areas"]else 0,
            "reading_wait":bool(state["reading_wait"])if state else False,
            "source_title_verified":False,"visual_render_verified":False,
            "trace":table_value(self.vm["trace"])if self.vm else[],"dialogs":self.dialogs,
            "host_counts":dict(self.counts),"resource_reads":self.resource_reads,
            "derived_png_masks":sorted(self.derived),"missing_audio":missing_audio,
            "decoded_page_images":len(self.decoded_images),
            "online_mock_batches":self.translation_submits,
            "translation":table_value(self.lua.globals()["PLAYER_STATUS"]()["translation"])if self.vm else None,
            "host_events":self.host_events,"engine_hashes":self.profile_hashes,
            "blocked_resource_alias_candidates":aliases,
            "patch_layers":self.manifest.get("script_layers",[]),"patch_archives":self.manifest.get("archives",[]),
            "isolated_save_writes":len(self.writes),"source_changed_during_run":any(
                sha256(ROOT/"player/runtime"/name)!=hash_ for name,hash_ in initial_hashes.items())}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--game",action="append")
    parser.add_argument("--mode",action="append",choices=("original","local","online"))
    parser.add_argument("--exclude",action="append",default=[])
    parser.add_argument("--seconds",type=int,default=25);parser.add_argument("--maximum",type=int,default=1500000)
    args=parser.parse_args();folder=archive_dir("startup_resource_audit")
    files,roots=payload(folder)
    samples=read_json(ROOT/"config/player_samples.json")["games"]+read_json(ROOT/"config/player_new_samples.json")["games"]
    by_id={game["id"]:game for game in samples}
    report={"schema":1,"timestamp":timestamp(),"status":"running","games":[],"device_accessed":False,
        "windows_executable_executed":False,"old_artemis_runtime_used":False,"full_gameplay_verified":False,
        "scope":"independent Lua VM and actual source/deployment resources until first source UI/text/dialog/input boundary",
        "fixture_limits":["graphics decodes loaded image pixels but does not render or verify every composition operation",
            "audio durations use libsndfile; Android IJK can support formats not accepted by that fixture",
            "known ASF duration is parsed; other movie metadata may stop the PC fixture",
            "fresh in-memory saves and original default input; no dialog answers or title choices are invented",
            "source_ui_wait is only a title candidate, not visual verification"],
        "evidence":str(folder.relative_to(ROOT)),
        "source_sha256":{str(path.relative_to(ROOT)).replace("\\","/"):sha256(path)
            for path in[*(ROOT/"player/runtime").glob("*.lua"),
                ROOT/"player/android/game_host.cpp",ROOT/"player/core/image_resource.hpp",
                ROOT/"player/android/src/org/majiro/player/GameBridge.java",
                ROOT/"scripts/analysis/audit_startup_resources.py"]}}
    receipt=ROOT/"reports/startup_resource_audit.json"
    for root in roots:
        if args.game and root["id"]not in args.game:continue
        if root["id"]in args.exclude:continue
        prefix=root["directory"]+"/"
        gamefiles={target[len(prefix):]:source for source,target in files if target.startswith(prefix)}
        for mode in(args.mode or("original","local","online")):
            try:row=Startup(by_id[root["id"]],gamefiles,mode,args.maximum,args.seconds).run()
            except Exception as error:row={"game":root["id"],"mode":mode,"stop":"fixture_index_error","error":str(error)}
            report["games"].append(row)
            write_json(receipt,report);write_json(folder/"20261110_startup_resource_audit.json",report)
            print(root["id"],mode,row["stop"],"steps",row.get("steps",0),
                  (row.get("problem")or{}).get("reason",row.get("error")or"")[:200],flush=True)
    report.update(status="diagnostic_complete",timestamp=timestamp(),summary=dict(Counter(row["stop"]for row in report["games"])))
    write_json(receipt,report);write_json(folder/"20261110_startup_resource_audit.json",report)
    return 0


if __name__=="__main__":raise SystemExit(main())
