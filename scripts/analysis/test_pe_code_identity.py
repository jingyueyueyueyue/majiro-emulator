"""Verify implementation identity ignores resource/header edits and normalizes relocation."""
import hashlib
import json
import struct
from pathlib import Path
import sys
from pe_code_identity import code_identity
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,write_json


def fixture():
    data=bytearray(0x800);data[:2]=b"MZ";struct.pack_into("<I",data,0x3c,0x80)
    data[0x80:0x84]=b"PE\0\0";struct.pack_into("<HH",data,0x84,0x14c,3)
    struct.pack_into("<H",data,0x94,0xe0);optional=0x98
    struct.pack_into("<H",data,optional,0x10b);struct.pack_into("<I",data,optional+28,0x400000)
    struct.pack_into("<II",data,optional+136,0x3000,12)
    for index,(name,rva,offset,flags)in enumerate([
            (b".text",0x1000,0x200,0x60000020),(b".rsrc",0x2000,0x400,0x40000040),
            (b".reloc",0x3000,0x600,0x42000040)]):
        at=optional+0xe0+index*40;data[at:at+8]=name.ljust(8,b"\0")
        struct.pack_into("<4I",data,at+8,0x100,rva,0x200,offset)
        struct.pack_into("<I",data,at+36,flags)
    data[0x200:0x208]=b"\xb8\0\0\0\0\xc3\x90\x90";struct.pack_into("<I",data,0x201,0x401234)
    struct.pack_into("<IIHH",data,0x600,0x1000,12,0x3001,0)
    return data


def main():
    raw=fixture();identity=code_identity(raw)["identity"];checks=[]
    modified=bytearray(raw);modified[0x88:0x8c]=b"date"
    modified[0x400:0x480]=b"R"*0x80;modified+=b"overlay";modified[0x310]=123
    assert code_identity(modified)["identity"]==identity
    checks.append("timestamp/resources/raw padding/overlay changes preserve identity")
    relocated=bytearray(raw);struct.pack_into("<I",relocated,0x98+28,0x500000)
    struct.pack_into("<I",relocated,0x201,0x501234)
    assert code_identity(relocated)["identity"]==identity
    checks.append("loader HIGHLOW image-base relocation normalizes")
    patched=bytearray(raw);patched[0x207]^=1
    assert code_identity(patched)["identity"]!=identity
    checks.append("real code changes alter identity")
    noexecute=bytearray(raw)
    for i in range(3):struct.pack_into("<I",noexecute,0x178+i*40+36,0xc0000040)
    assert code_identity(noexecute)["identity"]==identity
    checks.append("known .text remains identifiable when execute flags are omitted")
    failed=False
    invalid=bytearray(raw);struct.pack_into("<I",invalid,0x604,0xffffffff)
    try:code_identity(invalid)
    except ValueError:failed=True
    assert failed;checks.append("truncated/malformed relocations reject")
    registry=json.loads((ROOT/"reports/engine_code_profiles.json").read_text(encoding="utf8"))
    for row in registry["sources"]:
        assert code_identity(Path(row["source"]).read_bytes())["identity"]==row["identity"]
    checks.append("all sixteen recovered build identities reproduce")
    folder=archive_dir("pe_code_identity_tests")
    report={"schema":1,"timestamp":timestamp(),"status":"passed","checks":checks,
            "fixture_identity":identity,"verified_builds":len(registry["sources"]),
            "device_accessed":False,"evidence":str(folder.relative_to(ROOT)),
            "source_sha256":hashlib.sha256((ROOT/"scripts/analysis/pe_code_identity.py").read_bytes()).hexdigest()}
    write_json(ROOT/"reports/pe_code_identity_tests.json",report)
    write_json(folder/"20261110_pe_code_identity_tests.json",report)
    print("PE implementation identities:",len(checks),"checks passed")


if __name__=="__main__":main()
