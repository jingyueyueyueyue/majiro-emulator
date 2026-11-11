"""Check generic version/API discovery on real engines and no-EXE/mutated inputs."""
from pathlib import Path
import json
import re
import struct
import subprocess
import sys
import pefile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,read_json,write_json,timestamp,sha256
from inspect_engine_feature_paths import EVIDENCE


def boundary_inputs(registry, folder, observed):
    cases=[]
    raw=Path(registry[0]["path"]).read_bytes()
    baseline=observed[registry[0]["sample"]]
    pe=pefile.PE(data=raw)
    def add(name, data, expected):
        path=folder/("20261111_"+name+".exe")
        path.write_bytes(data)
        cases.append((name,path,expected))
    bait=b"MajiroObjX1.000\0MajiroSavV1.105\0\x3d\x9d\x70\xd1\x2b\x74\x00"
    add("non_pe_markers",bait,set())
    add("non_pe_mz_markers",b"MZ"+bytes(58)+struct.pack("<I",0x7fffffff)+bait,set())
    add("overlay_false_generation_and_api",raw+bait,baseline)
    add("overlay_other_valid_engine",raw+Path(registry[-1]["path"]).read_bytes(),baseline)
    resource=next(s for s in pe.sections if s.Name.rstrip(b"\0")==b".rsrc")
    changed=bytearray(raw)
    end=resource.PointerToRawData+resource.SizeOfRawData
    changed[end-len(bait):end]=bait
    add("resource_false_generation_and_api",changed,baseline)
    text=next(s for s in pe.sections if s.Name.rstrip(b"\0")==b".text")
    changed=bytearray(raw)
    struct.pack_into("<I",changed,text.get_file_offset()+36,0xC0000040)
    add("text_section_data_flags",changed,baseline)
    mutations=(
        ("dos_offset_negative",60,"<I",0xffffffff),
        ("dos_offset_past_file",60,"<I",len(raw)+1),
        ("pe_signature",pe.DOS_HEADER.e_lfanew,"<I",0),
        ("wrong_machine",pe.DOS_HEADER.e_lfanew+4,"<H",0x8664),
        ("no_sections",pe.DOS_HEADER.e_lfanew+6,"<H",0),
        ("excess_sections",pe.DOS_HEADER.e_lfanew+6,"<H",97),
        ("short_optional_header",pe.DOS_HEADER.e_lfanew+20,"<H",95),
        ("not_pe32",pe.DOS_HEADER.e_lfanew+24,"<H",0x20b),
        ("raw_pointer_overflow",text.get_file_offset()+20,"<I",0x7fffffff),
        ("raw_size_overflow",text.get_file_offset()+16,"<I",0x7fffffff),
        ("virtual_size_negative",text.get_file_offset()+8,"<I",0xffffffff),
        ("virtual_address_negative",text.get_file_offset()+12,"<I",0xffffffff),
    )
    for name,at,encoding,value in mutations:
        changed=bytearray(raw);struct.pack_into(encoding,changed,at,value)
        add(name,changed,set())
    for size in (0,1,2,63,64,pe.DOS_HEADER.e_lfanew+24,text.get_file_offset()+39):
        add("truncated_"+str(size),raw[:size]+bait,set())
    # A string and an isolated PUSH in unused executable padding must not
    # invent an X control. No reachable original instruction is modified.
    old=next(s for s in registry if s["sample"]=="kizoku")
    changed=bytearray(Path(old["path"]).read_bytes());oldpe=pefile.PE(data=changed)
    oldtext=next(s for s in oldpe.sections if s.Name.rstrip(b"\0")==b".text")
    oldresource=next(s for s in oldpe.sections if s.Name.rstrip(b"\0")==b".rsrc")
    assert oldtext.SizeOfRawData-oldtext.Misc_VirtualSize>=6
    pointer=oldresource.PointerToRawData+oldresource.SizeOfRawData-16
    changed[pointer:pointer+5]=b"\0x%s\0"
    address=oldpe.OPTIONAL_HEADER.ImageBase+oldresource.VirtualAddress+pointer-oldresource.PointerToRawData+1
    at=oldtext.PointerToRawData+oldtext.Misc_VirtualSize
    changed[at:at+6]=b"\x68"+struct.pack("<I",address)+b"\xc3"
    add("isolated_x_format_push",changed,observed[old["sample"]])
    # Structurally readable PE whose CALL operand straddles code/data sections.
    # It must not use the following section as instruction bytes.
    tiny=bytearray(0x320);tiny[:2]=b"MZ";struct.pack_into("<I",tiny,60,0x80)
    tiny[0x80:0x84]=b"PE\0\0";struct.pack_into("<HH",tiny,0x84,0x14c,2)
    struct.pack_into("<H",tiny,0x94,0xe0);struct.pack_into("<H",tiny,0x98,0x10b)
    struct.pack_into("<I",tiny,0xb4,0x400000)
    for name,at,rva,size,offset,flags in (
        (b".text",0x178,0x1000,0x20,0x200,0x60000020),
        (b".data",0x1a0,0x2000,0x100,0x220,0xc0000040)):
        tiny[at:at+len(name)]=name
        struct.pack_into("<IIII",tiny,at+8,size,rva,size,offset)
        struct.pack_into("<I",tiny,at+36,flags)
    tiny[0x200:0x220]=b"\x90"*0x20
    tiny[0x200:0x20b]=b"\x3d"+struct.pack("<I",0x45815f6d)+b"\x0f\x84"+struct.pack("<i",20)
    tiny[0x212]=0xc3;tiny[0x21f]=0xe8;struct.pack_into("<i",tiny,0x220,-18)
    marker=b"MajiroObjX1.000\0MajiroSavV1.103\0"
    tiny[0x230:0x230+len(marker)]=marker
    add("truncated_code_call",tiny,{"save:103"})
    return cases


