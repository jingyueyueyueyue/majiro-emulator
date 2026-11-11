"""Main changed-entry checks for six supplied engines; no Android device use."""
from __future__ import annotations
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, timestamp, write_json, sha256


def main():
    stages = (
        "scripts/analysis/test_new_graphics_abi.py",
        "scripts/analysis/test_new_sprite_sequences.py",
        "scripts/analysis/test_new_ui_abi.py",
        "scripts/analysis/test_new_control_abi.py",
        "scripts/analysis/test_new_archive_abi.py",
        "scripts/analysis/test_cross_thread_events.py",
        "scripts/analysis/test_sprite_variable_geometry.py",
        "scripts/analysis/test_sprite_argument_forms.py",
        "scripts/analysis/inspect_save_composition.py",
        "scripts/analysis/summarize_new_abi_evidence.py",
    )
    folder = archive_dir("new_sample_checks")
    rows = []
    for stage in stages:
        output = folder / ("20261110_" + Path(stage).stem + ".txt")
        with output.open("wb") as log:
            result = subprocess.run([sys.executable, "-B", "-X", "utf8", str(ROOT / stage)],
                                    cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        rows.append({"stage": stage, "source_sha256": sha256(ROOT / stage),
                     "status": "passed" if result.returncode == 0 else "failed", "log": str(output)})
        print(stage, rows[-1]["status"], flush=True)
        if result.returncode:
            break
    passed = len(rows) == len(stages) and all(row["status"] == "passed" for row in rows)
    write_json(ROOT / "reports/new_sample_checks.json", {
        "schema": 1, "timestamp": timestamp(), "status": "passed" if passed else "failed",
        "stages": rows, "device_testing": False, "evidence": str(folder),
        "scope": "changed-entry native ABI/state comparisons and independent retained-scene menu composition",
    })
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
