"""Compile new movie Java/JNI inputs without installing or running a device."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,read_json,timestamp,write_json


def main():
    tools=read_json(ROOT/"reports/android_toolchain.json")
    sdk,java=Path(tools["sdk"]),Path(tools["java"])
    folder=archive_dir("android_movie_compile")
    classes=folder/"classes";classes.mkdir()
    source=ROOT/"player/android"
    sources=[*list((source/"src").rglob("*.java")),
             *list((ROOT/"references/player/ijk/java").rglob("*.java"))]
    command=[str(java/"bin/javac.exe"),"-encoding","UTF-8","-source","8","-target","8",
             "-bootclasspath",os.pathsep.join(map(str,(sdk/"platforms/android-36/android.jar",
                 sdk/"build-tools/36.0.0/core-lambda-stubs.jar"))),
             "-classpath",str(ROOT/"references/player/SDL3/classes.jar"),
             "-d",str(classes),*map(str,sources)]
    process=subprocess.run(command,cwd=folder,capture_output=True)
    (folder/"20261110_javac.txt").write_bytes(process.stdout+process.stderr)
    if process.returncode:raise RuntimeError("movie Java compilation failed: "+str(folder))
    descriptors=subprocess.check_output([str(java/"bin/javap.exe"),"-classpath",str(classes),
                                         "-s","org.majiro.player.GameBridge"],text=True)
    assert "(Ljava/lang/String;Ljava/lang/String;IZJJ)I"in descriptors
    assert "movieFrame()"in descriptors and "()[I"in descriptors
    compiler=sdk/"ndk/28.2.13676358/toolchains/llvm/prebuilt/windows-x86_64/bin/clang++.exe"
    command=[str(compiler),"--target=aarch64-linux-android26","-std=c++20","-O2","-fPIC",
             "-I"+str(ROOT/"references/player/lua/src"),
             "-I"+str(ROOT/"references/player/SDL3/include"),
             "-I"+str(ROOT/"player/core"),"-c",str(ROOT/"player/android/game_host.cpp"),
             "-o",str(folder/"20261110_game_host.o")]
    process=subprocess.run(command,cwd=folder,capture_output=True)
    (folder/"20261110_clang.txt").write_bytes(process.stdout+process.stderr)
    if process.returncode:raise RuntimeError("movie JNI compilation failed: "+str(folder))
    report={"schema":1,"timestamp":timestamp(),"status":"passed",
            "java_sources":len(sources),"movie_descriptor":"(Ljava/lang/String;Ljava/lang/String;IZJJ)I",
            "frame_descriptor":"()[I","jni_android_compile":"arm64_api26",
            "source_sha256":{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()for name in(
                "player/android/src/org/majiro/player/MovieTextures.java",
                "player/android/src/org/majiro/player/GameBridge.java",
                "player/android/game_host.cpp","player/runtime/majiro_media.lua")},
            "device_tests_executed":False,"actual_decode_verified":False,
            "evidence":str(folder.relative_to(ROOT))}
    write_json(ROOT/"reports/android_movie_compile_tests.json",report)
    write_json(folder/"20261110_android_movie_compile_tests.json",report)
    print("Android movie host Java and ARM64 JNI compilation passed; no device execution")


if __name__=="__main__":main()
