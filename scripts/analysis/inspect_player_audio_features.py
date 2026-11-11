"""Observe original audio resource preference across every local engine sample."""
from pathlib import Path
import sys

from original_extsound import ExtSoundOracle
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,read_json,write_json,timestamp


def main():
    samples=read_json(ROOT/"reports/opcode_dispatch.json")["samples"]
    dispatch={row["sample"]:row["entries"]for row in
              read_json(ROOT/"reports/syscall_dispatch.json")["samples"]}
    rows=[]
    for sample in samples:
        oracle=ExtSoundOracle(sample,dispatch[sample["sample"]])
        profile=oracle.audio_profile()
        body=oracle.functions.body(oracle.resolver_va)
        rows.append({"sample":sample["sample"],"exe_sha256":sample["exe_sha256"],
            "audio_controls":profile,"sound_is_exist_hash":oracle.entries["$sound_is_exist"]["hash"],
            "resolver":oracle.resolver_va,"lookups":list(oracle.lookups),"plays":list(oracle.plays),
            "disassembly":[{"address":item.address,"mnemonic":item.mnemonic,"operands":item.op_str}
                           for item in body]})
        print(sample["sample"],profile,flush=True)
    report={"timestamp":timestamp(),"status":"passed","samples":rows,
        "scope":"original resolver executes with WAV and OGG both present; VFS/decoder boundaries",
        "production_identity_gate":False,"device_accessed":False}
    folder=ROOT.parent/"archive/2026-11/20261111_emulator_audit_host/phase2"
    folder.mkdir(parents=True,exist_ok=True)
    write_json(folder/"20261111_audio_feature_evidence.json",report)
    write_json(ROOT/"reports/player_audio_feature_evidence.json",report)
    return report


if __name__=="__main__":
    main()
