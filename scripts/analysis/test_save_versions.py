"""Execute historical save writers and current load/continue/save transactions."""
from pathlib import Path
from functools import lru_cache
import hashlib
import json
import re
import subprocess
import sys
import tarfile
import zipfile

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / part)
                for part in ("analysis", "convert", "build")]
from lua_bridge import create_lua
from native_pixels import Pixels
from port_common import ROOT, archive_dir, read_json, sha256, timestamp, write_json


@lru_cache(maxsize=None)
def git_source(ref, path):
    return subprocess.check_output(["git", "show", ref + ":" + path], cwd=ROOT).decode("utf8")


def versions():
    result, seen = [], set()
    commits = subprocess.check_output(["git", "rev-list", "--reverse", "HEAD"], cwd=ROOT, text=True).splitlines()
    for ref in commits:
        manifest = git_source(ref, "player/android/AndroidManifest.xml")
        name = re.search(r'android:versionName="([^"]+)"', manifest)[1]
        code = int(re.search(r'android:versionCode="(\d+)"', manifest)[1])
        if (name, code) in seen:
            continue
        seen.add((name, code))
        result.append({"name": name, "code": code, "kind": "git", "ref": ref})
    tools = read_json(ROOT / "reports/android_toolchain.json")
    aapt = Path(tools["sdk"]) / "build-tools/36.0.0/aapt2.exe"
    paths = [
        ROOT.parent / "resources/recovery/majiro-installed-20261110.apk",
        ROOT / "archive/2026-11/20261111_085150_365770_pre_01_apk_preservation/previous_recovery.apk",
        ROOT / "archive/2026-11/20261111_094450_398963_public_01_release_assets/Majiro-0.1-arm64-v8a.apk",
        ROOT / "archive/2026-11/20261111_release_0_2/Majiro-0.2-arm64-v8a.apk",
        ROOT / "archive/2026-11/20261112_startup_hotfix/Majiro-0.2-arm64-v8a.apk",
        ROOT / "archive/2026-11/20261112_chapter_pause_hotfix/Majiro-0.2-arm64-v8a.apk",
        ROOT / "archive/2026-11/20261114_192504_627761_android_release/Majiro-0.2-arm64-v8a.apk",
    ]
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError("historical APK required: " + str(path))
        badging = subprocess.check_output([str(aapt), "dump", "badging", str(path)], text=True, encoding="utf8")
        match = re.search(r"^package: name='([^']+)' versionCode='(\d+)' versionName='([^']+)'", badging)
        assert match and match[1].startswith("org.majiro.player")
        result.append({"name": match[3], "code": int(match[2]), "kind": "apk", "path": str(path),
                       "sha256": sha256(path)})
    manifest=(ROOT/"player/android/AndroidManifest.xml").read_text("utf8")
    result.append({"name":re.search(r'android:versionName="([^"]+)"',manifest)[1],
                   "code":int(re.search(r'android:versionCode="(\d+)"',manifest)[1]),
                   "kind":"current"})
    return result


def source(version, path):
    if version["kind"] == "git":
        return git_source(version["ref"], "player/" + path)
    if version["kind"] == "current":
        return (ROOT / "player" / path).read_text("utf8")
    with zipfile.ZipFile(version["path"]) as archive:
        asset = "assets/" + ("scripts/" + Path(path).name if path.startswith("tables/") else path)
        return archive.read(asset).decode("utf8")