def main():
    folder=EVIDENCE/(timestamp().replace(":", "").replace("+", "_")+"_features")
    folder.mkdir(parents=True,exist_ok=False)
    java=Path(read_json(ROOT/"reports/android_toolchain.json")["java"])/"bin"
    production=ROOT/"player/android/src/org/majiro/player/EngineFeatures.java"
    fixture=ROOT/"scripts/analysis/EngineFeaturesChecks.java"
    def run(name,args):
        result=subprocess.run(args,cwd=folder,capture_output=True,timeout=40)
        (folder/(name+".txt")).write_bytes(result.stdout+result.stderr)
        if result.returncode:raise RuntimeError(name+" failed: "+str(folder/(name+".txt")))
        return result.stdout.decode("utf8")
    run("compile",[str(java/"javac.exe"),"-encoding","UTF-8","-d",str(folder),str(production),str(fixture)])
    registry=read_json(ROOT/"reports/opcode_dispatch.json")["samples"]
    dispatch={s["exe_sha256"]:{e["hash"]for e in s["entries"]}
              for s in read_json(ROOT/"reports/syscall_dispatch.json")["samples"]}
    samples=[(row["sample"],Path(row["path"]))for row in registry]
    changed=folder/"changed.exe"
    changed.write_bytes(samples[-1][1].read_bytes()+b"arbitrary resource changes are not an engine identity")
    samples.append(("modified_not_whitelisted",changed))
    empty=folder/"empty.exe";empty.write_bytes(b"not an EXE");samples.append(("no_version_hints",empty))
    listing=folder/"inputs.txt";listing.write_text("\n".join(str(p)for _,p in samples)+"\n",encoding="utf8")
    actual=run("run",[str(java/"java.exe"),"-Dfile.encoding=UTF-8","-cp",str(folder),
        "org.majiro.player.EngineFeaturesChecks",str(listing)]).splitlines()
    assert len(actual)==len(samples)
    cases=[]
    observed_by_sample={}
    for(name,path),value in zip(samples,actual):
        raw=path.read_bytes();expected=set()
        if name!="no_version_hints":
            pe=pefile.PE(data=raw)
            mapped=b"".join(section.get_data()for section in pe.sections)
            identity=registry[-1]["exe_sha256"]if name=="modified_not_whitelisted"else next(
                s["exe_sha256"]for s in registry if s["sample"]==name)
            expected.update("save:"+v.decode()for v in re.findall(rb"MajiroSavV1\.(10[2-5])",mapped))
            for api in(0x2bd1709d,0x7e0b8320,0x87a631a6,0xf63ed9ae,
                       0x15d07df8,0xc102bcc3,0xa361d9f7,0x3670b283):
                if format(api,"08x")in dispatch[identity]:
                    expected.add("api:"+format(api,"x"))
        observed=set(value.split(","))if value else set()
        observed_by_sample[name]=observed
        assert {x for x in observed if x.startswith(("save:","api:"))}==expected,(name,value,expected)
        cases.append({"sample":name,"features":sorted(observed),"status":"passed"})
    assert cases[-2]["features"]==cases[-3]["features"]
    boundaries=boundary_inputs(registry,folder,observed_by_sample)
    boundary_listing=folder/"20261111_boundary_inputs.txt"
    boundary_listing.write_text("\n".join(str(p)for _,p,_ in boundaries)+"\n",encoding="utf8")
    checked=run("boundaries",[str(java/"java.exe"),"-Dfile.encoding=UTF-8","-cp",str(folder),
        "org.majiro.player.EngineFeaturesChecks",str(boundary_listing)]).splitlines()
    assert len(checked)==len(boundaries)
    boundary_rows=[]
    for(name,path,expected),value in zip(boundaries,checked):
        observed=set(value.split(","))if value else set()
        assert observed==expected,(name,observed,expected)
        boundary_rows.append({"name":name,"status":"passed","features":sorted(observed)})
    report={"status":"passed","timestamp":timestamp(),"cases":cases,"exe_fingerprint_validation":False,
        "boundary_cases":boundary_rows,"boundary_case_count":len(boundary_rows),
        "version_104_discovered":True,"evidence":str(folder.relative_to(ROOT)),
        "source_sha256":sha256(production),"device_accessed":False}
    write_json(ROOT/"reports/engine_version_features_tests.json",report);write_json(folder/"report.json",report)
    for _,path,_ in boundaries:path.unlink()
    changed.unlink();empty.unlink()
    print("Engine version/API discovery:",len(cases),"cases;",len(boundary_rows),
          "boundary cases; changed EXE remains recognized without hashes")
    # Reuse this compilation for the narrow embedded-fallback regression.
    from test_engine_embedded_ambiguity import main as embedded_ambiguity
    result=embedded_ambiguity(classes=folder,java=java)
    if result:return result
    # The registered production-feature suite also verifies source selection
    # and that discovered availability reaches real runtime providers.
    from test_engine_source_selection import main as source_selection
    return source_selection()


if __name__=="__main__":raise SystemExit(main())
