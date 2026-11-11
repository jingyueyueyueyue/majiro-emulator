"""Verify original page-load boundaries and actual mismatched RCT/RC8 assets."""
from pathlib import Path
import json
import subprocess
import sys
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/"build"),
              str(Path(__file__).resolve().parents[1]/"convert")]
from build_player_core import msvc_environment
from generate_portable_pixels import generate
from majiro_arc import Catalogue
from port_common import ROOT,archive_dir,read_json,sha256,timestamp,write_json
from test_new_graphics_abi import FunctionFixture
from audit_player_semantics import registered_providers


def original_missing_pages():
    samples=read_json(ROOT/"reports/opcode_dispatch.json")["samples"]
    rows=[]
    for label,normal,alpha,access,result,resolver,load,alloc32,alloc8,probe,cookie in [
        ("BOKUTSUBA",0x419450,0x4195d0,0x41d960,0x41d980,0x4180f0,0x418730,
         0x43fed0,0x440040,None,None),
        ("narci_sumire",0x44ac20,0x44acd0,0x44e730,0x44e550,0x447a50,0x447720,
         0x4746b0,0x4748b0,0x480030,0x48a287),
    ]:
        sample=next(s for s in samples if s["sample"]==label)
        for root in(normal,alpha):
            for indexed in(False,True):
                f=FunctionFixture(sample)
                text=0x10020000
                f.uc.mem_write(text,bytes(12)+b"absent"+(b"_"if indexed else b"")+b"\0")
                f.hook(access,lambda sp:f.top)
                f.hook(result,lambda sp:setattr(f,"result",f.read(sp+4))or 0)
                f.hook(resolver,lambda sp:0xffffffff)
                def reject(sp):
                    raise AssertionError("original decoded an absent file")
                f.hook(load,reject)
                allocations=[]
                def allocate(depth):
                    def call(sp):
                        args=[f.read(sp+i*4)for i in(1,2,3)]
                        allocations.append((depth,args));return 0x10040000
                    return call
                f.hook(alloc32,allocate(32));f.hook(alloc8,allocate(8))
                if probe:f.hook(probe,lambda sp:int(indexed))
                if cookie:f.hook(cookie,lambda sp:0)
                # Missing-file logging and CRT underscore search are explicit
                # boundaries; the branch/allocation/result paths are original.
                if label=="BOKUTSUBA":
                    f.hook(0x40a110,lambda sp:0)
                    f.hook(0x451215,lambda sp:1 if indexed else 0)
                else:f.hook(0x440d60,lambda sp:0)
                f.call(root,[text])
                assert allocations==[(8 if indexed else 32,[1,1,0])],allocations
                assert f.result==0x10040000
                rows.append({"sample":label,"wrapper":hex(root),"indexed":indexed,
                             "dimensions":[1,1],"status":"passed"})
    return rows


def original_mask_association():
    sample=next(s for s in read_json(ROOT/"reports/opcode_dispatch.json")["samples"]if s["sample"]=="BOKUTSUBA")
    rows=[]
    for width,height,indexed in((150,300,True),(150,200,True),(149,300,True),(150,300,False)):
        f=FunctionFixture(sample);owner,mask=0x10030000,0x10031000
        f.put(owner,150);f.put(owner+4,300);f.put(owner+0x1c,0x12345678)
        f.put(mask,width);f.put(mask+4,height);f.put(mask+0x10,1 if indexed else 0)
        f.hook(0x41d960,lambda sp:f.top)
        f.hook(0x41d980,lambda sp:setattr(f,"result",f.read(sp+4))or 0)
        f.hook(0x440120,lambda sp:0);f.hook(0x40a110,lambda sp:0)
        f.call(0x41aab0,[owner,mask])
        accepted=indexed and width==150 and height==300
        assert f.read(owner+0x1c)==(mask if accepted else 0x12345678)
        assert f.result==0
        commands=[]
        lua,_,_,system=registered_providers(overrides={"graphics":lambda command:commands.append(command)or"OK"})
        state=system["state"];state["pages"][1]=lua.table_from({
            "width":150,"height":300,"depth":32,"antidata":99})
        state["pages"][2]=lua.table_from({"width":width,"height":height,"depth":8 if indexed else 32})
        assert system["providers"]["$page_set_antidata"](lua.table(),lua.table_from([1,2]))==0
        assert state["pages"][1]["antidata"]==(2 if accepted else 99)
        assert len(commands)==int(accepted)
        rows.append({"mask":[width,height],"indexed":indexed,"associated":accepted,"status":"passed"})
    return rows


def main():
    folder=archive_dir("page_image_loading")
    game=next(g for g in read_json(ROOT/"config/player_samples.json")["games"]if g["id"]=="BOKUTSUBA")
    catalogue=Catalogue(Path(game["directory"]),include_patch=False)
    color=catalogue.get("\u30dc\u30bf\u30f3\u8aac\u660e.rct")
    mask=catalogue.get("\u30dc\u30bf\u30f3\u8aac\u660e_.rc8")
    (folder/"color.rct").write_bytes(color.read());(folder/"mask.rc8").write_bytes(mask.read())
    generated=folder/"pixels.cpp";generate(generated)
    env=msvc_environment();compiler=Path(env["VCTOOLSINSTALLDIR"])/"bin/Hostx64/x64/cl.exe"
    def run(name,args):
        result=subprocess.run(args,cwd=folder,env=env,capture_output=True,timeout=60)
        (folder/(name+".txt")).write_bytes(result.stdout+result.stderr)
        if result.returncode:raise RuntimeError(name+" failed: "+str(folder/(name+".txt")))
        return result
    run("compile",[str(compiler),"/nologo","/std:c++20","/EHsc","/O2","/utf-8",
        "/I"+str(ROOT/"player/core"),str(generated),str(ROOT/"player/core/majiro_image.cpp"),
        str(ROOT/"scripts/analysis/page_image_loading_probe.cpp"),"/Fe:"+str(folder/"probe.exe")])
    report=json.loads(run("run",[str(folder/"probe.exe")]).stdout)
    report.update(timestamp=timestamp(),original_missing_pages=original_missing_pages(),
        original_mask_association=original_mask_association(),
        resource_hashes=[color.digest(),mask.digest()],device_accessed=False,
        evidence=str(folder.relative_to(ROOT)),source_sha256={name:sha256(ROOT/name)for name in(
            "player/core/image_resource.hpp","player/core/portable_pixels_wrapper.inc",
            "player/core/portable_pixels.hpp","player/android/game_host.cpp")})
    write_json(ROOT/"reports/page_image_loading_tests.json",report)
    write_json(folder/"report.json",report)
    for path in [folder/"probe.exe",*folder.glob("*.obj")]:path.unlink(missing_ok=True)
    print(json.dumps(report,ensure_ascii=True))
    return 0


if __name__=="__main__":raise SystemExit(main())
