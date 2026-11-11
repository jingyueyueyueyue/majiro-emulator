"""Stable PE implementation identity excluding headers, resources and overlays."""
from __future__ import annotations
import hashlib
import struct

PREFIX = b"MAJIRO-CODE-V1\0"


def code_identity(data):
    def u16(at):
        if not 0 <= at <= len(data)-2:raise ValueError("truncated PE word")
        return struct.unpack_from("<H",data,at)[0]
    def u32(at):
        if not 0 <= at <= len(data)-4:raise ValueError("truncated PE dword")
        return struct.unpack_from("<I",data,at)[0]
    if len(data)>128*1024*1024 or data[:2]!=b"MZ":raise ValueError("unsupported PE input")
    pe=u32(0x3c)
    if data[pe:pe+4]!=b"PE\0\0":raise ValueError("PE signature missing")
    machine,count=u16(pe+4),u16(pe+6)
    if machine!=0x14c or not 0<count<=96:raise ValueError("unsupported PE machine/section count")
    optional=pe+24;optional_size=u16(pe+20)
    if u16(optional)!=0x10b or optional_size<144:raise ValueError("unsupported PE optional header")
    image_base=u32(optional+28)
    sections=[]
    for index in range(count):
        at=optional+optional_size+index*40
        name=data[at:at+8].rstrip(b"\0")
        virtual,rva,size,offset=u32(at+8),u32(at+12),u32(at+16),u32(at+20)
        length=virtual or size
        if length>64*1024*1024 or offset+size>len(data):raise ValueError("PE section outside input")
        sections.append({"name":name,"rva":rva,"length":length,"size":size,
                         "offset":offset,"flags":u32(at+36)})
    selected=[s for s in sections if s["flags"]&0x20000000]
    if not selected:
        selected=[s for s in sections if s["name"]==b".text"]
    if not selected:raise ValueError("PE has no identified implementation section")
    selected.sort(key=lambda s:s["rva"])
    images=[]
    for section in selected:
        copied=min(section["length"],section["size"])
        images.append((section,bytearray(data[section["offset"]:section["offset"]+copied])+
                       bytearray(section["length"]-copied)))
    def offset(rva,size):
        for section in sections:
            relative=rva-section["rva"]
            if 0<=relative and relative+size<=section["size"]:return section["offset"]+relative
        raise ValueError("PE relocation directory outside stored sections")
    # Normalize only loader-declared HIGHLOW addresses inside selected code.
    # Constants/immediates without relocations remain part of the identity.
    relocation_rva,relocation_bytes=u32(optional+136),u32(optional+140)
    if relocation_bytes:
        cursor=offset(relocation_rva,relocation_bytes);end=cursor+relocation_bytes
        while cursor<end:
            if cursor+8>end:raise ValueError("truncated PE relocation block")
            page,block=u32(cursor),u32(cursor+4)
            if block<8 or block%2 or cursor+block>end:raise ValueError("invalid PE relocation block")
            for at in range(cursor+8,cursor+block,2):
                entry=u16(at);kind,relative=entry>>12,entry&4095
                target=page+relative
                for section,image in images:
                    index=target-section["rva"]
                    if not 0<=index<len(image):continue
                    if kind==0:continue
                    if kind!=3 or index+4>len(image):
                        raise ValueError("unsupported code relocation kind")
                    value=struct.unpack_from("<I",image,index)[0]
                    struct.pack_into("<I",image,index,(value-image_base)&0xffffffff)
            cursor+=block
    canonical=bytearray(PREFIX)+struct.pack("<HH",machine,len(images))
    for section,image in images:
        canonical+=struct.pack("<II",section["rva"],len(image))+image
    return {"identity":"code-sha256-v1:"+hashlib.sha256(canonical).hexdigest(),
            "code_sections":[{"name":s["name"].decode("ascii",errors="replace"),
                              "rva":s["rva"],"bytes":len(b)}for s,b in images],
            "canonical_bytes":len(canonical),"format":"MAJIRO-CODE-V1"}
