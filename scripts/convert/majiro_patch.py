"""Read the user's pinned translation ZIP directly, without extraction or injection."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
from pathlib import Path
import zipfile
from port_common import ROOT,read_json


@dataclass(frozen=True)
class ZipMember:
    archive: Path
    entry: str
    name: str
    size: int
    sha256: str
    offset: int=0
    name_hash: int=0
    index: int=0
    extracted: Path | None=None

    @property
    def raw_name(self):return self.name.encode("cp932")
    @property
    def identity(self):return "translation:"+self.entry
    @property
    def extension(self):return Path(self.name).suffix.lower()
    def read(self,limit=64*1024*1024):
        if self.size>limit:raise ValueError("translation member exceeds read budget")
        if self.extracted is not None:
            data=self.extracted.read_bytes()
        else:
            with zipfile.ZipFile(self.archive)as z:data=z.read(self.entry)
        if len(data)!=self.size:
            raise ValueError("translation member size changed: "+self.name)
        if hashlib.sha256(data).hexdigest()!=self.sha256:
            raise ValueError("translation member changed: "+self.name)
        return data
    def chunks(self,block=1024*1024):
        data=self.read()
        for offset in range(0,len(data),block):yield data[offset:offset+block]
    def digest(self):
        self.read()
        return self.sha256


def tunnel_table(text):
    table={}
    for index,char in enumerate(text):
        high,low=divmod(index,0x3a)
        high=(0x81+high)if high<0x1f else(0xe0+high-0x1f)
        low+=1
        for excluded in (9,10,13,32,44):
            if low>=excluded:low+=1
        table[bytes((high,low))]=char
    return table


class Patch:
    def __init__(self,root=ROOT):
        self.root=root
        self.lock=read_json(root/"localization/patch.lock.json")
        self.path=Path(self.lock["zip"])
        source_config=root/"config/translation_source.json"
        extracted=None
        if source_config.exists():
            extracted=(root/read_json(source_config)["extracted_scenario"]).resolve(strict=True)
        self.members=[ZipMember(self.path,r["entry"],r["name"],r["bytes"],r["sha256"],index=i)
                      for i,r in enumerate(self.lock["overrides"])]
        if extracted is not None:
            from dataclasses import replace
            self.members=[replace(m,extracted=extracted/m.name) for m in self.members]
        self.tunnel=tunnel_table(self.lock["tunnel_characters"])
        self.relocations=read_json(root/"localization/relocations.json")if(root/"localization/relocations.json").exists()else {}
        self.stack_repairs=read_json(root/"localization/stack_repairs.json")if(root/"localization/stack_repairs.json").exists()else {}
    def decode(self,data):
        output=[];index=0
        while index<len(data):
            pair=data[index:index+2]
            if pair in self.tunnel:
                output.append(self.tunnel[pair]);index+=2
            elif 0x81<=data[index]<=0x9f or 0xe0<=data[index]<=0xfc:
                output.append(pair.decode("cp932"));index+=2
            else:
                output.append(data[index:index+1].decode("cp932"));index+=1
        return "".join(output)
    def encode(self,text):
        inverse={char:pair for pair,char in self.tunnel.items()}
        return b"".join(inverse[char]if char in inverse else char.encode("cp932")for char in text)
    def repairs(self,name,sha):
        record=self.relocations.get(name)
        if not record:return None
        if record["patch_sha256"]!=sha:raise ValueError("translation relocation source changed")
        return record["repairs"]
    def preludes(self,name,sha):
        record=self.stack_repairs.get(name)
        if not record:return None
        if record["patch_sha256"]!=sha:raise ValueError("translation prelude source changed")
        return record["repairs"]
