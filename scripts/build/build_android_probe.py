"""Package the native core's Android resource diagnostic app without Gradle."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import zipfile
import struct
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, atomic_bytes, read_json, sha256, timestamp, write_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recovery",action="store_true",
        help="Build a separately signed, co-installable recovery app; keep the original installation intact")
    args=parser.parse_args()
    tools = read_json(ROOT / "reports/android_toolchain.json")
    native = read_json(ROOT / "reports/player_core_build.json")
    if tools["status"] != "ready" or native["android"] != "cross_compiled":
        raise RuntimeError("prepare_android_toolchain.py and build_player_core.py --android must pass first")
    from build_player_core import core_inputs
    if native.get("input_sha256")!=core_inputs():
        raise RuntimeError("native parser inputs changed; run build_player_core.py --android before packaging")
    if sha256(Path(native["android_library"]))!=native["android_library_sha256"]:
        raise RuntimeError("native parser library does not match its build receipt")
    sdk, java = Path(tools["sdk"]), Path(tools["java"])
    build_tools = sdk / "build-tools/36.0.0"
    android_jar = sdk / "platforms/android-36/android.jar"
    folder = archive_dir("android_probe_build")
    classes, dex = folder / "classes", folder / "dex"
    classes.mkdir(); dex.mkdir()
    source = ROOT / "player/android"
    resource_inputs={path.relative_to(source).as_posix():sha256(path)
                     for path in (source/"res").rglob("*")if path.is_file()}
    manifest_hash=sha256(source/"AndroidManifest.xml")
    manifest_path=source/"AndroidManifest.xml"
    application_id="org.majiro.player"
    version_namespace="{http://schemas.android.com/apk/res/android}"
    if args.recovery:
        namespace="{http://schemas.android.com/apk/res/android}"
        tree=ET.parse(manifest_path);manifest=tree.getroot()
        original_id=manifest.attrib["package"]
        application_id="org.majiro.player.recovery"
        manifest.set("package",application_id)
        manifest.set(namespace+"versionName","0.1")
        manifest.set(namespace+"versionCode","11")
        for item in manifest.iter():
            if item.tag in("activity","service","receiver","provider","application","instrumentation"):
                name=item.get(namespace+"name")
                if name and name.startswith("."):item.set(namespace+"name",original_id+name)
            if item.tag=="instrumentation":item.set(namespace+"targetPackage",application_id)
        manifest.find("application").set(namespace+"label","Majiro 模拟器")
        manifest_path=folder/"AndroidManifest.xml"
        tree.write(manifest_path,encoding="utf-8",xml_declaration=True)
    package_manifest=ET.parse(manifest_path).getroot()
    version_name=package_manifest.get(version_namespace+"versionName")
    version_code=int(package_manifest.get(version_namespace+"versionCode"))
    java_sources = [*[path for path in (source/"src").rglob("*.java")
                     if path.name!="EngineCodeIdentity.java"],
                    *list((ROOT / "references/player/ijk/java").rglob("*.java"))]
    java_inputs={path.relative_to(ROOT).as_posix():sha256(path)for path in java_sources}
    sdl_jar = ROOT / "references/player/SDL3/classes.jar"
    commands = [
        [str(java / "bin/javac.exe"), "-encoding", "UTF-8", "-source", "8", "-target", "8",
         "-bootclasspath", os.pathsep.join(map(str,(android_jar,build_tools/"core-lambda-stubs.jar"))), "-classpath", str(sdl_jar),
         "-d", str(classes), *map(str, java_sources)],
    ]
    def run(name, command):
        result = subprocess.run(command, cwd=folder, capture_output=True)
        (folder / (name + ".txt")).write_bytes(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError(f"{name} failed: {folder / (name + '.txt')}")
        return result
    run("javac", commands[0])
    parser_api=run("javap_native_core",[str(java/"bin/javap.exe"),"-classpath",str(classes),
        "-s","-p","org.majiro.player.NativeCore"])
    parser_signatures={
        "inspectArchive":"(IJ)Ljava/lang/String;",
        "inspectImage":"([B[B)Ljava/lang/String;",
        "inspectNativeSave":"(IJZ)Ljava/lang/String;",
    }
    parsed={};current=None
    for line in parser_api.stdout.decode("utf-8").splitlines():
        match=re.search(r'\bstatic\s+native\s+\S+\s+(\w+)\(',line)
        if match:current=match.group(1)
        elif line.strip().startswith("descriptor:"):
            if current:parsed[current]=line.split("descriptor:",1)[1].strip()
            current=None
    if parsed!=parser_signatures:
        raise RuntimeError("compiled parser Java/JNI descriptor mismatch: "+repr(parsed))
    native_core_text=(ROOT/"player/android/native_bridge.cpp").read_text(encoding="utf-8")
    for name in parser_signatures:
        if "Java_org_majiro_player_NativeCore_"+name+"("not in native_core_text:
            raise RuntimeError("native parser JNI implementation missing: "+name)
    # Check the compiled Java descriptors against the actual native call sites
    # before an APK reaches a device. C++ compilation cannot type-check JNI.
    native_source=ROOT/"player/android/game_host.cpp"
    native_text=native_source.read_text(encoding="utf-8")
    literal=re.compile(r'method\(\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\)')
    conditional=re.compile(r'method\(\s*\w+\s*\?\s*"([^"]+)"\s*:\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\)')
    declarations=re.compile(r'\bjmethodID\s+method\([^)]*\)')
    expected=set(literal.findall(native_text))
    covered=[match.span()for pattern in(literal,conditional,declarations)for match in pattern.finditer(native_text)]
    for first,second,signature in conditional.findall(native_text):
        expected.update(((first,signature),(second,signature)))
    unmatched=[match.start()for match in re.finditer(r'\bmethod\(',native_text)
               if not any(start<=match.start()<end for start,end in covered)]
    if unmatched or len(expected)<16:
        raise RuntimeError("native JNI bridge call-site recovery changed; update the package verifier")
    result=run("javap_bridge",[str(java/"bin/javap.exe"),"-classpath",str(classes),
                                "-s","-p","org.majiro.player.GameBridge"])
    actual=set();current=None
    for line in result.stdout.decode("utf-8").splitlines():
        match=re.search(r'(\w+)\([^;]*\)(?:\s+throws\s+[^;]+)?;',line)
        if match:current=match.group(1)if" static "in line else None
        elif line.strip().startswith("descriptor:"):
            if current:actual.add((current,line.split("descriptor:",1)[1].strip()))
            current=None
    missing=sorted(expected-actual)
    if missing:raise RuntimeError("compiled Java/JNI descriptor mismatch: "+repr(missing))
    game=read_json(ROOT/"reports/android_game_build.json")
    from build_android_game import native_inputs
    if game.get("input_sha256")!=native_inputs():
        raise RuntimeError("native source/header/adapter inputs changed; run build_android_game.py before packaging")
    compiled_native=next((value for name,value in game["source_sha256"].items()
        if Path(name).as_posix()=="player/android/game_host.cpp"),None)
    if compiled_native!=sha256(native_source)or sha256(Path(game["library"]))!=game["sha256"]:
        raise RuntimeError("native game bridge changed; run build_android_game.py before packaging")
    if(game.get("pixel_source_sha256")!=sha256(ROOT/"player/native/majiro_pixels.cpp")
       or game.get("screen_view_sha256")!=sha256(ROOT/"player/core/screen_view.hpp")):
        raise RuntimeError("shared presentation core changed; run build_android_game.py before packaging")
    jni_evidence=[{"method":name,"descriptor":signature}for name,signature in sorted(expected)]
    write_json(folder/(folder.name[:8]+"_jni_bridge.json"),{
        "status":"passed","methods":jni_evidence,"cpp_sha256":sha256(native_source),
        "class_sha256":sha256(classes/"org/majiro/player/GameBridge.class")})
    run("d8", [str(java / "bin/java.exe"), "-cp", str(build_tools / "lib/d8.jar"),
               "com.android.tools.r8.D8", "--min-api", "26", "--lib", str(android_jar),
               "--output", str(dex), str(sdl_jar), *map(str, classes.rglob("*.class"))])
    unsigned, aligned, signed = folder / "unsigned.apk", folder / "aligned.apk", folder / "signed.apk"
    source_assets={}
    binary_assets={"classes.dex":dex/"classes.dex",
        "lib/arm64-v8a/libmajiro_core.so":Path(native["android_library"]),
        "lib/arm64-v8a/libmajiro_game.so":Path(game["library"]),
        "lib/arm64-v8a/libSDL3.so":ROOT/"references/player/SDL3/arm64-v8a/libSDL3.so"}
    binary_assets.update({"lib/arm64-v8a/"+path.name:path
        for path in(ROOT/"references/player/ijk/arm64-v8a").glob("*.so")})
    binary_inputs={target:sha256(path)for target,path in binary_assets.items()}
    compiled_resources=folder/"compiled-resources.zip"
    # Windows aapt2 directory enumeration rejects Unicode --dir paths; files
    # still accept Unicode names, so enumerate from the source cwd.
    resource_compile=subprocess.run([str(build_tools/"aapt2.exe"),"compile","--dir","res","-o",str(compiled_resources)],
                                    cwd=source,capture_output=True)
    (folder/"aapt2_compile.txt").write_bytes(resource_compile.stdout+resource_compile.stderr)
    if resource_compile.returncode:raise RuntimeError("aapt2 compile failed: "+str(folder/"aapt2_compile.txt"))
    run("aapt2", [str(build_tools / "aapt2.exe"), "link", "-I", str(android_jar),
                  "--manifest", str(manifest_path), "-o", str(unsigned),str(compiled_resources)])
    with zipfile.ZipFile(unsigned, "a", compression=zipfile.ZIP_DEFLATED) as apk:
        for target,path in binary_assets.items():
            apk.write(path,target)
        for license_file in (ROOT / "references/player/ijk/licenses").iterdir():
            if license_file.is_file():
                apk.write(license_file, "assets/licenses/ijk/" + license_file.name)
        for path in (ROOT / "player/runtime").glob("*.lua"):
            if not path.name.startswith("artemis_")and path.name not in(
                "player_native_library.lua","majiro_engine_identity.lua","majiro_engine_profiles.lua",
                "majiro_engine_code_profiles.lua","majiro_platform_profiles.lua",
                "majiro_external_profiles.lua","majiro_engine_generations.lua"):
                target="assets/runtime/"+path.name
                source_assets[target]=(path,path.read_bytes())
                apk.writestr(target,source_assets[target][1])
        for name in ("opcodes.lua", "syscall_names.lua","tile_ai.lua"):
            path=ROOT/"player/tables"/name;target="assets/scripts/"+name
            source_assets[target]=(path,path.read_bytes())
            apk.writestr(target,source_assets[target][1])
        apk.write(ROOT / "references/player/SDL3/LICENSE.txt", "assets/licenses/SDL3.txt")
        apk.write(ROOT / "references/player/lua/COPYRIGHT", "assets/licenses/Lua.txt")
        # The independently generated parser fixtures travel with this debug
        # APK, so the same JNI API can be checked on a real ARM64 device.
        sys.path.insert(0, str(ROOT / "scripts/analysis"))
        from test_formats import arc, mjo
        code = struct.pack("<HiH", 0x800, 0, 0x82B)
        for version in (1, 2, 3):
            fixture = arc(version, [(b"start.mjo", mjo(code)),
                                    (b"functions.mjo", mjo(code, main=0xFFFFFFFF))])
            apk.writestr(f"assets/core-test-v{version}.arc", fixture)
        apk.writestr("assets/core-test-v4.arc", b"truncated-invalid-archive")
        from test_encrypted_images import fixture_key, image
        password = b"fixture-key"
        key_bytes = fixture_key(password)
        plain = b"\x01\x02\x03\x00\x04\x05\x06"
        encrypted = bytes(value ^ key_bytes[i] for i, value in enumerate(plain + b"TS"))
        apk.writestr("assets/core-image-tc00.rct", image(b"TC00", plain))
        apk.writestr("assets/core-image-ts00.rct", image(b"TS00", encrypted))
        palette = bytes(value for index in range(256) for value in (index, index, index))
        apk.writestr("assets/core-image-rc8.rc8",
                     b"\x98\x5a\x92\x9a8_00" + struct.pack("<III", 2, 1, 3)
                     + palette + b"\0\0\1")
        apk.writestr("assets/core-image-bad.rct", image(b"TC00", b"\x01"))
        from test_native_saves import synthetic_system
        from test_player_native_saves import readmark
        apk.writestr("assets/core-save-system.mss",synthetic_system())
        apk.writestr("assets/core-save-readmarks.mss",readmark())
        for version,header_size,fields in((103,0xa3e0,0x90),(105,0xa560,0x210)):
            header=bytearray(header_size)
            header[:16]=f"MajiroSavV1.{version}\0".encode("ascii")
            struct.pack_into("<7I",header,fields,0,0,0,1,0,0,0)
            frame=bytearray(0xa8);frame[:12]=b"fixture.mjo\0"
            fixture=bytes(header)+bytes(frame)+struct.pack("<4I",1,0,0,42)
            from majiro_story_save import parse_story_save
            assert parse_story_save(fixture)["groups"][0]["common_value_view"]["variable_count"]==1
            apk.writestr(f"assets/core-save-story{version}.mss",fixture)
        apk.writestr("assets/core-save-bad.mss",b"MajiroSavV1.105\0"+bytes(64))
        build_provenance={"schema":1,"timestamp":timestamp(),
            "application_id":application_id,"version_name":version_name,"version_code":version_code,
            "java_source_sha256":java_inputs,"binary_asset_sha256":binary_inputs,
            "runtime_source_sha256":{target:hashlib.sha256(raw).hexdigest()
                for target,(path,raw)in sorted(source_assets.items())},
            "native_game_source_sha256":game["input_sha256"],
            "native_parser_source_sha256":native["input_sha256"],
            "device_verified":False}
        provenance_raw=json.dumps(build_provenance,ensure_ascii=True,sort_keys=True).encode("utf8")
        apk.writestr("assets/build-provenance.json",provenance_raw)
    run("zipalign", [str(build_tools / "zipalign.exe"), "-f", "-P", "16", "4", str(unsigned), str(aligned)])
    key = ROOT / ("config/player-recovery-debug.keystore"if args.recovery else"config/player-debug.keystore")
    if not key.is_file():
        run("debug_key", [
            str(java / "bin/keytool.exe"), "-genkeypair", "-keystore", str(key),
            "-alias", "androiddebugkey", "-storepass", "android", "-keypass", "android",
            "-dname", "CN=Majiro Debug,O=Local Development,C=US", "-keyalg", "RSA",
            "-keysize", "2048", "-validity", "10000", "-noprompt"])
    apksigner = build_tools / "lib/apksigner.jar"
    run("apksigner", [
        str(java / "bin/java.exe"), "-jar", str(apksigner), "sign",
        "--ks", str(key), "--ks-pass", "pass:android", "--key-pass", "pass:android",
        "--out", str(signed), str(aligned)])
    run("verify_signature", [str(java / "bin/java.exe"), "-jar", str(apksigner),
                             "verify", "--verbose", str(signed)])
    run("verify_alignment", [str(build_tools / "zipalign.exe"), "-c", "-P", "16", "4", str(signed)])
    badging=run("verify_package_identity",[str(build_tools/"aapt2.exe"),"dump","badging",str(signed)])
    identity=re.search(r"^package: name='([^']+)' versionCode='(\d+)' versionName='([^']+)'",badging.stdout.decode("utf-8"),re.MULTILINE)
    if not identity or identity.groups()!=(application_id,str(version_code),version_name):
        raise RuntimeError("signed APK package/version differs from the requested recovery identity")
    with zipfile.ZipFile(signed)as apk:
        for target,(path,raw)in source_assets.items():
            if path.read_bytes()!=raw or apk.read(target)!=raw:
                raise RuntimeError("shared source changed during packaging or APK contains stale code: "+target)
        for target,path in binary_assets.items():
            if(sha256(path)!=binary_inputs[target]
                    or hashlib.sha256(apk.read(target)).hexdigest()!=binary_inputs[target]):
                raise RuntimeError("signed APK binary differs from its verified build: "+target)
        if apk.read("assets/build-provenance.json")!=provenance_raw:
            raise RuntimeError("signed APK build provenance changed")
    if java_inputs!={path.relative_to(ROOT).as_posix():sha256(path)for path in java_sources}:
        raise RuntimeError("Java inputs changed during packaging; rebuild before using this APK")
    if resource_inputs!={path.relative_to(source).as_posix():sha256(path)for path in(source/"res").rglob("*")if path.is_file()}or manifest_hash!=sha256(source/"AndroidManifest.xml"):
        raise RuntimeError("Android manifest/resources changed during packaging")
    if game.get("input_sha256")!=native_inputs()or native.get("input_sha256")!=core_inputs():
        raise RuntimeError("native inputs changed during packaging; rebuild before using this APK")
    runtime_assets=[{"asset":target,"source":path.relative_to(ROOT).as_posix(),
        "sha256":hashlib.sha256(raw).hexdigest()}for target,(path,raw)in sorted(source_assets.items())]
    destination = ROOT / ("resources/player/android/majiro-Recovery.apk"if args.recovery else
                          "resources/player/android/majiro-Emulator.apk")
    atomic_bytes(destination, signed.read_bytes())
    report = {
        "status": "built_and_package_verified", "timestamp": timestamp(),
        "application_id":application_id,"recovery_variant":args.recovery,
        "version_name":version_name,"version_code":version_code,"signed_package_identity_verified":True,
        "binary_asset_sha256":binary_inputs,
        "embedded_provenance_sha256":hashlib.sha256(provenance_raw).hexdigest(),
        "signing_key":str(key),"original_installation_replaced":False,
        "apk": str(destination), "sha256": sha256(destination), "bytes": destination.stat().st_size,
        "abi": "arm64-v8a", "minimum_api": 26, "target_api": 36,
        "native_library_sha256": native["android_library_sha256"],
        "page_alignment": 16384, "device_verified": False, "playable": False,
        "purpose": "Android SAF diagnostics and SDL3 shared-VM execution host under validation",
        "game_host_sha256": game["sha256"],
        "jni_bridge_signatures":jni_evidence,
        "jni_bridge_class_sha256":sha256(classes/"org/majiro/player/GameBridge.class"),
        "jni_parser_signatures":parser_signatures,
        "java_input_sha256":java_inputs,
        "android_resource_sha256":resource_inputs,"manifest_sha256":manifest_hash,
        "runtime_source_verification":{"status":"passed","assets":runtime_assets},
        "evidence": str(folder),
    }
    write_json(ROOT / "reports/android_probe_build.json", report)
    (ROOT/"resources/player/android/Majiro-Resource-Probe.apk").unlink(missing_ok=True)
    # These are reproducible packaging intermediates; the signed APK has one formal location.
    for path in (unsigned, aligned, signed, folder / "signed.apk.idsig"):
        path.unlink(missing_ok=True)
    print("Android diagnostic APK:", destination)
    print("Shared game VM host packaged; gameplay validation remains in progress")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
