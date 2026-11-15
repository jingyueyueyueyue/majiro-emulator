"""Restore recovered player slots with exact archived scripts and native pixels."""
from pathlib import Path, PurePosixPath
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
import sys
import tarfile

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / part)
                for part in ("analysis", "convert", "build")]
from desktop_pixel_session import PixelSession
from test_desktop_game_batch import files_for
from test_host_load_atomicity import captured_options
from lua_bridge import create_lua
from port_common import ROOT, archive_dir, read_json, sha256, timestamp, write_json


def records():
    lua = create_lua()
    codec = lua.execute((ROOT / "player/runtime/majiro_codec.lua").read_text("utf8"))
    with tarfile.open(ROOT.parent / "resources/recovery/app-private-20261110.tar") as archive:
        objects = {m.name: archive.extractfile(m).read() for m in archive.getmembers()
                   if m.isfile() and m.name.endswith((".mjs", ".mjb"))}
    result = []
    for name, raw in objects.items():
        if not name.endswith("/index.mjs"):
            continue
        base = PurePosixPath(name).parent
        def get(descriptor):
            pieces = []
            for digest in descriptor["parts"].values():
                data = objects[str(base / (digest + ".mjb"))]
                assert hashlib.sha256(data).hexdigest() == digest
                pieces.append(data)
            raw = b"".join(pieces)
            assert len(raw) == descriptor["bytes"]
            return raw.decode("ascii")
        index = codec["decode"](get(codec["decode"](raw.decode("ascii"))))
        for slot, record in index["slots"].items():
            encoded = get(record["snapshot"])
            packet = codec["decode"](encoded)
            commands = []
            for graphic in record["graphics"].values():
                data = get(graphic)
                batch = (codec["decode"](data) if data.startswith("MJS1\n") else data).encode()
                at = 6
                while at < len(batch):
                    end = batch.index(b"\n", at)
                    length = int(batch[at:end])
                    commands.append(batch[end + 1:end + 1 + length].decode())
                    at = end + 1 + length
                assert at == len(batch)
            result.append({"slot": slot, "identity": index["identity"], "packet": encoded,
                           "commands": commands,
                           "game": "RURINOKASANE" if packet["vm"]["scripts"][1]["sha256"] ==
                           "5ad8d3467d3efaf8684d77db8005d7e3ae67f1e8f3f69a86fc7e5ee5adcd0b8b" else "oh_pants"})
    return result