def fixture(version, files, identity, native, legacy_files=None, legacy_identity=None):
    lua = create_lua()
    def module(path):
        actual = "tables/" + Path(path).name if path.startswith(("scripts/", "resources/scripts/")) else path
        return lua.execute(source(version, actual))
    codec = module("runtime/majiro_codec.lua")
    def host(request):
        if request.startswith("sha256\n"):
            return "OK " + hashlib.sha256(request[7:].encode()).hexdigest()
        action, _, rest = request.partition(" ")
        name, _, data = rest.partition("\n")
        if action=="save_legacy_identity":return"OK "+legacy_identity
        if action=="save_legacy_catalog":
            return"OK data\n"+json.dumps([{"root":1,"name":key}for key in sorted(legacy_files)
                if key=="index.mjs"or(key.startswith("index_")and key.endswith(".mjs"))])
        if action=="save_legacy_read":
            root,filename=name.split(" ",1)
            assert root=="1"
            return"OK data\n"+legacy_files[filename]if filename in legacy_files else"OK absent"
        if action=="save_legacy_copy":
            root,filename=name.split(" ",1);assert root=="1"
            files[filename]=legacy_files[filename];return"OK"
        if action == "save_exists":
            return "OK " + str(int(name in files))
        if action == "save_read":
            return "OK data\n" + files[name] if name in files else "OK absent"
        if action == "save_write":
            files[name] = data
            return "OK"
        if action == "save_delete":
            files.pop(name, None)
            return "OK"
        raise AssertionError(action)
    store = module("runtime/majiro_store.lua")["new"](codec, host, identity)
    clock, document, audio = {"now": 100}, {"value": ""}, []
    def media_asset(name):
        return lua.table_from({"file": "/fresh/" + name, "duration_ms": 1000})
    def play(action, spec):
        if action == "play":
            assert spec["file"].startswith("/fresh/"), "expired historical audio path reused"
        audio.append(action)
        return action != "completed"
    def external(action, data):
        if action == "save":
            document["value"] = data
            return "OK"
        return "OK\n" + document["value"]
    options = lua.table_from({"names": module("scripts/syscall_names.lua"), "source_identity": identity,
        "config_files": {}, "config_lines": {}, "environment": {}, "resource_names": {"voice.ogg": True},
        "width": 800, "height": 600}, recursive=True)
    options["modules"], options["store"] = module, store
    options["now"], options["set_clock"] = lambda: clock["now"], lambda value: clock.update(now=value)
    options["graphics"], options["audio"], options["media_asset"] = native, play, media_asset
    options["external_save"], options["cp932"] = external, lambda text: text.encode("cp932")
    system = module("runtime/majiro_syscalls.lua")["new"](options)
    script = lua.table_from({"name": "version-story.mjo", "source_sha256": "c" * 64,
        "main": 0, "functions": [[101, 100]],
        "code": [[0, 0x800, 7], [6, 0x80f, 101, 0, 1], [18, 0x1b0, 32, 123, -1],
                 [28, 0x800, 0], [34, 0x82b], [100, 0x836, [0]], [105, 0x800, 3],
                 [111, 0x800, 4], [117, 0x835, 0xf8004c93, 0], [125, 0x118],
                 [127, 0x802, 96, 999, -2], [137, 0x118], [139, 0x82b]]}, recursive=True)
    # Resolve the actual original save_point hash from each shipped name table.
    script["code"][9][3] = next(hash_ for hash_, name in options["names"].items() if name == "$save_point")
    vm = module("runtime/majiro_vm.lua")["new"](lua.table_from({
        "loader": lambda _: codec["clone"](script), "opcodes": module("scripts/opcodes.lua"),
        "syscall": system["invoke"], "resume_action": system["resume_action"], "string_ops": system["strings"]}))
    vm["begin"](vm, script["name"])
    return locals()


def historical_case(version, mode, external, migrate=False):
    identity = hashlib.sha256(("version-fixture:" + mode).encode()).hexdigest()
    files = {}
    native = Pixels()
    old = fixture(version, files, identity, native)
    lua, system, vm = old["lua"], old["system"], old["vm"]
    p, state = system["providers"], system["state"]
    page = p["$page_create"](vm, lua.table_from([2, 2]))
    p["$grp_boxfill"](vm, lua.table_from([page, 0, 0, 2, 2, 773834]))
    lua.eval("""function(state,vm)
      local a={kind='array',element_type=1,dims={2},values={[0]=1.25,[1]=-2.5}}
      vm.globals[1][234]=a;state.version_shared=a
      state.media.channels.music={id='music',file='/evicted/music.ogg',name='voice',
        duration_ms=1000,generation=4,started=0,loop=true,channel_volume=500,volume=500}
      state.media.generation=4
    end""")(state, vm)
    # Execute the historical writer from a real nested call and a nonempty operand stack.
    for _ in range(6):
        vm["step"](vm)
    assert vm["frame"](vm)["script"]["code"][vm["frame"](vm)["index"]][1] == 125
    if external:
        p["$do_extsave"](vm, lua.table())
    else:
        p["$do_save"](vm, lua.table_from([101]))
    disk = dict(files)
    portable_document = old["document"]["value"]
    assert native("reset") == "OK"
    if migrate:
        current_identity=hashlib.sha256(("upgraded:"+mode).encode()).hexdigest()
        upgraded_files={}
        new=fixture({"kind":"current"},upgraded_files,current_identity,native,files,identity)
        if not external:
            def validate(packet,commands):
                new["module"]("runtime/majiro_save.lua")["validate_checkpoint"](packet,commands)
                staging=new["module"]("runtime/majiro_vm.lua")["new"](new["lua"].table_from({
                    "loader":lambda _:new["codec"]["clone"](new["script"]),
                    "opcodes":new["module"]("scripts/opcodes.lua"),"syscall":lambda *args:None}))
                staging["import_state"](staging,new["codec"]["clone"](packet["vm"]))
            recovery=new["module"]("runtime/majiro_save_compatibility.lua")["new"](
                new["store"],new["host"],new["module"]("runtime/majiro_json.lua"),new["lua"].table(),validate)
            assert recovery["recover"]()["recovered"]==1
            new=fixture({"kind":"current"},upgraded_files,current_identity,native,files,identity)
        else:
            from test_host_load_atomicity import captured_options
            captured_options(new["lua"],new["system"]["providers"]["$do_extload"])[
                "compatible_save_identity"]=lambda value:value==identity
    else:new = fixture({"kind": "current"}, files, identity, native)
    new["document"]["value"] = portable_document
    owner, providers = new["vm"], new["system"]["providers"]
    providers["$do_extload"](owner, new["lua"].table()) if external else providers["$do_load"](
        owner, new["lua"].table_from([101]))
    assert native(f"pixel {page} 0 0") == "OK 202 206 11 255"
    new["lua"].eval("""function(state,vm)
      assert(state.version_shared==vm.globals[1][234])
      assert(vm.globals[1][234].element_type==1 and vm.globals[1][234].values[0]==1.25)
      assert(#vm.frames==2 and #vm.stack==2 and vm:frame().args[1]==7)
    end""")(new["system"]["state"], owner)
    assert "play" in new["audio"]
    for _ in range(7):
        if owner["status"] == "finished":
            break
        owner["step"](owner)
    assert owner["status"] == "finished" and owner["globals"][1][123] == 14
    providers["$do_save"](owner, new["lua"].table_from([102]))
    assert new["system"]["state"]["save_catalog"]["slot_00102"] is not None
    if migrate:assert files==disk,"upgrade changed the original historical save directory"
    else:assert all(files[name] == data for name, data in disk.items() if name != "index.mjs" and name in files)
    return {"version": version["name"], "version_code": version["code"], "source": version["kind"],
            "mode": mode, "external": external, "identity_migration":migrate,
            "nested_continue_result": 14, "status": "passed"}


