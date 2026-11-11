"""Read naturally, restart Artemis, hold/release CTRL and measure actual page progress."""
import os
import subprocess
from model_session import Session
from lua_bridge import create_lua, table_value
from probe_project import isolated_project, clean_project
from port_common import ROOT, archive_dir, read_json, write_json


def run():
    evidence = archive_dir("fast_native")
    saves = evidence / "earned_reads"
    s = Session(False, save_root=saves)
    assert s.advance() == "ui" and s.click(1) == "text"
    for _ in range(65):
        assert s.page() == "text", s.summary()
    s.system["state"]["persist_dirty"] = True
    s.system["saves"]["flush"](s.vm)
    project = isolated_project(ROOT / "projects/windows", evidence / "probe")
    native_saves = project / "savedata-dev/port-data"
    native_saves.mkdir()
    for file in saves.iterdir():
        if file.is_file():
            os.link(file, native_saves / file.name)
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    try:
        with (evidence / "20261109_engine.txt").open("wb") as log:
            process = subprocess.Popen([str(project / "Ruri_Artemis.exe"),
                "/profile", "3500", "/profile_scene", "fast"], cwd=project,
                startupinfo=startup, stdout=log, stderr=subprocess.STDOUT)
            try:
                code = process.wait(timeout=45)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)
                raise RuntimeError("native fast response timed out")
        path = next((p for p in (project / "native_profile.lua",
            project / "savedata-dev/native_profile.lua") if p.is_file()), None)
        assert code == 0 and path, str(evidence)
        native = table_value(create_lua().execute(path.read_text(encoding="utf-8")))
        assert not native.get("problem"), native.get("problem")
        samples = native["samples"]
        held = [sample for sample in samples if sample.get("control_down")]
        released = [sample for sample in samples if sample["t"] >= 2300]
        assert held and released
        start = held[0]["reading_generation"]
        first = next((sample["t"] for sample in held if sample["reading_generation"] > start), None)
        assert first is not None and first - held[0]["t"] < 300, (first, held[0])
        advanced = held[-1]["reading_generation"] - start
        assert advanced >= 1, ("fast did not advance", advanced)
        dwells=[]
        for before,after in zip(samples,samples[1:]):
            if after["reading_generation"]>before["reading_generation"]and before.get("page_visible_since")is not None:
                dwell=after["clock"]-before["page_visible_since"]
                assert dwell>=0,("unpresented page skipped",dwell)
                dwells.append(dwell)
        assert dwells,"missing actually presented fast page"
        assert released[-1]["reading_generation"] - released[0]["reading_generation"] <= 1
        assert native["build_id"] == read_json(ROOT / "reports/windows_build.json")["build_id"]
        report = {"status": "passed", "build_id": native["build_id"],
                  "first_page_response_ms": first - held[0]["t"],
                  "pages_while_held": advanced, "release_stops_fast": True,
                  "minimum_visible_page_ms":min(dwells),"added_dwell_ms":0,
                  "naturally_read_pages": 65, "evidence": str(evidence),
                  "scope": "actual Artemis holding/releasing CTRL after normal reading; no manually unlocked flags"}
        write_json(ROOT / "reports/native_fast_response.json", report)
        print("native fast response:", report["first_page_response_ms"], "ms;",
              advanced, "pages; release passed")
        return report
    finally:
        clean_project(project)


if __name__ == "__main__":
    run()
