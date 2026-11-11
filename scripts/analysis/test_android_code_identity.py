"""Compare Android/JVM and Python PE identities on verified builds and edge cases."""
import hashlib
import ctypes
import json
from pathlib import Path
import struct
import subprocess
import sys
from pe_code_identity import code_identity
from test_pe_code_identity import fixture
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,write_json


def main():
    folder=archive_dir("android_code_identity_tests");classes=folder/"classes";classes.mkdir()
    tools=json.loads((ROOT/"reports/android_toolchain.json").read_text(encoding="utf8"))
    java=Path(tools["java"])
    source=ROOT/"player/android/src/org/majiro/player/EngineCodeIdentity.java"
    harness=ROOT/"scripts/analysis/EngineCodeIdentityChecks.java"
    process=subprocess.run([str(java/"bin/javac.exe"),"-encoding","UTF-8","-source","8","-target","8",
                            "-d",str(classes),str(source),str(harness)],capture_output=True)
    (folder/"20261110_javac.txt").write_bytes(process.stdout+process.stderr)
    assert process.returncode==0,str(folder)
    registry=json.loads((ROOT/"reports/engine_code_profiles.json").read_text(encoding="utf8"))
    paths=[Path(row["source"])for row in registry["sources"]]
    names=[row["sample"]for row in registry["sources"]]
    variants={"base":fixture()}
    raw=fixture();raw[0x88:0x8c]=b"date";raw[0x400:0x480]=b"R"*0x80;raw+=b"overlay"
    variants["resource_timestamp_overlay"]=raw
    raw=fixture();struct.pack_into("<I",raw,0x98+28,0x500000);struct.pack_into("<I",raw,0x201,0x501234)
    variants["relocated"]=raw
    raw=fixture();raw[0x207]^=1;variants["changed_code"]=raw
    raw=fixture()
    for i in range(3):struct.pack_into("<I",raw,0x178+i*40+36,0xc0000040)
    variants["missing_execute_flags"]=raw
    raw=fixture();struct.pack_into("<I",raw,0x604,0xffffffff);variants["malformed_reloc"]=raw
    variants["truncated"]=b"MZ"
    for name,data in variants.items():
        path=folder/("20261110_"+name+".exe");path.write_bytes(data);paths.append(path);names.append(name)
    expected=[]
    for path in paths:
        try:expected.append(code_identity(path.read_bytes())["identity"])
        except ValueError:expected.append("REJECT")
    listing=folder/"20261110_inputs.txt";listing.write_text("\n".join(map(str,paths))+"\n",encoding="utf8")
    def short(path):
        buffer=ctypes.create_unicode_buffer(32768)
        if not ctypes.windll.kernel32.GetShortPathNameW(str(path),buffer,len(buffer)):
            raise OSError("short path unavailable")
        return buffer.value
    process=subprocess.run([str(java/"bin/java.exe"),"-Dfile.encoding=UTF-8","-cp",short(classes),
                            "org.majiro.player.EngineCodeIdentityChecks","--utf8-list",short(listing)],
                            capture_output=True)
    (folder/"20261110_jvm.txt").write_bytes(process.stdout+process.stderr)
    assert process.returncode==0,str(folder)
    actual=process.stdout.decode("utf8").splitlines()
    assert len(actual)==len(expected)
    rows=[]
    for name,left,right in zip(names,expected,actual):
        assert left==right,(name,left,right)
        rows.append({"sample":name,"identity":right,"status":"passed"})
    report={"schema":1,"timestamp":timestamp(),"status":"passed","cases":rows,
            "verified_builds":len(registry["sources"]),"device_accessed":False,"gameplay_started":False,
            "evidence":str(folder.relative_to(ROOT)),
            "source_sha256":{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in(source,harness,ROOT/"scripts/analysis/pe_code_identity.py")}}
    write_json(ROOT/"reports/android_code_identity_tests.json",report)
    write_json(folder/"20261110_android_code_identity_tests.json",report)
    print("Android/JVM code identity:",len(rows),"Python comparisons passed")


if __name__=="__main__":main()