def worker(record, directory):
    folder = Path(directory) / record["slot"]
    folder.mkdir(parents=True, exist_ok=True)
    games = read_json(ROOT / "config/recovered_library.json")["games"]
    game = next(g for g in games if g["id"] == record["game"])
    session = PixelSession(game, files_for(game), "original", folder, seconds=90)
    startup = session.run()
    assert not startup["error"], startup["error"]
    lua, system, vm = session.lua, session.system, session.vm
    codec = lua.execute((ROOT / "player/runtime/majiro_codec.lua").read_text("utf8"))
    packet = codec["decode"](record["packet"])
    source_loader = vm["loader"]
    if record["game"] == "RURINOKASANE":
        catalogue = lua.execute((ROOT / "resources/scripts/catalogue.lua").read_text("utf8"))
        names = {name.casefold(): value["id"] for name, value in catalogue["scripts"].items()}
        def load(name):
            return lua.execute((ROOT / "resources/scripts" / (names[name.casefold()] + ".lua")).read_text("utf8"))
        vm["loader"] = load
    # Bind every recorded script to bytes retained before upgrading. A changed
    # hash rejects the test rather than replacing the saved source identity.
    for source in packet["vm"]["scripts"].values():
        actual = vm["loader"](source["name"])
        assert actual["source_sha256"] == source["sha256"], source["name"]
    options = captured_options(lua, system["providers"]["$do_extload"])
    options["compatible_save_identity"] = lambda identity: identity == record["identity"]
    document = codec["encode"](lua.table_from({
        "format": "MajiroPlayerExternalSave", "schema": 1, "identity": record["identity"],
        "packet": packet, "graphics": lua.table_from(record["commands"]),
    }))
    options["external_save"] = lambda action, value: "OK\n" + document if action == "load" else "OK"
    system["providers"]["$do_extload"](vm, lua.table())
    assert vm["status"] == "running"
    missing=[page for page in system["state"]["pages"].keys()
             if session.native(f"pixel {int(page)} 0 0").startswith("ERR")]
    assert not missing, {"missing_saved_pages":missing}
    session.last_present_request=None
    session.last_signature=None
    session.lua.globals()["PLAYER_TICK"](session.clock,lua.table_from({"buttons":0,"held_buttons":0}))
    session.capture("restored")
    initial = system["state"]["reading_generation"] or 0
    pages = 0
    pending=None;right_sent=False
    for tick in range(1000):
        state = system["state"]
        menu=any(f["script"]["name"]in("loadsave.mjo","pausemenu.mjo")for f in vm["frames"].values())
        input_wait=vm["waiting"] is not None and vm["waiting"]["kind"]=="input"
        close_menu=tick%12==0 and menu and(state["ui_waiting"]or input_wait)and not right_sent
        if close_menu:right_sent=True
        click = tick % 12 == 0 and (state["reading_wait"] or
            (vm["waiting"] is not None and vm["waiting"]["kind"] == "input"))
        area=None
        if not close_menu and tick%12==0 and state["ui_waiting"]and state["ui_areas"]and len(state["ui_areas"]):
            candidates=[item for item in state["ui_areas"].values()if item["args"][4]>0 and item["args"][5]>0]
            area=min(candidates,key=lambda item:(item["args"][3],item["args"][2]))
            pending=area;click=False
        elif tick%12==1 and pending:
            area=pending;pending=None;click=True
        a=area["args"]if area else None
        session.lua.globals()["PLAYER_TICK"](session.clock, lua.table_from({
            "x": a[2]+a[4]/2+.25 if a else 511.75,
            "y": a[3]+a[5]/2+.625 if a else 287.625, "buttons": 2 if close_menu else 1 if click else 0,
            "held_buttons": 2 if close_menu else 1 if click else 0}))
        pages = (state["reading_generation"] or 0) - initial
        if pages >= 3:
            break
        assert vm["status"] != "blocked", str(vm["problem"])
        deadline=vm["next_deadline"](vm)
        session.clock=max(session.clock+100,min(deadline or session.clock+100,session.clock+1000))
    if pages<3:
        session.capture("continuation_blocked")
        from lua_bridge import table_value
        raise AssertionError({"slot":record["slot"],"pages":pages,"status":vm["status"],
            "waiting":table_value(vm["waiting"]),"ui":system["state"]["active_ui_script"],
            "areas":[[a["args"][i]for i in range(1,6)]for a in(system["state"]["ui_areas"]or lua.table()).values()],
            "frames":[(f["script"]["name"],f["script"]["code"][f["index"]][1])for f in vm["frames"].values()]})
    system["providers"]["$do_save"](vm, lua.table_from([777]))
    system["providers"]["$do_load"](vm, lua.table_from([777]))
    session.capture("continued_and_reloaded")
    return {"slot": record["slot"], "game": record["game"], "status": "passed", "continued_pages": pages,
            "original_identity": record["identity"], "scripts_verified": len(packet["vm"]["scripts"]),
            "screenshots": session.snapshots, "player_save_source_modified": False}


def main(arguments=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slot", action="append")
    args = parser.parse_args(arguments)
    folder = archive_dir("historical_gameplay_saves")
    cases = [r for r in records() if not args.slot or r["slot"] in args.slot]
    report = {"status": "running", "timestamp": timestamp(), "cases": [], "device_accessed": False,
              "evidence": str(folder.relative_to(ROOT)),
              "archive_sha256": sha256(ROOT.parent / "resources/recovery/app-private-20261110.tar")}
    with ProcessPoolExecutor(max_workers=2) as pool:
        pending = {pool.submit(worker, record, str(folder)): record for record in cases}
        for future in as_completed(pending):
            record = pending[future]
            try:
                row = future.result()
            except Exception as error:
                row = {"slot": record["slot"], "game": record["game"], "status": "failed", "error": str(error)}
            report["cases"].append(row)
            print(json.dumps(row, ensure_ascii=True)[:1800], flush=True)
            write_json(folder / "report.json", report)
            write_json(ROOT / "reports/historical_gameplay_save_tests.json", report)
    report["status"] = "passed" if all(row["status"] == "passed" for row in report["cases"]) else "failed"
    write_json(folder / "report.json", report)
    write_json(ROOT / "reports/historical_gameplay_save_tests.json", report)
    return int(report["status"] != "passed")


if __name__ == "__main__":
    raise SystemExit(main())
