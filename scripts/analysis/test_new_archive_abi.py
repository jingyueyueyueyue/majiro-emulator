"""Compare real original archive wrapper path splitting with Android requests."""
from __future__ import annotations
import json
from pathlib import Path
import struct
import sys
from unicorn import x86_const as reg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, timestamp, write_json, sha256
from test_new_graphics_abi import FunctionFixture
from audit_player_semantics import registered_providers


def missing_register(sample,wrapper,accessor,result):
    """Run the real registry allocator, loader and failure path, not a core stub."""
    if sample["sample"]!="idol_revolution":
        raise ValueError("missing-archive core layout not yet verified for this original build")
    cases=[]
    for existing in(False,True):
        for entry_kind in("wrapper","core"):
            f=FunctionFixture(sample)
            f.uc.mem_write(0x10020000,bytes(12)+b"slowdata1\0")
            f.put(0x57b138,1);f.put(0x57b13c,0)
            head=0x10050000 if existing else 0
            f.put(0x57b028,head);f.put(0x57b02c,head)
            if existing:
                f.uc.mem_write(head,bytes(0x240))
                f.uc.mem_write(head+0x104,b"existing\0")
                f.put(head+0x220,0x76543210);f.put(head+0x234,1)
            before=bytes(f.uc.mem_read(head,0x240))if existing else b""
            events=[];allocations=[];frees=[]
            def text(pointer):
                return bytes(f.uc.mem_read(pointer,512)).split(b"\0")[0].decode("ascii")
            def allocate(sp):
                size=f.read(sp+4);assert size==0x240
                allocations.append(0x10040000);return allocations[-1]
            def free(sp):
                frees.append(f.read(sp+4));return 0
            def fopen(sp):
                events.append({"boundary":"fopen","path":text(f.read(sp+4)),
                               "mode":text(f.read(sp+8)),"result":"NULL"})
                return 0
            def stat(sp):
                events.append({"boundary":"stat","path":text(f.read(sp+4)),"result":"ENOENT"})
                return 0xffffffff
            def compare(sp):
                left,right=text(f.read(sp+4)).lower(),text(f.read(sp+8)).lower()
                return 0 if left==right else(-1 if left<right else 1)
            f.hook(0x4454c0,allocate);f.hook(0x455acb,free)
            f.hook(0x455a89,fopen);f.hook(0x47bb04,stat);f.hook(0x455d36,compare)
            for index,iat in enumerate((0x4801c4,0x4801c0)):
                address=0x30000100+index*16
                f.put(iat,address);f.hook(address,lambda sp:0,cleanup=4)
            f.hook(accessor,lambda sp:f.top)
            f.hook(result,lambda sp:setattr(f,"result",f.read(sp+4))or 0)
            if entry_kind=="wrapper":
                f.call(wrapper,[0x10020000]);native_result=f.result
                assert native_result==0
            else:
                # Core ABI receives the stripped name and archive flags.
                stack=0x2000f000
                f.put(stack,0x30000000);f.put(stack+4,0x1002000c);f.put(stack+8,0)
                f.uc.reg_write(reg.UC_X86_REG_ESP,stack)
                f.uc.reg_write(reg.UC_X86_REG_EFLAGS,2)
                f.uc.emu_start(0x445690,0x30000000,count=100000)
                assert f.uc.reg_read(reg.UC_X86_REG_EIP)==0x30000000
                native_result=f.uc.reg_read(reg.UC_X86_REG_EAX)
                assert native_result==0xffffffff
            assert events==[{"boundary":"fopen","path":"slowdata1.arc","mode":"rb","result":"NULL"},
                            {"boundary":"stat","path":"slowdata1","result":"ENOENT"}]
            assert allocations==frees==[0x10040000]
            assert f.read(0x57b028)==head and f.read(0x57b02c)==head
            assert f.read(0x57b13c)==0
            if existing:assert bytes(f.uc.mem_read(head,0x240))==before
            cases.append({"entry":entry_kind,"preexisting_unrelated_archive":existing,
                          "return_u32":native_result,"status":"passed","host_events":events,
                          "registry_head_tail_unchanged":True,"other_archive_unchanged":True,
                          "temporary_node_allocated_and_freed":True,"load_nesting_restored":True})
    return cases


def main():
    scan = json.loads((ROOT / "reports/new_sample_scan.json").read_text(encoding="utf8"))
    cases = []
    for label, accessor, result, register, release, register_core, release_core in (
        ("idol_revolution", 0x41eab0, 0x41ead0, 0x403970, 0x4039d0, 0x445690, 0x445530),
        ("reconquista", 0x41c740, 0x41c760, 0x4036d0, 0x403730, 0x43b790, 0x43b630)):
        sample = next(s for s in scan["opcode_samples"] if s["sample"] == label)
        for name, root, core, hash_ in (
            ("register", register, register_core, "4fac462c"),
            ("release", release, release_core, "f4bb403d")):
            count = 0
            for raw in (b"custom.arc", b"relative\\custom.arc", b"custom", b"part.one.arc", b"D:custom.abc"):
                f = FunctionFixture(sample)
                f.uc.mem_write(0x10020000, bytes(12) + raw + b"\0")
                f.hook(accessor, lambda sp: f.top)
                f.hook(result, lambda sp: setattr(f, "result", f.read(sp + 4)) or 0)
                native = []
                def capture(sp):
                    native.append(bytes(f.uc.mem_read(f.read(sp + 4), 260)).split(b"\0")[0])
                    return 0
                f.hook(core, capture)
                f.call(root, [0x10020000])
                assert f.result == 0 and len(native) == 1
                actions = []
                lua, _, _, system = registered_providers(overrides={
                    "archive": lambda action, value: actions.append((action, value)) or 0})
                actual = system["providers"]["$regist_arcfile" if name == "register" else "$release_arcfile"](
                    lua.table(), lua.table_from([raw.decode("ascii")]))
                assert actual == 0 and actions == [(name, native[0].decode("ascii") + ".arc")]
                count += 1
            cases.append({"sample": label, "hash": hash_, "argc": 1, "opcode": "syscallp",
                          "status": "passed", "cases": count,
                          "scope": "original CRT directory/drive/extension removal and wrapper zero return",
                          "limits": "archive decoder and registration precedence are separately validated"})
    idol=next(s for s in scan["opcode_samples"]if s["sample"]=="idol_revolution")
    missing=missing_register(idol,0x403970,0x41eab0,0x41ead0)
    report = {"schema": 1, "timestamp": timestamp(), "status": "passed", "cases": cases,
              "missing_register_cases":missing,
              "missing_register_rule":{"script_wrapper_result":0,"native_registry_result":-1,
                  "side_effect":"no resource inserted; temporary node freed; prior archive registry unchanged",
                  "condition":"neither stem.arc nor fallback stem directory exists"},
              "missing_original_exe_sha256":idol["exe_sha256"],
              "scope": "20 original wrapper paths plus 4 real registry/loader missing-file cases; no archive payloads extracted",
              "isolated_boundaries": ["VM argument/result access for wrapper",
                                      "CRT fopen NULL and stat ENOENT for missing core",
                                      "allocator/free and single-thread critical section"],
              "runtime_sha256": sha256(ROOT / "player/runtime/majiro_syscalls.lua")}
    folder = archive_dir("new_archive_abi")
    write_json(folder / "20261110_new_archive_abi.json", report)
    write_json(ROOT / "reports/new_archive_abi_tests.json", report)
    print("new archive ABI: 20 wrapper paths and 4 real registry missing-file cases passed")


if __name__ == "__main__":
    main()
