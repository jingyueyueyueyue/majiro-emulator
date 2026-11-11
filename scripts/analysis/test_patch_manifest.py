"""Compile and exercise the generic local-layer manifest, plus real boot paths."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,read_json,sha256,timestamp,write_json
from lua_bridge import create_lua


def main():
    folder=archive_dir("patch_manifest_tests");classes=folder/"classes";classes.mkdir()
    tools=read_json(ROOT/"reports/android_toolchain.json");java=Path(tools["java"])/"bin"
    jar=ROOT/"references/player/testing-json/json-20240303.jar"
    assert sha256(jar)=="3cf6cd6892e32e2b4c1c39e0f52f5248a2f5b37646fdfbb79a66b46b618414ed"
    production=ROOT/"player/android/src/org/majiro/player/PatchManifest.java"
    fixture=ROOT/"scripts/analysis/PatchManifestChecks.java"
    def run(name,args):
        result=subprocess.run(args,capture_output=True,timeout=90)
        (folder/(folder.name[:8]+"_"+name+".txt")).write_bytes(result.stdout+result.stderr)
        if result.returncode:raise RuntimeError(name+" failed: "+str(folder))
        return result.stdout
    run("javac",[str(java/"javac.exe"),"-encoding","UTF-8","-source","8","-target","8",
        "-classpath",str(jar),"-d",str(classes),str(production),str(fixture)])
    result=json.loads(run("java",[str(java/"java.exe"),"-cp",os.pathsep.join((str(classes),str(jar))),
                                  "org.majiro.player.PatchManifestChecks"]))
    lua=create_lua();boot=(ROOT/"player/runtime/player_boot.lua").read_text(encoding="utf8")
    prefix=boot[:boot.index("local configuration=")]
    loaded=[]
    config={"schema":1,"legacy":False,"raw_metadata":"patches/arbitrary/metadata.lua",
            "text_adapter":{"kind":"sequential_text_v1","path":"patches/other/text.lua"}}
    host=lua.table()
    def asset(path):
        if path=="runtime/majiro_patch.lua":
            return"return {new=function(data)return {decode=function(v)return v end}end}"
        return(ROOT/"player"/path).read_text(encoding="utf8")
    host["asset_text"]=asset;host["patch_configuration"]=lambda:json.dumps(config)
    def game_text(path):
        loaded.append(path)
        if path==config["raw_metadata"]:return"return {schema=1,scripts={}}"
        return None
    host["game_text"]=game_text;lua.globals()["HOST"]=host
    for mode in("original","online"):
        host["text_mode"]=lambda mode=mode:mode
        lua.execute(prefix)
        assert not loaded and lua.globals()["PLAYER_PATCH_METADATA"]is None
    host["text_mode"]=lambda:"local";lua.execute(prefix)
    assert loaded==["patches/arbitrary/metadata.lua"]
    assert lua.globals()["PLAYER_PATCH_METADATA"]["schema"]==1
    host["game_text"]=lambda name:None
    ok,why=lua.eval("function(source)return pcall(assert(loadstring(source)))end")(prefix)
    assert not ok and"declared raw patch metadata is missing"in str(why)
    config["legacy"]=True;lua.execute(prefix)
    cpp=(ROOT/"player/android/game_host.cpp").read_text(encoding="utf8")
    bridge=(ROOT/"player/android/src/org/majiro/player/GameBridge.java").read_text(encoding="utf8")
    assert "for(const auto& layer:patch_layers)"in cpp and 'method("patchLayers"'in cpp
    assert 'std::sort(rows.begin(),rows.end()'not in cpp[cpp.index("void initialize_sources()"):cpp.index("void install_pixels()")]
    assert 'new ArrayList<String>(patch.archives)'in bridge
    assert 'PatchManifest.parse(text,inspectLocal)'in bridge
    result.update(schema=1,timestamp=timestamp(),lua_boot_checks=6,device_accessed=False,
        windows_game_executed=False,manifest_path="patches/manifest.json",
        scope="production manifest parser, generic precedence and local-only boot source resolution",
        evidence=str(folder.relative_to(ROOT)),
        source_sha256={name:sha256(ROOT/name)for name in(
            "player/android/src/org/majiro/player/PatchManifest.java",
            "player/android/src/org/majiro/player/GameBridge.java","player/android/game_host.cpp",
            "player/runtime/player_boot.lua","scripts/analysis/PatchManifestChecks.java",
            "scripts/analysis/test_patch_manifest.py")})
    write_json(ROOT/"reports/patch_manifest_tests.json",result)
    write_json(folder/"20261110_patch_manifest_tests.json",result)
    print("Generic patch manifest:",result["checks"],"JVM and",result["lua_boot_checks"],"boot checks passed")
    return 0


if __name__=="__main__":raise SystemExit(main())
