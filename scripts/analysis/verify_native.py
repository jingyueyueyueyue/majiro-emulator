"""Launch our development project in the real engine, capture and compare its bootstrap."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys

from lua_bridge import create_lua, table_value
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, read_json, sha256, write_json


def _run(scenario,evidence,project):
    source_project = ROOT / "projects/windows"
    # The player and probes never share frame names, settings or probe outputs.
    output_names = ("native_result.lua", "native_error.txt", "native_screen.png")
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    executable = project / "Ruri_Artemis.exe"
    with (evidence / "20261108_engine_output.txt").open("wb") as log:
        process = subprocess.Popen([str(executable), "/probe", "1","/scenario",scenario], cwd=project,
                                   startupinfo=startup, stdout=log, stderr=subprocess.STDOUT)
        try:
            code = process.wait(timeout=45)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            raise RuntimeError("Artemis initialization probe timed out")
    found = {}
    for base in (project, project / "savedata-dev"):
        for name in output_names:
            path = base / name
            if path.is_file():
                destination = evidence / ("20261108_" + name)
                path.replace(destination)
                found[name] = destination
    if "native_error.txt" in found:
        raise RuntimeError(found["native_error.txt"].read_text(encoding="utf-8", errors="replace"))
    if code != 0 or "native_result.lua" not in found:
        raise RuntimeError(f"Artemis failed: exit={code}, evidence={evidence}")
    native = table_value(create_lua().execute(found["native_result.lua"].read_text(encoding="utf-8")))
    model = read_json(ROOT / "reports/bootstrap_probe.json") if scenario=="title" else read_json(
        ROOT/"reports"/("title_path_%d.json"%(1 if scenario=="start"else 3)))
    fields=("status","steps","library_count") if scenario=="title" else ("steps",)
    for field in fields:
        if native[field] != model[field]:
            raise AssertionError(f"model/native mismatch: {field}: {native[field]} != {model[field]}")
    if model.get("problem"):
        for field in ("script", "offset", "reason"):
            if native["problem"][field] != model["problem"][field]:
                raise AssertionError(f"model/native source mismatch: {field}")
    elif native.get("problem"):
        raise AssertionError("unexpected native execution error")
    picture = found.get("native_screen.png")
    if picture is None:
        raise RuntimeError("native screenshot missing")
    from PIL import Image, ImageStat, ImageChops
    with Image.open(picture) as image:
        if image.size != (1280, 720) or max(ImageStat.Stat(image.convert("RGB")).stddev) < 10:
            raise AssertionError("native screenshot has incorrect dimensions or is blank")
        if native["status"]=="waiting_for_input" or native.get("reading_wait"):
            if scenario=="title" and len(native["ui_areas"]) != len(model["ui_areas"]):
                raise AssertionError("native title hit-area mismatch")
            state_dir=project/"savedata-dev"
            live=sorted((p for p in state_dir.glob("ruri_live_*.*")if p.suffix in(".png",".bmp")),
                        key=lambda p:int(p.stem.rsplit("_",1)[-1]))
            if not live:
                raise AssertionError("rendered title frame missing")
            with Image.open(live[-1]) as rendered:
                difference=ImageChops.difference(image.convert("RGB"),rendered.convert("RGB"))
                region=(0,0,1280,500) if native.get("reading_wait")else (0,0,1280,720)
                if max(ImageStat.Stat(difference.crop(region)).mean)>1:
                    raise AssertionError("native presentation differs from current generated frame")
            if native.get("reading_wait"):
                if native["console"]["text"]!=model["console"]["text"]:
                    raise AssertionError("native first page differs from original-script model")
                console=native["console"]
                assert console.get("raster_only"),"live text bypassed source composition"
                reference=read_json(ROOT/"reports/display_fidelity.json")["first_screenshot"]
                with Image.open(reference)as expected:
                    delta=ImageChops.difference(image.convert("RGB"),expected.convert("RGB"))
                    # Text is now drawn into the original page and revealed
                    # by CONSOLE_WROTE. The final host no longer adds glyphs.
                    area=console["configuration"]
                    rectangle=(area[1],area[2],area[1]+area[3],min(720,area[2]+area[4]))
                    if max(ImageStat.Stat(delta.crop(rectangle)).mean)>2:
                        raise AssertionError("native source-composited dialogue differs from model")
        else:
            text=image.convert("RGB").crop((40,300,1180,345))
            bright=sum(1 for r,g,b in text.get_flattened_data() if min(r,g,b)>200)
            if bright<100:raise AssertionError("native diagnostic text did not render")
    build = read_json(ROOT / "reports/windows_build.json")
    if native["build_id"] != build["build_id"]:
        raise AssertionError("native build identity mismatch")
    report = {"schema": 2, "status": "passed", "scope": "native initialization, source position, image output",
              "build_id": build["build_id"], "engine_sha256": sha256(executable),
              "native": native, "screenshot": str(picture), "evidence": str(evidence),
              "full_gameplay": "not_implemented", "original_engine_pixel_comparison": "not_run",
              "release_allowed": False}
    if scenario=="title":
        from test_title_render import assert_title
        report["title_background"]=assert_title(picture)
    elif scenario=="config":
        from test_display_fidelity import assert_config
        # CPU composition is exact; the engine's final texture pass rounds
        # a small number of channels down by 1..2 units.
        report["original_config_pixels"]=assert_config(picture,tolerance=2)
    report["scenario"]=scenario
    write_json(ROOT / "reports"/("native_verify.json"if scenario=="title"else"native_verify_"+scenario+".json"), report)
    print(json.dumps({"status": report["status"], "steps": native["steps"], "screenshot": str(picture)},
                     ensure_ascii=True))
    return report


def run(scenario="title"):
    from probe_project import isolated_project,clean_project
    evidence=archive_dir("artemis_native")
    project=isolated_project(ROOT/"projects/windows",evidence/"probe")
    try:
        return _run(scenario,evidence,project)
    finally:
        clean_project(project)


if __name__ == "__main__":
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument("--scenario",choices=("title","start","config"),default="title")
    run(parser.parse_args().scenario)
