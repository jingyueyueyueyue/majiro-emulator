"""Read-only five-sample original/local startup, patch/font and PNG-mask audit."""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import struct
import sys
import time
from fontTools.ttLib import TTFont
from audit_startup_resources import Startup,Resource,key
from lua_bridge import table_value
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,read_json,sha256,timestamp,write_json
from majiro_mjo import Script


class NarcissuStartup(Startup):
    def __init__(self,*args,**kwargs):
        self.image_requests=[];self.png_info={};self.font_requests=[]
        super().__init__(*args,**kwargs)

    def png(self,name):
        name=key(name)
        if name in self.png_info:return self.png_info[name]
        item=self.resource(name);head=item.head(33)
        if not head.startswith(b"\x89PNG\r\n\x1a\n")or len(head)<33:
            raise ValueError("invalid PNG metadata: "+name)
        width,height,depth,color=struct.unpack_from(">IIBB",head,16)
        transparency=color in(4,6);trns=False
        if not transparency:
            with (item.member.archive if item.member else item.path).open("rb")as stream:
                begin=item.member.offset if item.member else 0
                stream.seek(begin+8);remaining=item.size-8
                while remaining>=12:
                    chunk=stream.read(8);length=struct.unpack_from(">I",chunk)[0];kind=chunk[4:]
                    if length>remaining-12:raise ValueError("truncated PNG chunk: "+name)
                    if kind in(b"IDAT",b"IEND"):break
                    if kind==b"tRNS":trns=length>0;transparency=trns;break
                    stream.seek(length+4,1);remaining-=length+12
        result={"width":width,"height":height,"bit_depth":depth,"color_type":color,
                "transparency":transparency,"trns":trns}
        self.png_info[name]=result
        return result

    def image(self,requested):
        request=key(requested)
        for candidate in(request,request+".rct",request+".rc8",request+".png"):
            if candidate in self.resources:
                result=self._image(candidate)
                self.image_requests.append({"requested":requested,"resolved":candidate,"derived":False})
                return result
        stem=request[:-4]if request.endswith((".rc8",".png"))else request
        if stem.endswith("_")and stem[:-1]+".png"in self.resources:
            source=stem[:-1]+".png"
            if not self.png(source)["transparency"]:
                raise ValueError("PNG has no transparency mask: "+requested)
            image=dict(self._image(source))
            image.update(name=stem+".rc8",file=stem+".rc8",depth=8,source_depth=8)
            self.derived.add(image["name"])
            self.image_requests.append({"requested":requested,"resolved":image["name"],
                                        "source":source,"derived":True})
            return image
        raise ValueError("resource missing: "+requested)

    def member_names(self):
        actual=set(self.resources);names=[item.name for item in self.resources.values()]
        for name in sorted(actual):
            if name.endswith(".png")and name[:-4]+"_.rc8"not in actual and self.png(name)["transparency"]:
                names.append(name[:-4]+"_.rc8")
        return self.lua.table_from(names)

    def script(self,name):
        # Native HOST.script decodes a fresh table on every loader request.
        value=super().script(name)
        return self.lua.eval("function(value)local codec=assert(loadstring(HOST.asset_text('runtime/majiro_codec.lua')))();return codec.clone(value)end")(value)

    def archive_control(self,action,name):
        target=key(name)
        matches=[row for row in self.archive_rows if key(row[0])==target]
        if action=="release":
            self.active_archives=[row for row in self.active_archives if key(row[0])!=target]
        elif action=="register":
            if not matches:
                source=Path(self.game["directory"])/name[:-4]
                if source.is_dir():raise RuntimeError("PC fixture does not model original directory registration: "+name)
                self.host_events.append({"kind":"optional_archive_missing","name":name,"script_result":0})
                return 0
            self.active_archives=matches+[row for row in self.active_archives if key(row[0])!=target]
        else:raise ValueError("unknown archive control")
        self._refresh()
        self.resources.update(self.loose)
        if self.mode=="local":
            for layer in reversed(self.manifest.get("script_layers",[])):
                prefix=layer["directory"]+"/"
                for name,path in self.files.items():
                    if name.startswith(prefix):
                        basename=name[len(prefix):]
                        if "/"not in basename and basename.endswith(".mjo"):
                            self.resources[key(basename)]=Resource(path=path)
        self.image_cache.clear();self.png_info.clear()
        self.host_events.append({"kind":"archive_"+action,"name":name})
        return 0

    def run(self):
        began=time.monotonic();stop="budget";error=None
        host=self.lua.table()
        callbacks={"asset_text":self.module,"game_text":self.game_text,"resource_text":self.resource_text,
            "text_mode":lambda:self.mode,"patch_configuration":lambda:json.dumps(dict(self.manifest,legacy=False,
                raw_metadata=self.manifest.get("raw_metadata",""),text_adapter=self.manifest.get("text_adapter",{}))),
            "identity":lambda:"a"*64,"engine_hashes":lambda:self.lua.table_from(self.profile_hashes),
            "member_names":self.member_names,"image_metadata":lambda n:self.lua.table_from(self.image(n)),
            "image_asset":lambda n,*args:self.lua.table_from(self.image(n)),"script":self.script,
            "graphics":self.graphics,"media_asset":self.media,"audio":self.audio,"movie":self.movie,
            "movies":self.movies,"storage":self.storage,"service":self.service,"dialog":self.dialog,
            "platform":self.platform,"screen_mode":lambda v:v,"screen_fit":lambda *args:True,
            "font_dialog":lambda face,*args:face,"font_filter":lambda *args:None,"resize_window":lambda *args:True,
            "change_cursor":self.cursor,"wallpaper":lambda n,*args:self.image(n)and 0,
            "load_font":lambda n:bool(self.files.get(n)or self.files.get("font/"+n)),
            "cp932":lambda text:text.encode("cp932"),"decode_bytes":lambda raw:raw.encode("latin1").decode("cp932"),
            "script_file_read":lambda n:self.resource_text(n)if key(n)in self.resources else self.writes.get(n),
            "script_file_write":lambda n,text:self.writes.update({n:text}),
            "external_save":lambda *args:"CANCEL","set_clock":lambda value:setattr(self,"clock",value),
            "archive_control":self.archive_control,"license":lambda *args:self.lua.table_from({"registry_open":False})}
        for name,value in callbacks.items():host[name]=value
        self.lua.globals()["HOST"]=host
        boot=(ROOT/"player/runtime/player_boot.lua").read_text(encoding="utf8")
        try:
            self.lua.execute(boot+"\nfunction STARTUP_AUDIT_STATE()return vm,system,configuration end\n")
            self.vm,self.system,configuration=self.lua.globals()["STARTUP_AUDIT_STATE"]()
            while self.vm["steps"]<self.maximum and time.monotonic()-began<self.seconds:
                self.lua.globals()["PLAYER_TICK"](self.clock,self.lua.table())
                state=self.system["state"]
                if self.vm["modal_dialog"]:stop="dialog_boundary";break
                if self.vm["external_checker"]:stop="authentication_boundary";break
                if state["ui_waiting"]and not state["reading_wait"]:stop="source_ui_wait";break
                if state["reading_wait"]:stop="first_text_wait";break
                if self.vm["status"]=="finished":stop="finished";break
                self.clock=max(self.clock+16,self.vm["next_deadline"](self.vm)or 0)
                if self.clock>180000:stop="simulated_time_limit";break
        except Exception as exc:
            error=str(exc)[:5000];stop="blocked"if self.vm and self.vm["status"]=="blocked"else"fixture_or_configuration_error"
        state=self.system["state"]if self.system else None
        return {"game":self.game["id"],"mode":self.mode,"stop":stop,"error":error,
            "problem":table_value(self.vm["problem"])if self.vm and self.vm["problem"]else None,
            "steps":int(self.vm["steps"])if self.vm else 0,"simulated_ms":self.clock,
            "seconds":round(time.monotonic()-began,3),"loaded_scripts":sorted(self.script_cache),
            "derived_png_masks":sorted(self.derived),"host_counts":dict(self.counts),
            "host_events":self.host_events,"dialogs":self.dialogs,"resource_reads":self.resource_reads,
            "ui_areas":len(state["ui_areas"])if state and state["ui_areas"]else 0,
            "visual_verified":False,"device_accessed":False}


