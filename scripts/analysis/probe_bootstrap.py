"""Advance the original startup until a real missing dependency; never skip it."""
from __future__ import annotations
import json
from pathlib import Path
import sys

from lua_bridge import create_lua, setup_runtime, table_value
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, read_json, sha256, write_json


def run(verify_only=False):
    lua = create_lua()
    _, vm, system, clock = setup_runtime(lua, allow_prepare=not verify_only)
    error = None
    try:
        vm["begin"](vm, "start.mjo")
        for _ in range(10000):
            status = vm["run"](vm, 10000)
            if vm["steps"]>4_000_000:
                raise RuntimeError("bootstrap probe exceeded 4,000,000 instructions")
            if status=="waiting" and system["state"]["ui_waiting"]:
                break
            deadline=vm["next_deadline"](vm) if status=="waiting" else None
            if deadline is not None:
                clock["ms"]=max(clock["ms"]+10,deadline)
                system["poll"](vm,lua.table())
                continue
            if status in ("blocked", "finished", "waiting"):
                break
        else:
            raise RuntimeError("bootstrap probe exceeded 10,000 simulated frames")
    except Exception as exc:
        if vm["status"] != "blocked":
            raise
        error = str(exc)
    report = {
        "schema": 1, "source_identity": read_json(ROOT / "resources/source_manifest.json")["source_identity"],
        "runtime_hashes": {p.name: sha256(p) for p in sorted((ROOT / "runtime").glob("*.lua"))},
        "host": "Lua 5.1 model (lupa), not native Artemis",
        "status": "waiting_for_input" if system["state"]["ui_waiting"] and not error else vm["status"],
        "steps": vm["steps"], "error": error,
        "problem": table_value(vm["problem"]) if vm["problem"] else None,
        "last_syscall": table_value(vm["last_syscall"]) if vm["last_syscall"] else None,
        "loaded_scripts": sorted(vm["scripts"].keys()),
        "library_count": len(list(vm["libraries"].keys())),
        "event_count": sum(len(list(v.values())) for v in system["state"]["events"].values()),
        "syscalls": table_value(system["state"]["calls"]),
        "simulated_ms": clock["ms"],
        "ui_areas": table_value(system["state"]["ui_areas"]) if system["state"]["ui_areas"] else None,
        "trace": table_value(vm["trace"]),
        "release_allowed": False,
    }
    write_json(ROOT / "reports/bootstrap_probe.json", report)
    print(json.dumps({k: report[k] for k in ("status", "steps", "library_count", "problem")}, ensure_ascii=True))
    return report


if __name__ == "__main__":
    run("--verify-only" in sys.argv)
