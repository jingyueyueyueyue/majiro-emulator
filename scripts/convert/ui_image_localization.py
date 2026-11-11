"""Deterministic UI lettering; preserve artwork and share the resulting PNG once."""
from __future__ import annotations
import hashlib
import io
import os
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
from port_common import ROOT,read_json,sha256,atomic_bytes


def specification(name):
    path=ROOT/"localization/ui_images.json"
    if not path.exists():return None
    data=read_json(path)
    for group in data["groups"]:
        if name.casefold()in [n.casefold()for n in group["images"]]:
            return data,group
    return None


def identity():
    path=ROOT/"localization/ui_images.json"
    if not path.exists():return None
    return hashlib.sha256((sha256(path)+sha256(Path(__file__))+sha256(
        Path(__file__).with_name("majiro_image.py"))).encode()).hexdigest()


def companion_name(name):
    """The original scripts sometimes attach the RC8 page explicitly."""
    if name.lower().endswith("_.rc8"):
        parent=name[:-5]+".rct"
        if specification(parent):
            return parent
    return None


def apply(image,name):
    spec=specification(name)
    if not spec:return image,None
    data,group=spec
    font_path=ROOT/data["font"]
    result=image.convert("RGBA")
    replacements=group.get("substitutions",{})
    for record in group["labels"]:
        if isinstance(record,list):
            box,text=record[:2]
            record={"box":box,"text":text,**({"size":record[2]}if len(record)>2 else {})}
        text=record["text"]
        for old,new in replacements.items():text=text.replace(old,new)
        x0,y0,x1,y1=record["box"]
        if not(0<=x0<x1<=image.width and 0<=y0<y1<=image.height):
            raise ValueError("UI label outside source image: "+name)
        erase=record.get("erase",group.get("erase","strip"))
        pixels=result.load()
        if erase=="clear":
            ImageDraw.Draw(result).rectangle((x0,y0,x1-1,y1-1),fill=(0,0,0,0))
        elif erase=="inpaint":
            import cv2
            import numpy as np
            # For patterned header bands, erase the light source lettering
            # and its outline, preserving the surrounding decorative texture.
            crop=np.asarray(result.crop((x0,y0,x1,y1))).copy()
            core=(crop[:,:,:3].min(axis=2)>155)&(crop[:,:,3]>80)
            mask=cv2.dilate(core.astype("uint8")*255,np.ones((9,9),dtype="uint8"))
            padded=np.asarray(result.crop((max(0,x0-5),max(0,y0-5),
                                           min(result.width,x1+5),min(result.height,y1+5)))).copy()
            ox=x0-max(0,x0-5);oy=y0-max(0,y0-5)
            full=np.zeros(padded.shape[:2],dtype="uint8")
            full[oy:oy+y1-y0,ox:ox+x1-x0]=mask
            restored=cv2.inpaint(padded[:,:,:3],full,3,cv2.INPAINT_TELEA)
            crop[:,:,:3]=restored[oy:oy+y1-y0,ox:ox+x1-x0]
            restored_alpha=cv2.inpaint(padded[:,:,3],full,3,cv2.INPAINT_TELEA)
            crop[:,:,3]=restored_alpha[oy:oy+y1-y0,ox:ox+x1-x0]
            result.paste(Image.fromarray(crop),(x0,y0))
        elif erase=="strip":
            # The source buttons use horizontal scanlines and vertical
            # gradients. Reconstruct each scanline from its clean endpoints,
            # preserving those rows instead of smearing them vertically.
            for y in range(y0,y1):
                left=pixels[max(0,x0-1),y]
                right=pixels[min(result.width-1,x1),y]
                for x in range(x0,x1):
                    weight=(x-x0+1)/(x1-x0+1)
                    pixels[x,y]=tuple(round(a+(b-a)*weight)for a,b in zip(left,right))
        else:raise ValueError("unknown UI lettering erase method")
        if not text:continue
        size=record.get("size",group.get("size",20))
        vertical=record.get("vertical",False)
        lines=list(text)if vertical else text.split("\n")
        color=record.get("color",group.get("color","#FFFFFF"))
        stroke=record.get("stroke",group.get("stroke","#294B73"))
        stroke_width=record.get("stroke_width",group.get("stroke_width",1))
        scale=3
        while True:
            font=ImageFont.truetype(str(font_path),round(size*scale))
            bounds=[font.getbbox(line,stroke_width=stroke_width*scale)for line in lines]
            width=max(b[2]-b[0]for b in bounds)
            total=sum(b[3]-b[1]for b in bounds)+max(0,len(lines)-1)*round(size*0.25*scale)
            if (width<=(x1-x0)*scale-2 and total<=(y1-y0)*scale)or size<=8:break
            size-=0.5
        overlay=Image.new("RGBA",((x1-x0)*scale,(y1-y0)*scale))
        draw=ImageDraw.Draw(overlay)
        y=((y1-y0)*scale-total)/2
        for line,b in zip(lines,bounds):
            x=((x1-x0)*scale-(b[2]-b[0]))/2-b[0]
            draw.text((round(x),round(y-b[1])),line,font=font,fill=color,
                      stroke_width=stroke_width*scale,stroke_fill=stroke)
            y+=b[3]-b[1]+round(size*0.25*scale)
        overlay=overlay.resize((x1-x0,y1-y0),Image.Resampling.LANCZOS)
        result.alpha_composite(overlay,(x0,y0))
    return result,{"identity":identity(),"labels":len(group["labels"]),
                   "source":"localization/ui_images.json","font_sha256":sha256(font_path)}


def write_shared(image,target):
    formal=ROOT/"localization/images"/target.name
    output=io.BytesIO();image.save(output,format="PNG",compress_level=6)
    atomic_bytes(formal,output.getvalue())
    target.parent.mkdir(parents=True,exist_ok=True)
    if not target.exists()or not os.path.samefile(formal,target):
        temporary=target.with_name(target.name+".link-tmp")
        try:
            os.link(formal,temporary);os.replace(temporary,target)
        finally:
            if temporary.exists():temporary.unlink()
