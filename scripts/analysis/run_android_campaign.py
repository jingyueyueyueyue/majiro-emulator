"""Drive the real Android VM through public click/key input and collect executed reading evidence."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, read_json, timestamp, write_json


class Device:
    def __init__(self, serial, port=28765):
        self.serial, self.port = serial, port
        self.adb = Path(read_json(ROOT / "reports/android_toolchain.json")["sdk"]) / "platform-tools/adb.exe"
        subprocess.run([str(self.adb), "-s", serial, "forward", f"tcp:{port}", "localabstract:majiro-debug"],
                       check=True, capture_output=True, timeout=20)

    def command(self, command):
        with socket.create_connection(("127.0.0.1", self.port), timeout=15) as stream:
            stream.sendall((command + "\n").encode("utf-8"))
            output = bytearray()
            while b"\n" not in output:
                block = stream.recv(65536)
                if not block:
                    break
                output.extend(block)
                if len(output) > 32 * 1024 * 1024:
                    raise ValueError("device diagnostic response exceeds bound")
        result = json.loads(output)
        if "error" in result:
            raise RuntimeError(result["error"])
        return result

    def snapshot(self):
        return self.command("STATUS")

    def click(self, area):
        x = area["x"] + area["width"] / 2
        y = area["y"] + area["height"] / 2
        self.command(f"MOVE {x:g} {y:g}")
        time.sleep(0.08)
        return self.command(f"CLICK {x:g} {y:g} 1")

    def key(self, code, down):
        return self.command(f"KEY {code} {int(down)}")


def run(serial, name, seconds, start, fast, page_limit):
    device = Device(serial)
    folder = archive_dir("android_campaign_" + name)
    began = time.monotonic()
    report = {
        "schema": 1, "timestamp": timestamp(), "serial": serial, "name": name,
        "apk_sha256": read_json(ROOT / "reports/android_probe_build.json")["sha256"],
        "selections": [], "evidence": str(folder), "complete": False,
        "scope": "actual Android SDL/Lua execution via source key and pointer input",
        "status": "running", "read_positions": {}, "runtime_problem": None,
    }
    terminal = None
    previous_choice = None
    chapter_latest = {}
    last_progress = 0
    latest = None
    active = False
    try:
        latest = device.snapshot()
        if start:
            areas = latest["areas"]
            root = latest["frames"][0]["script"] if latest["frames"] else ""
            if root != "start.mjo" or not isinstance(areas, list):
                raise ValueError("start requested but actual title controls are not active")
            area = next(a for a in areas if a["id"] == 1)
            device.click(area); active = True
            time.sleep(0.4)
        else:
            active = True
        if fast:
            device.key(17, True)
        while time.monotonic() - began < seconds:
            latest = device.snapshot()
            if latest.get("problem"):
                report["runtime_problem"] = latest["problem"]
                raise RuntimeError("device VM reported a source failure")
            positions = latest.get("read_positions") or {}
            if isinstance(positions, dict):
                report["read_positions"].update({key: True for key, value in positions.items() if value})
            elapsed = time.monotonic() - began
            if elapsed >= last_progress + 15:
                print(name, "Android pages", latest["reading_generation"], "unique",
                      len(report["read_positions"]), "steps", latest["steps"],
                      "root", latest["frames"][0]["script"] if latest["frames"] else "-", flush=True)
                last_progress = elapsed
                report["latest"] = latest
                write_json(ROOT / ("reports/android_campaign_" + name + ".json"), report)
            if page_limit and len(report["read_positions"]) >= page_limit:
                terminal = "page_budget_reached"; break
            frames = latest.get("frames") or []
            root = frames[0]["script"] if frames else ""
            areas = latest.get("areas")
            if root == "start.mjo" and latest.get("ui_waiting") and len(report["read_positions"]) > 0:
                terminal = "returned_to_title"; break
            if latest.get("ui_waiting") and not latest.get("reading_wait") and isinstance(areas, list) and areas:
                signature = (root, tuple((a["id"], a["x"], a["y"], a["width"], a["height"]) for a in areas))
                if signature != previous_choice:
                    device.key(17, False)
                    if root.startswith("sub_title"):
                        candidates = [(a["id"], a) for a in areas if 0 < a["id"] < 99]
                        newest, selected = max(candidates, key=lambda pair: pair[0]) if candidates else (0, None)
                        if newest <= chapter_latest.get(root, 0):
                            selected = next((a for a in areas if a["id"] == 99), selected)
                        else:
                            chapter_latest[root] = newest
                        if selected is None:
                            raise RuntimeError("source chapter menu has no usable controls")
                    else:
                        selected = areas[0]
                    report["selections"].append({
                        "root": root, "area": selected, "areas": areas,
                        "reading_generation": latest["reading_generation"],
                    })
                    device.click(selected); time.sleep(0.5)
                    if fast:
                        device.key(17, True)
                    previous_choice = signature
                elif time.monotonic() - began > seconds - 2:
                    terminal = "choice_not_resolved"
            else:
                previous_choice = None
                if latest.get("reading_wait") and not fast:
                    device.command("CLICK 1000 650 1")
            time.sleep(0.25 if fast else 0.4)
        report.update(status="passed", terminal=terminal or "time_budget_reached")
    except Exception as error:
        report.update(status="failed", error=str(error), terminal="source_or_transport_failure")
        log = subprocess.run([str(device.adb), "-s", serial, "logcat", "-d", "-s",
                              "MajiroPlayer:I", "AndroidRuntime:E", "*:S"],
                             capture_output=True, timeout=25)
        (folder / "device_log.txt").write_bytes(log.stdout + log.stderr)
    finally:
        if active:
            try:
                device.key(17, False)
            except Exception:
                pass
        report.update(seconds=round(time.monotonic() - began, 2), latest=latest,
                      unique_read_positions=len(report["read_positions"]))
        write_json(ROOT / ("reports/android_campaign_" + name + ".json"), report)
        write_json(folder / "campaign.json", report)
    print(name, report["status"], report["terminal"], report["unique_read_positions"], "read positions")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--seconds", type=int, default=600)
    parser.add_argument("--start", action="store_true")
    parser.add_argument("--fast", action="store_true")
    parser.add_argument("--pages", type=int, default=0)
    args = parser.parse_args()
    if not args.name or not all(c.isascii() and (c.isalnum() or c in "_-") for c in args.name):
        parser.error("name must contain only ASCII letters, digits, underscores and hyphens")
    raise SystemExit(run(args.serial, args.name, args.seconds, args.start, args.fast, args.pages))
