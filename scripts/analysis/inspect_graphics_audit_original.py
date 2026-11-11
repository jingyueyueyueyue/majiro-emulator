"""Bounded original-code listings for unresolved graphics fixture ABIs."""
from pathlib import Path
import json
import sys

from native_function import NativeFunctions
from original_calls import CallOracle
from original_x86 import Original

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT


def main():
    folder = ROOT / "archive/2026-11/20261111_emulator_audit_graphics"
    folder.mkdir(parents=True, exist_ok=True)
    samples = json.loads((ROOT / "reports/opcode_dispatch.json").read_text(encoding="utf8"))["samples"]
    dispatch = json.loads((ROOT / "reports/syscall_dispatch.json").read_text(encoding="utf8"))["samples"]
    lines = []
    for name, apis in [
        ("happy_princess", ("$grp_mulcopy",)),
        ("kizoku", ("$fontout_push", "$fontout_pop", "$fontout_locate")),
        ("magical_kanon2", ("$curpos_x", "$curpos_y", "b84c9953")),
        ("RURINOKASANE", ("$grp_copy", "$grp_extcopy", "$histbuff_clear")),
    ]:
        sample = next(row for row in samples if row["sample"] == name)
        oracle = CallOracle(sample)
        functions = NativeFunctions(oracle)
        entries = next(row["entries"] for row in dispatch if row["sample"] == name)
        for api in apis:
            entry = next(row for row in entries if row["name"] == api or row["hash"] == api)
            root = functions.syscall_helper(entry)
            lines.append(f"\n{name} {api} {root:08x}")
            targets = [root]
            targets += [row.operands[0].imm for row in functions.calls(root)]
            for target in dict.fromkeys(targets):
                lines.append(f"FUNCTION {target:08x}")
                for row in functions.body(target):
                    lines.append(f"{row.address:08x} {row.mnemonic} {row.op_str}")
    path = folder / "20261111_original_abi.txt"
    path.write_text("\n".join(lines), encoding="utf8")
    print(path)


if __name__ == "__main__":
    main()
