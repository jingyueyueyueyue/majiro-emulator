"""Render original and online title frames through the generated Android pixel core."""
from pathlib import Path
import hashlib
import io
import json
import struct
import sys
from PIL import Image
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/"convert"),
              str(Path(__file__).resolve().parents[1]/"build")]
from audit_startup_resources import Startup,key
from deploy_player_android import payload
from majiro_image import Decoder
from port_common import ROOT,archive_dir,read_json,write_json,timestamp
from test_screen_semantics import Portable


def main():
    folder=archive_dir("patch_archive_frames")
    files,roots=payload(folder)
    game=next(g for g in read_json(ROOT/"config/player_samples.json")["games"]if g["id"]=="narci_sumire")
    prefix=next(r["directory"]for r in roots if r["id"]==game["id"])+"/"
    files={target[len(prefix):]:path for path,target in files if target.startswith(prefix)}
    native=Portable(folder)
    rows=[]
    try:
        for mode in("original","online"):
            startup=Startup(game,files,mode,1500000,35);result=startup.run()
            assert not result["error"]and result["stop"]=="source_ui_wait",result
            # Script and non-image members cannot leak out of mixed update1.arc.
            for name,item in startup.resources.items():
                if item.member and key(str(item.member.archive.name)).startswith("update"):
                    assert item.extension in(".png",".bmp",".rct",".rc8"),name
            state=startup.system["state"]
            native("reset")
            images={}
            class CatalogueView:
                @property
                def lookup(self):return startup.resources
                key=staticmethod(key)
                def get(self,name):return startup.resource(name)
                def resolve(self,name,extensions=()):
                    for candidate in(name,*(name+ext for ext in extensions)):
                        if key(candidate)in startup.resources:return startup.resource(candidate)
                    raise ValueError(name)
            decoder=Decoder(CatalogueView())
            def decoded(name,alpha):
                metadata=startup.image(name);resolved=metadata["name"]
                if resolved not in startup.resources and resolved.endswith("_.rc8"):
                    source=resolved[:-5]+".png"
                    if source in startup.resources:
                        image,_=decoded(source,False)
                        inverse=Image.eval(image.getchannel("A"),lambda value:255-value).convert("P")
                        inverse.putpalette([value for i in range(256)for value in(i,i,i)])
                        return inverse.convert("RGBA"),inverse
                item=startup.resource(resolved);raw=item.read()
                if raw.startswith((b"\x89PNG",b"BM")):
                    image=Image.open(io.BytesIO(raw));image.load()
                    return image.convert("RGBA"),image if image.mode=="P"else None
                image,_=decoder.rgba(resolved)if alpha else decoder.pixels(resolved)
                return image.convert("RGBA"),image if image.mode=="P"else None
            commands=list(state["graphics_journal"].values())
            for command in commands:
                if command.startswith(("load ","load8 ")):
                    operation,handle,alpha,name=command.split(" ",3)
                    image,indexed=decoded(name,bool(int(alpha)))
                    identity=hashlib.sha256((mode+"\n"+name+"\n"+alpha).encode()).hexdigest()
                    path=folder/(identity+".mji")
                    if not path.exists():
                        palette=bytes((indexed.getpalette()or[])[:768])if indexed else bytes(768)
                        palette=palette+bytes(768-len(palette))
                        path.write_bytes(struct.pack("<III",*image.size,8 if indexed else 32)+palette+
                            image.tobytes()+(indexed.tobytes()if indexed else b""))
                    command=" ".join((operation,handle,alpha,"fixture://"+path.as_posix()))
                native(command)
            startup.lua.globals()["PLAYER_TICK"](startup.clock+16,startup.lua.table())
            sprites=[]
            for ident in state["sprite_order"].values():
                sprite=state["sprites"][ident]
                if sprite and sprite["destination"]==state["frontbuffer"]and sprite["anti"]<255:
                    sprites.append("%d %d %d %d %d %.8g %.8g %.8g %.8g %.8g %d"%tuple(
                        sprite[k]for k in("page","sx","sy","width","height","x","y","xscale","yscale","rotate","anti"))+
                        (" 1"if sprite["extended"]else" 0"))
            request=startup.system["screen"]["present"](state["frontbuffer"],"screen","\n".join(sprites))
            _,frame=native(request)
            image=Image.frombytes("RGBA",(800,600),frame)
            extrema=image.convert("RGB").getextrema()
            assert sum(high-low for low,high in extrema)>100,"title frame is blank"
            image.save(folder/(mode+"_title.png"))
            rows.append({"mode":mode,"status":"passed","frame_sha256":hashlib.sha256(frame).hexdigest(),
                         "extrema":extrema,"source_ui_areas":result["ui_area_count"]})
    finally:native.close()
    report={"status":"passed","timestamp":timestamp(),"cases":rows,"device_accessed":False,
        "actual_generated_pixel_core":True,"evidence":str(folder.relative_to(ROOT))}
    write_json(ROOT/"reports/patch_archive_frames_tests.json",report);write_json(folder/"report.json",report)
    print(json.dumps(report,ensure_ascii=True))
    return 0


if __name__=="__main__":raise SystemExit(main())
