"""Headless original-script execution with real native page/sprite composition.

Run one instance per worker PROCESS: the production native DLL owns page state.
Only presentation sampling is reduced; every page operation and glyph is real.
Audio/video metadata and a delayed translation transport are local test fixtures.
"""
from pathlib import Path
from collections import Counter
import hashlib
import io
import json
import re
import shutil

from PIL import Image
from audit_startup_resources import Startup,Resource,key
from majiro_image import Decoder,image_key
from native_pixels import Pixels
from lua_bridge import table_value


class PixelSession(Startup):
    def __init__(self,game,files,mode,folder,maximum=3000000,seconds=90):
        self.folder=Path(folder);self.folder.mkdir(parents=True,exist_ok=True)
        self.native=Pixels();self.last_render=-100000;self.last_signature=None
        self.last_present_request=None;self.page_sources={};self.decoded_paths={}
        self.operation_counts=Counter();self.rendered_frames=0;self.snapshots=[]
        super().__init__(game,files,mode,maximum,seconds)

    def local_path(self,name):
        prefix,_,relative=name.partition("://")
        target=(self.folder/prefix/relative).resolve()
        if not target.is_relative_to(self.folder.resolve()):raise ValueError("fixture path escape")
        target.parent.mkdir(parents=True,exist_ok=True)
        return target

    def image(self,name):
        if name.startswith(("save://","cache://")):
            path=self.local_path(name)
            with Image.open(path)as image:
                return {"name":name,"file":name,"width":image.width,"height":image.height,"depth":32}
        return super().image(name)

    def decoded(self,requested,alpha,indexed):
        if requested.startswith(("save://","cache://")):return self.local_path(requested)
        metadata=self.image(requested);name=metadata["name"]
        marker=(name,alpha,indexed,self.system["state"]["picture_key"]if self.system else"")
        if marker in self.decoded_paths:return self.decoded_paths[marker]
        owner=self
        class View:
            @property
            def lookup(self):return owner.resources
            key=staticmethod(key)
            def get(self,name):return owner.resource(name)
            def resolve(self,name,extensions=()):
                for candidate in(name,*(name+ext for ext in extensions)):
                    if key(candidate)in owner.resources:return owner.resource(candidate)
                raise ValueError("missing image "+name)
        decoder=Decoder(View())
        password=self.system["state"]["picture_key"]if self.system else None
        if password:decoder.encryption_key=image_key(password.encode("cp932"))
        if name not in self.resources and name.endswith("_.rc8")and name[:-5]+".png"in self.resources:
            source=self.resource(name[:-5]+".png").read()
            with Image.open(io.BytesIO(source))as png:
                image=Image.eval(png.getchannel("A"),lambda x:255-x).convert("P")
                image.putpalette([v for i in range(256)for v in(i,i,i)])
        elif name not in self.resources:
            image=Image.new("P"if indexed else"RGBA",(1,1),0)
            if indexed:image.putpalette([v for i in range(256)for v in(i,i,i)])
        else:
            item=self.resource(name);head=item.head()
            if head.startswith((b"\x89PNG",b"BM")):
                image=Image.open(io.BytesIO(item.read()));image.load()
                if not indexed:image=image.convert("RGBA")
            else:image,_=decoder.rgba(name)if alpha else decoder.pixels(name)
        if indexed and image.mode!="P":raise ValueError("native indexed resource required: "+name)
        path=self.folder/"decoded"/(hashlib.sha256(repr(marker).encode()).hexdigest()+".png")
        path.parent.mkdir(exist_ok=True);image.save(path)
        self.decoded_paths[marker]=path
        return path

    def graphics(self,request):
        op=request.split(" ",1)[0].split("\n",1)[0];self.operation_counts[op]+=1
        if op=="batch":
            raw=request.encode();at=raw.index(b"\n")+1
            while at<len(raw):
                end=raw.index(b"\n",at);count=int(raw[at:end]);at=end+1
                self.graphics(raw[at:at+count].decode());at+=count
            return "OK"
        if op in("load","load8"):
            operation,handle,alpha,name=request.split(" ",3)
            self.page_sources[int(handle)]=name
            request=" ".join((operation,handle,alpha,str(self.decoded(name,bool(int(alpha)),operation=="load8"))))
        elif op in("glyphs","text"):
            lines=request.split("\n")
            if len(lines)>1 and(lines[1]in("","sans-serif","serif","ＭＳ ゴシック","MS Gothic")
                                or lines[1].startswith("__majiro_style__")):
                lines[1]="Source Han Sans SC";request="\n".join(lines)
        elif op in("png","cachebmp"):
            parts=request.split(" ",2)
            if len(parts)==3 and parts[2].startswith(("save://","cache://")):
                request=" ".join((*parts[:2],str(self.local_path(parts[2]))))
        elif op in("present","presentview","presentzoom"):
            self.last_present_request=request
            state=self.system["state"]if self.system else None
            signature=(state["reading_generation"]or 0,
                       state["active_ui_script"]or"",
                       bool(state["ui_waiting"]),bool(state["reading_wait"]))if state else None
            # CPU composition at every new page/menu plus periodic transition
            # samples. Framebuffers are still updated by every preceding op.
            if signature==self.last_signature and self.clock-self.last_render<1000:return "OK"
            self.last_signature=signature
            return self.render()
        reply=self.native(request)
        if not reply.startswith("OK"):raise RuntimeError("native graphics "+op+" ["+request.split("\n")[0]+"]: "+reply)
        return reply

    def dialog(self,*args):
        action,token=args[:2]
        self.fixture_dialogs=getattr(self,"fixture_dialogs",{})
        if action=="begin":
            self.fixture_dialogs[token]=args[2]
            self.host_events.append({"kind":"native_dialog_cancel_fixture","flags":args[2]})
            return "pending"
        if action=="cancel":
            self.fixture_dialogs.pop(token,None);return "OK"
        flags=self.fixture_dialogs.pop(token,0)
        if flags>=65536:return "data "
        mode=flags%16
        return "result "+str(7 if mode==4 else 2 if mode in(1,5)else 1)

    def render(self):
        if not self.last_present_request:return "OK"
        header,separator,rows=self.last_present_request.partition("\n")
        head=header.rsplit(" ",1)[0]
        request=head+" "+str(self.folder/"current.png")+separator+rows
        reply=self.native(request)
        if not reply.startswith("OK"):raise RuntimeError("native composition: "+reply)
        self.last_render=self.clock;self.rendered_frames+=1
        return reply

    def capture(self,label):
        self.render()
        source=self.folder/"current.png"
        if not source.exists():return {"label":label,"image_available":False}
        target=self.folder/(re.sub(r"[^A-Za-z0-9_-]","_",label)+".png")
        shutil.copy2(source,target)
        with Image.open(target)as image:
            rgb=image.convert("RGB");extrema=rgb.getextrema()
            nonempty=bool(rgb.getbbox())
        state=self.system["state"]
        sprites=[]
        for ident in state["sprite_order"].values():
            sprite=state["sprites"][ident]
            if sprite:
                row=table_value(sprite);row["page_source"]=self.page_sources.get(sprite["page"])
                sprites.append(row)
        result={"label":label,"image":str(target),"nonblack":nonempty,"extrema":extrema,
                "vm_status":self.vm["status"],"steps":self.vm["steps"],
                "reading_generation":state["reading_generation"]or 0,
                "active_ui_script":state["active_ui_script"],"sprite_count":len(sprites)}
        (target.with_suffix(".json")).write_text(json.dumps(result|{"sprites":sprites},ensure_ascii=True,indent=2),encoding="utf8")
        self.snapshots.append(result)
        return result
