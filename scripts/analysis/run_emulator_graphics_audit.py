"""Run owned graphics regressions with evidence confined to the audit archive."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "archive/2026-11/20261111_emulator_audit_graphics"
PHASE = os.environ.get("MAJIRO_GRAPHICS_AUDIT_PHASE", "")
if PHASE:
    if not PHASE.replace("_", "").replace("-", "").isalnum():
        raise ValueError("invalid graphics audit phase")
    EVIDENCE /= PHASE


def child(name):
    sys.path.insert(0, str(ROOT / "scripts/convert"))
    import port_common
    counter = 0

    def archive(label):
        nonlocal counter
        counter += 1
        path = EVIDENCE / name / f"{counter:02d}_{label}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    port_common.archive_dir = archive
    sys.argv = [str(ROOT / "scripts/analysis" / (name + ".py"))]
    module = importlib.import_module(name)
    entry = getattr(module, "run", None) or getattr(module, "main")
    result = entry()
    if isinstance(result, dict) and result.get("status") in ("failed", "incomplete", "source_changed", "inconclusive"):
        raise RuntimeError(f"{name}: {result['status']}")
    if isinstance(result, int) and result:
        raise SystemExit(result)


def run(names):
    EVIDENCE.mkdir(parents=True, exist_ok=True)

    def check(name):
        started = time.monotonic()
        log = EVIDENCE / f"{name}.log"
        with log.open("wb") as stream:
            result = subprocess.run(
                [sys.executable, "-X", "utf8", str(Path(__file__).resolve()), "--child", name],
                cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                env={**os.environ, "PYTHONUTF8": "1"})
        row = {"test": name, "exit_code": result.returncode,
               "seconds": round(time.monotonic() - started, 2), "log": str(log)}
        print(json.dumps(row), flush=True)
        return row

    with ThreadPoolExecutor(max_workers=3) as executor:
        rows = list(executor.map(check, names))
    (EVIDENCE / "suite_results.json").write_text(
        json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    return int(any(row["exit_code"] for row in rows))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child")
    parser.add_argument("tests", nargs="*")
    arguments = parser.parse_args()
    if arguments.child:
        child(arguments.child)
    else:
        raise SystemExit(run(arguments.tests))
