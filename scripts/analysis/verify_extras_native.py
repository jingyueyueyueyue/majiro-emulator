"""Cold-start earned unlocks and open actual extras screens in Artemis."""
import os
import subprocess
from PIL import Image, ImageStat
from lua_bridge import create_lua, table_value
from probe_project import isolated_project, clean_project
from port_common import ROOT, archive_dir, read_json, write_json


def run():
    earned = read_json(ROOT / "reports/ending_extras_tests.json")
    assert earned["cold_restart_unlock_retained"] and earned["all_four_menus_opened"]
    profile = __import__("pathlib").Path(earned["evidence"]) / "saves/index.mjs"
    evidence = archive_dir("native_extras")
    cases = []
    for scene in ("extras_music", "extras_cg", "extras_replay"):
        project = isolated_project(ROOT / "projects/windows", evidence / scene)
        saves = project / "savedata-dev/port-data"
        saves.mkdir()
        # index.mjs is a descriptor, whose content-addressed chunks are
        # required too. Hardlinks retain one physical copy of every object.
        for file in profile.parent.iterdir():
            if file.is_file() and not file.name.endswith(".writing"):
                os.link(file, saves / file.name)
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        try:
            with (evidence / ("20261109_" + scene + ".txt")).open("wb") as log:
                process = subprocess.Popen([str(project / "Ruri_Artemis.exe"),
                    "/probe", "1", "/scenario", scene], cwd=project,
                    startupinfo=startup, stdout=log, stderr=subprocess.STDOUT)
                try:
                    code = process.wait(timeout=55)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=5)
                    raise RuntimeError("native extras timed out: " + scene)
            outputs = {}
            for folder in (project, project / "savedata-dev"):
                for name in ("native_result.lua", "native_screen.png", "native_error.txt"):
                    file = folder / name
                    if file.is_file():
                        dest = evidence / ("20261109_" + scene + "_" + name)
                        file.replace(dest); outputs[name] = dest
            assert code == 0 and "native_result.lua" in outputs and "native_error.txt" not in outputs, outputs
            result = table_value(create_lua().execute(outputs["native_result.lua"].read_text(encoding="utf-8")))
            assert not result.get("problem"), result.get("problem")
            assert result["build_id"] == read_json(ROOT / "reports/windows_build.json")["build_id"]
            assert len(result["ui_areas"]) >= (25 if scene == "extras_music" else 8 if scene == "extras_cg" else 5)
            with Image.open(outputs["native_screen.png"]) as image:
                assert image.size == (1280, 720) and max(ImageStat.Stat(image.convert("RGB")).stddev) > 10
            if scene == "extras_music":
                assert result["media"]["music"]["name"] == "pl740", result["media"]
            cases.append({"scene": scene, "status": "passed", "steps": result["steps"],
                          "screenshot": str(outputs["native_screen.png"])})
            print("native extras:", scene, "passed", flush=True)
        finally:
            clean_project(project)
    report = {"status": "passed", "cases": cases, "evidence": str(evidence),
              "build_id": read_json(ROOT / "reports/windows_build.json")["build_id"],
              "scope": "three real Artemis cold processes using naturally earned profile; public title/extras clicks"}
    write_json(ROOT / "reports/native_extras_tests.json", report)
    return report


if __name__ == "__main__":
    run()