def recovered_objects():
    path = ROOT.parent / "resources/recovery/app-private-20261110.tar"
    lua = create_lua()
    codec = lua.execute((ROOT / "player/runtime/majiro_codec.lua").read_text("utf8"))
    saves = lua.execute((ROOT / "player/runtime/majiro_save.lua").read_text("utf8"))
    with tarfile.open(path) as archive:
        objects = {m.name: archive.extractfile(m).read() for m in archive.getmembers()
                   if m.isfile() and m.name.endswith((".mjs", ".mjb"))}
    checks = {"indexes": 0, "slots": 0, "hashed_parts": 0}
    def read(base, descriptor):
        parts = []
        for digest in descriptor["parts"].values():
            raw = objects[str(base / (digest + ".mjb"))]
            assert hashlib.sha256(raw).hexdigest() == digest
            checks["hashed_parts"] += 1
            parts.append(raw)
        raw = b"".join(parts)
        assert len(raw) == descriptor["bytes"]
        return raw.decode("ascii")
    for name, raw in objects.items():
        if not name.endswith("/index.mjs"):
            continue
        base = Path(name).parent
        # TAR paths always use '/', including on Windows.
        from pathlib import PurePosixPath
        base = PurePosixPath(name).parent
        index = codec["decode"](read(base, codec["decode"](raw.decode("ascii"))))
        assert index["schema"] == 1
        checks["indexes"] += 1
        for slot, record in index["slots"].items():
            packet = codec["decode"](read(base, record["snapshot"]))
            commands = []
            for graphic in record["graphics"].values():
                raw = read(base, graphic)
                batch = (codec["decode"](raw) if raw.startswith("MJS1\n") else raw).encode()
                at = 6
                assert batch.startswith(b"batch\n")
                while at < len(batch):
                    end = batch.index(b"\n", at)
                    length = int(batch[at:end])
                    commands.append(batch[end + 1:end + 1 + length].decode())
                    at = end + 1 + length
                assert at == len(batch)
            saves["validate_checkpoint"](packet, lua.table_from(commands))
            assert packet["identity"] == index["identity"]
            checks["slots"] += 1
    assert checks["indexes"] == 25 and checks["slots"] == 5
    return {"archive_sha256": sha256(path), "status": "passed", **checks}


def main():
    folder = archive_dir("save_versions")
    inventory = versions()
    report = {"status": "running", "timestamp": timestamp(), "versions": inventory,
              "cases": [], "device_accessed": False, "player_saves_modified": False,
              "evidence": str(folder.relative_to(ROOT))}
    try:
        for version in inventory:
            for mode in ("original", "local", "online"):
                for external in (False, True):
                    for migrate in(False,True):
                        report["cases"].append(historical_case(version,mode,external,migrate))
            print(version["kind"],version["name"],version["code"],"12 write/migrate/load/continue cases passed",flush=True)
        report["recovered_real_saves"] = recovered_objects()
        report["status"] = "passed"
    except Exception as error:
        report.update(status="failed", error=str(error))
        raise
    finally:
        write_json(folder / "report.json", report)
        write_json(ROOT / "reports/save_versions_tests.json", report)
    print("Save versions:", len(report["cases"]), "historical writer roundtrips passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
