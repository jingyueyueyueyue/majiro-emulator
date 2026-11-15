"""Resume a real Windows V1.104 save through verified native frames and HOT_RESET."""
from pathlib import Path
import argparse
import hashlib
import json
import sys

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / part)
                for part in ("analysis", "convert", "build")]
from desktop_pixel_session import PixelSession
from test_desktop_game_batch import files_for
from test_story_entry_regressions import run_story
from test_host_load_atomicity import captured_options
from majiro_story_save import parse_story_save
from majiro_native_save import parse_system,parse_readmarks
from port_common import ROOT, archive_dir, read_json, sha256, timestamp, write_json


def main():
    folder = archive_dir("native_story_resume")
    game = next(g for g in read_json(ROOT / "config/recovered_library.json")["games"] if g["id"] == "kiraoto")
    source = Path(game["directory"]) / "savedata/majiro_0101.sav"
    raw = source.read_bytes()
    native = parse_story_save(raw)
    native["source_sha256"] = hashlib.sha256(raw).hexdigest()
    system_source=source.parent/"majiro_system.mss"
    marks_source=source.parent/"majiro_readmark.mss"
    native_system=parse_system(system_source.read_bytes())
    native_marks=parse_readmarks(marks_source.read_bytes())
    session = PixelSession(game, files_for(game), "original", folder, seconds=90)
    started = run_story(game, files_for(game), "original", 1, 90, session=session)
    assert started["status"] == "passed", started
    lua, vm, system = session.lua, session.vm, session.system
    options = captured_options(lua, system["providers"]["$do_load"])
    scripts = {}
    required = {r["name"] for r in vm["export_state"](vm)["scripts"].values()}
    required.update(bytes.fromhex(r["script_bytes_hex"]).decode("cp932") for r in native["frames"])
    required.update(bytes.fromhex(r["script_bytes_hex"]).decode("cp932") for r in native_marks["records"])
    for name in required:
        script = vm["load"](vm, name)
        scripts[script["name"].lower()] = {"sha256": script["source_sha256"],
                                         "version_identity": options["source_identity"]}
    assignments={}
    for name in required:
        script=vm["load"](vm,name)
        for instruction in script["code"].values():
            if instruction[2]==0x1b0 and instruction[3]//32%8==0:
                assignments.setdefault(instruction[4],(script["name"],instruction[1]))
    resource_bindings=[]
    for row in native_system["variables"]:
        if row["native_type"]!=0:continue
        hash_=int(row["hash"],16);current=vm["globals"][0][hash_]
        if not isinstance(current,(int,float))or row["value"]==current or row["value"]<65536:continue
        kind=("sprite"if system["state"]["sprites"][current]is not None else
              "page"if system["state"]["pages"][current]is not None else None)
        if kind:
            owner,offset=assignments[hash_]
            resource_bindings.append({"hash":row["hash"],"script":owner,"offset":offset,
              "kind":kind,"native_value":row["value"],"current_value":current})
    sample = next(s for s in read_json(ROOT / "reports/opcode_dispatch.json")["samples"] if s["sample"] == "kiraoto")
    manifest = lua.table_from({"schema": 1, "source_identity": options["source_identity"],
        "native_exe_sha256": sample["exe_sha256"], "save_sha256": native["source_sha256"],
        "verified_same_script_version": True, "restore_kind": "native_story", "scripts": scripts,
        "system_save_sha256":native_system["source_sha256"],"readmarks_sha256":native_marks["source_sha256"],
        "resource_globals":resource_bindings}, recursive=True)
    report = {"status": "running", "timestamp": timestamp(), "native_sha256": native["source_sha256"],
              "original_game_file_modified": False, "device_accessed": False,
              "evidence": str(folder.relative_to(ROOT))}
    try:
        json_module=lua.execute((ROOT/"player/runtime/majiro_json.lua").read_text("utf8"))
        codec=lua.execute((ROOT/"player/runtime/majiro_codec.lua").read_text("utf8"))
        before=codec["encode"](vm["export_state"](vm))
        for field in ("offset","native_value","current_value"):
            invalid=codec["clone"](manifest)
            invalid["resource_globals"][1][field]+=1
            try:
                system["saves"]["plan_native_import"](vm,json_module["decode"](json.dumps(native)),invalid,
                    json_module["decode"](json.dumps(native_system)),json_module["decode"](json.dumps(native_marks)))
            except Exception:
                pass
            else:
                raise AssertionError("invalid native resource binding accepted: "+field)
            assert codec["encode"](vm["export_state"](vm))==before
        plan = system["saves"]["plan_native_import"](vm,json_module["decode"](json.dumps(native)),manifest,
            json_module["decode"](json.dumps(native_system)),json_module["decode"](json.dumps(native_marks)))
        encoded_hex=lua.eval("""function(encode,text)return(encode(text):gsub('.',
          function(c)return string.format('%02x',c:byte())end))end""")
        native_strings=0
        for row in native_system["variables"]:
            value=plan["packet"]["vm"]["globals"][0][int(row["hash"],16)]
            if row["native_type"]==2:
                assert encoded_hex(options["cp932"],value)==row["value"]["bytes_hex"]
                native_strings+=1
            elif row["native_type"]==5:
                for i,item in enumerate(row["value"]["values"]):
                    assert encoded_hex(options["cp932"],value["values"][i])==item["bytes_hex"]
                    native_strings+=1
        system["saves"]["import_native"](vm, plan)
        assert system["providers"][0x99a5de25](vm,lua.table_from(["title_off_wo_omake"]))==1
        session.last_present_request=None;session.last_signature=None
        initial = system["state"]["reading_generation"] or 0
        for tick in range(2500):
            state = system["state"]
            click = tick % 12 == 0 and (state["reading_wait"] or
                (vm["waiting"] is not None and vm["waiting"]["kind"] == "input"))
            area=None
            if tick%12==0 and state["ui_waiting"]and state["ui_areas"]and len(state["ui_areas"]):
                area=min(state["ui_areas"].values(),key=lambda item:(item["args"][3],item["args"][2]))
                click=True
            a=area["args"]if area else None
            lua.globals()["PLAYER_TICK"](session.clock, lua.table_from({
                "x":a[2]+a[4]/2+.25 if a else 511.75,"y":a[3]+a[5]/2+.625 if a else 287.625,
                "buttons": int(click), "held_buttons": int(click)}))
            if (state["reading_generation"] or 0) - initial >= 3:
                break
            session.clock += 100
        pages = (system["state"]["reading_generation"] or 0) - initial
        if pages<3:
            session.capture("native_continuation_blocked")
            from lua_bridge import table_value
            raise AssertionError({"pages":pages,"vm_status":vm["status"],
              "waiting":table_value(vm["waiting"]),"read":system["state"]["reading_wait"],
              "ui":system["state"]["ui_waiting"],
              "frames":[(f["script"]["name"],f["script"]["code"][f["index"]][1])for f in vm["frames"].values()]})
        session.capture("native_continued")
        system["providers"]["$do_save"](vm, lua.table_from([777]))
        system["providers"]["$do_load"](vm, lua.table_from([777]))
        assert sha256(source) == native["source_sha256"]
        assert sha256(system_source)==native_system["source_sha256"]
        assert sha256(marks_source)==native_marks["source_sha256"]
        assert len(list(system["state"]["progress"]["native_pictures"].items()))==len(native_system["picture_basename_hashes"])
        for record in native_marks["records"]:
            script=vm["load"](vm,bytes.fromhex(record["script_bytes_hex"]).decode("cp932"))
            for line in record["marked_line_ids"]:
                assert system["state"]["progress"]["lines"][script["source_sha256"]+":"+str(line)]
        report.update(status="passed", native_format=native["format"], frames=len(native["frames"]),
                      native_stack_values=len(native["groups"][2]["common_value_view"]["variables"]),
                      continued_pages=pages, screenshots=session.snapshots,
                      native_system_variables=len(native_system["variables"]),
                      native_strings_preserved=native_strings,
                      native_resource_pointer_bindings=len(resource_bindings),
                      invalid_resource_bindings_rejected=3,
                      native_picture_hashes=len(native_system["picture_basename_hashes"]),
                      native_readmark_records=len(native_marks["records"]))
    except Exception as error:
        report.update(status="failed", error=str(error))
        raise
    finally:
        write_json(folder / "report.json", report)
        write_json(ROOT / "reports/native_story_resume_tests.json", report)
    print("Native original story resume:", json.dumps(report, ensure_ascii=True)[:1200])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