def static_audit(startup,manifest):
    images=[];failures=[]
    for name,item in startup.resources.items():
        if not name.endswith(".png"):continue
        info=startup.png(name);alias=name[:-4]+"_.rc8"
        try:
            if info["transparency"]:
                image=startup.image(alias)
                assert image["depth"]==8 and(image["width"],image["height"])==(info["width"],info["height"])
            images.append({"name":name,**info,"mask":alias if info["transparency"]else None,
                           "real_mask_file":alias in startup.resources})
        except Exception as error:failures.append({"name":name,"error":str(error)})
    formal={}
    if startup.mode=="local":
        data=startup.lua.execute((ROOT/"localization/narcissu"/startup.game["id"]/"adapter.lua").read_text(encoding="utf8"))
        for name,record in data["scripts"].items():
            actual=startup.resource(name)
            if hashlib.sha256(actual.read()).hexdigest()!=record["sha256"]:
                failures.append({"script":name,"error":"text adapter source differs from effective resource"})
        formal={"declared_scripts":sum(1 for _ in data["scripts"].keys()),"adapter_identity":data["identity"],
                "typography":table_value(data["typography"])}
        font_prefix=startup.manifest.get("font_directory","patches/fonts")+"/"
        font=startup.files.get(font_prefix+"SourceHanSansSC-Regular.otf")
        if not font:failures.append({"error":"declared bundled CJK font is missing"})
        else:
            with TTFont(font,lazy=True)as loaded_font:coverage=loaded_font.getBestCmap()
            chars=set(data["encoded_characters"].keys())
            missing=[c for c in chars if ord(c)>127 and ord(c)not in coverage]
            formal["font"]={"path":str(font),"sha256":sha256(font),"bytes":font.stat().st_size,
                            "translated_characters":len(chars),"missing_glyphs":missing}
        for entry in manifest["deployment"]:
            if sha256(Path(entry["source"]))!=entry["sha256"]:
                failures.append({"source":entry["source"],"error":"formal deployment source hash changed"})
    return {"png_count":len(images),"transparent_pngs":sum(i["transparency"]for i in images),
            "trns_pngs":sum(i["trns"]for i in images),"pngs":images,
            "formal_patch":formal,"failures":failures}


