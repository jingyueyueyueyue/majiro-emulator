"""Verify automatic patch-copy isolation using production Java and actual MJO/Lua."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path[:0]=[str(Path(__file__).resolve().parents[1]/"convert"),
              str(Path(__file__).resolve().parents[1]/"build")]
from port_common import ROOT,archive_dir,read_json,timestamp,write_json
from majiro_arc import Archive
from majiro_mjo import Script
from lua_bridge import create_lua


def main():
    folder=archive_dir("archive_layer_tests")
    classes=folder/"classes";classes.mkdir()
    java=Path(read_json(ROOT/"reports/android_toolchain.json")["java"])/"bin"
    jar=ROOT/"references/player/testing-json/json-20240303.jar"
    sources=[
        ROOT/"player/android/src/org/majiro/player/ArchiveLayerPolicy.java",
        ROOT/"player/android/src/org/majiro/player/PatchManifest.java",
        ROOT/"scripts/analysis/ArchiveLayerChecks.java",
    ]
    def run(label,arguments):
        result=subprocess.run(arguments,capture_output=True,timeout=90)
        (folder/("20261111_"+label+".txt")).write_bytes(result.stdout+result.stderr)
        if result.returncode:
            raise RuntimeError(label+" failed: "+result.stderr.decode("utf8","replace"))
        return result.stdout.decode("utf8")
    run("javac",[str(java/"javac.exe"),"-encoding","UTF-8","-source","8","-target","8",
        "-cp",str(jar),"-d",str(classes),*map(str,sources)])
    samples=read_json(ROOT/"config/player_samples.json")["games"]+read_json(ROOT/"config/player_new_samples.json")["games"]
    games=[game for game in samples if(Path(game["directory"])/"patches/manifest.json").is_file()]
    result=json.loads(run("java",[str(java/"java.exe"),"-Dfile.encoding=UTF-8",
        "-cp",os.pathsep.join((str(classes),str(jar))),
        "org.majiro.player.ArchiveLayerChecks",*[game["directory"]for game in games]]))
    tested=next(game for game in games if game["id"]=="narci_onetwo")
    root=Path(tested["directory"])
    duplicate=next(row["duplicates"]for row in result["games"]if Path(row["directory"])==root)
    assert duplicate.get("update.arc")=="patches/local/archives/update.arc",duplicate
    archives=[(path.name,Archive(path))for path in sorted(root.glob("*.arc"),reverse=True)
              if path.name not in duplicate and path.name!="savedata.arc"]
    original={}
    for _,archive in archives:
        for member in archive.members:original.setdefault(member.name.lower(),member)
    before=Archive(root/"update.arc")
    broken=next(member for member in before.members if member.name=="nar2_01.mjo")
    damaged=Script(broken.name,broken.read())
    bad_first=next(row.operands[0]for row in damaged.instructions if row.name=="text")
    lua=create_lua()
    data=lua.execute((root/"patches/text_adapter.lua").read_text(encoding="utf8"))
    adapter=lua.execute((ROOT/"player/runtime/majiro_text_patch.lua").read_text(encoding="utf8"))["new"](
        data,lambda value:value.encode("cp932"))
    units=0;story_first=None;translated_first=None
    for name,member in original.items():
        if member.extension!=".mjo":continue
        script=Script(member.name,member.read())
        if name=="nar2_01.mjo":
            story_first=next(row.operands[0]for row in script.instructions if row.name=="text")
        record=data["scripts"][name]
        if record is None:continue
        source=root/"patches/local/scripts"/member.name
        assert source.read_bytes()==member.read(),name
        output=adapter["apply"](lua.table_from(script.runtime_data(),recursive=True))
        assert output["text_patch"]==data["identity"]
        units+=len(record["entries"])
        if name=="nar2_01.mjo":
            entry=next(value for value in record["entries"].values()if value["opcode"]==0x840)
            translated_first=entry["value"]
    assert story_first=="\uff11\uff26\u306e\u5916\u6765\u53d7\u4ed8\u306e\u5148\u3002",story_first
    assert bad_first!=story_first and translated_first and translated_first!=story_first
    # The detector is data-only: no game names, hashes, authentication or text recoding.
    policy=sources[0].read_text(encoding="utf8")
    assert not any(value in policy for value in("MessageDigest","sha256","narci_","fingerprint","Charset"))
    bridge=(ROOT/"player/android/src/org/majiro/player/GameBridge.java").read_text(encoding="utf8")
    assert "ArchiveLayerPolicy.duplicates(base,localArchives" in bridge
    assert "duplicateArchives.containsKey(key(name))" in bridge
    from test_story_entry_regressions import run_story
    files={path.relative_to(root).as_posix():path for path in root.rglob("*")
           if path.is_file()and path.relative_to(root).as_posix()not in duplicate}
    stories=[]
    for mode in("original","online","local"):
        story=run_story(tested,files,mode,3,12)
        assert story["status"]=="passed",story
        stories.append(story)
    result.update(schema=1,timestamp=timestamp(),device_accessed=False,
        local_translation_units_checked=units,
        story={"before":bad_first,"original_and_online":story_first,"local":translated_first},
        story_runs=stories,
        detection="file size then direct streaming byte comparison; no game fingerprints",
        evidence=str(folder.relative_to(ROOT)))
    write_json(ROOT/"reports/archive_layer_tests.json",result)
    write_json(folder/"20261111_archive_layer_tests.json",result)
    print(json.dumps({"status":result["status"],"checks":result["checks"],"games":len(games),
        "duplicates":duplicate,"local_translation_units_checked":units,
        "story_runs":[{"mode":row["mode"],"status":row["status"],"pages":row["pages"]}
                      for row in stories]},ensure_ascii=True))
    return 0


if __name__=="__main__":raise SystemExit(main())
