"""Small native license error-boundary comparisons, without external validation."""
from __future__ import annotations
import json
import struct
from pathlib import Path
import sys
from original_calls import CallOracle
from unicorn import x86_const as reg
from lua_bridge import create_lua
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,write_json


class LicenseOracle(CallOracle):
    phase=1
    def _import(self,uc,at,size,unused):
        name=self.import_names.get(at);esp=uc.reg_read(reg.UC_X86_REG_ESP)
        arg=lambda i:self.read_u32(esp+4+i*4)
        if name=="RegOpenKeyExA":
            if self.phase==1:self._return(5,2)
            else:self.write_u32(arg(4),0x1234);self._return(5,0)
            return
        if name=="RegQueryValueExA":
            if self.phase==2:self._return(6,2)
            else:
                data=self.DATA+0x60000
                uc.mem_write(arg(4),bytes(32));self.write_u32(arg(5),32);self._return(6,0)
            return
        if name=="RegCloseKey":self._return(1);return
        if name=="CryptAcquireContextA":
            self.write_u32(arg(0),0x2345);self._return(5,int(self.phase!=3));return
        if name=="CryptGetUserKey":
            self.write_u32(arg(2),0x3456);self._return(3,int(self.phase!=4));return
        if name=="CryptDecrypt":
            if self.phase==5:self._return(6,0)
            else:
                data=b"LICENSENO:"if self.phase==0 else b"xxxxxxxxxx"
                uc.mem_write(arg(4),data);self.write_u32(arg(5),len(data));self._return(6,1)
            return
        if name=="CryptDestroyKey":self._return(1,1);return
        if name=="CryptReleaseContext":self._return(2,1);return
        return super()._import(uc,at,size,unused)


def run():
    sample=json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf8"))["samples"][0]
    original=LicenseOracle(sample)
    lua=create_lua();module=lua.execute((ROOT/"player/runtime/majiro_external.lua").read_text(encoding="utf8"))
    rows=[]
    for phase in(1,2,3,4,5,0,20):
        original.phase=phase
        got=original.execute("syscall",[(2,original.string("fixture",0))],
            struct.pack("<IH",0x87a631a6,1),budget=300000)
        expected=phase if phase in(1,2,3,4,5)else 0 if phase==0 else 20
        assert got==[(0,expected)],(phase,got)
        providers=lua.table();define=lua.eval("function(p)return function(k,v)p[k]=v end end")(providers)
        record={"registry_open":phase!=1,"query":phase!=2,"crypto_acquire":phase!=3,
                "user_key":phase!=4,"decrypt":phase!=5,
                "plaintext":"LICENSENO:"if phase==0 else"xxxxxxxxxx"}
        options=lua.table_from({"external_controls":{"license":"cryptoapi_user_license"}},recursive=True)
        options["license"]=lambda action,name:lua.table_from(record)
        module["install"](define,lua.table(),options)
        assert providers[0x87a631a6](lua.table(),lua.table_from(["fixture"]))==expected
        rows.append({"phase":phase,"result":expected})
    report={"status":"passed","timestamp":timestamp(),"cases":rows,"exe_sha256":sample["exe_sha256"],
        "scope":"actual original CryptoAPI control flow with explicit API-result fixtures; no validation is bypassed",
        "checker":{"available":False,"required_callback_message":"0x9004",
                   "status":"external dispatch implemented; authentication backend remains unavailable"}}
    folder=archive_dir("external_checks")
    write_json(folder/(folder.name[:8]+"_external_checks.json"),report)
    write_json(ROOT/"reports/external_check_tests.json",report)
    print("legacy license statuses: 7 original boundary cases passed")


if __name__=="__main__":run()