def latent_media_audit(startup):
    """Probe supplied movie members without choosing a source story route."""
    movies={name:item for name,item in startup.resources.items()
            if name.endswith((".mpg",".mpeg",".mp4",".wmv",".avi"))}
    if not movies:return []
    references=[]
    for name,item in startup.resources.items():
        if not name.endswith(".mjo"):continue
        try:script=Script(item.name,item.read())
        except (ValueError,UnicodeError):continue
        for index,row in enumerate(script.instructions):
            if row.name!="ldstr"or key(row.operands[0])not in movies:continue
            following=script.instructions[index+1:index+3]
            references.append({"script":script.name,"script_sha256":script.sha256,"offset":row.offset,
                               "name":row.operands[0],"following":[{"opcode":i.name,"operands":i.operands}
                                                                for i in following]})
    results=[]
    for name,item in movies.items():
        error=None
        if startup.system:
            probe=startup.lua.eval("function()return{fail=function(self,why)error(why,0)end}end")()
            try:startup.system["providers"]["$movie_play"](probe,startup.lua.table_from([item.name]))
            except Exception as failure:error=str(failure).split("\n",1)[0]
        results.append({"name":item.name,"bytes":item.size,
            "source_archive":str(item.member.archive)if item.member else None,
            "loose_movie_directory":False,"production_movie_provider_probe":error,
            "requires_streaming_above_current_64MiB_read_bound":item.size>64*1024*1024,
            "source_references":[r for r in references if key(r["name"])==name],
            "story_route_executed":False})
    return results


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--game",action="append")
    parser.add_argument("--seconds",type=int,default=25);args=parser.parse_args()
    folder=archive_dir("narcissu_startup_audit")
    from audit_startup_resources import payload
    files,roots=payload(folder)
    samples=read_json(ROOT/"config/player_samples.json")["games"]
    wanted={g["id"]:g for g in samples if g["id"].startswith("narci_")}
    frozen=[p for base in("player/runtime","player/core","player/android")for p in(ROOT/base).rglob("*")if p.is_file()]
    before={str(p.relative_to(ROOT)):sha256(p)for p in frozen}
    report={"schema":1,"timestamp":timestamp(),"status":"running","cases":[],"device_accessed":False,
            "windows_executable_executed":False,"scope":"five Narcissu original/local first source input/UI boundary",
            "limits":["graphics checks resources and metadata; pixels/UI are not rendered",
                      "audio metadata uses libsndfile; unsupported codecs are explicit fixture boundaries",
                      "fresh memory saves; no source choices or dialog answers injected"],
            "evidence":str(folder.relative_to(ROOT))}
    for root in roots:
        if root["id"]not in wanted or args.game and root["id"]not in args.game:continue
        prefix=root["directory"]+"/"
        gamefiles={target[len(prefix):]:source for source,target in files if target.startswith(prefix)}
        manifest=read_json(ROOT/"localization/narcissu"/root["id"]/"manifest.json")
        for mode in("original","local"):
            startup=NarcissuStartup(wanted[root["id"]],gamefiles,mode,seconds=args.seconds)
            inventory=static_audit(startup,manifest)
            row=startup.run();row["inventory"]=inventory;row["latent_archive_movies"]=latent_media_audit(startup)
            report["cases"].append(row)
            write_json(ROOT/"reports/narcissu_startup_audit.json",report)
            print(root["id"],mode,row["stop"],row["steps"],(row["problem"]or{}).get("reason",row["error"]or"")[:170],flush=True)
    report.update(status="diagnostic_complete",timestamp=timestamp(),
                  summary=dict(Counter(row["stop"]for row in report["cases"])),
                  runtime_files_modified=False,
                  source_changed_during_audit=any(sha256(ROOT/name)!=digest for name,digest in before.items()),
                  source_sha256=before)
    write_json(ROOT/"reports/narcissu_startup_audit.json",report)
    write_json(folder/"20261110_narcissu_startup_audit.json",report)


if __name__=="__main__":main()
