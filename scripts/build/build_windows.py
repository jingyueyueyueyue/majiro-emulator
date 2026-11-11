"""Assemble one runnable engine environment, sharing all generated assets."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, atomic_bytes, config, lua_value, read_json, sha256, write_json,native_pixels_library
from majiro_image import Decoder


def junction(link: Path, target: Path):
    target = target.resolve(strict=True)
    if link.exists():
        if not os.path.samefile(link, target):
            raise ValueError(f"unexpected link target: {link}")
        return
    if link.is_symlink():
        raise ValueError(f"dangling unexpected link: {link}")
    link.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(link), str(target)],
                            capture_output=True)
    if result.returncode or not os.path.samefile(link, target):
        raise RuntimeError(f"junction failed; copying is forbidden: {link}")


def hardlink(source: Path, destination: Path):
    source = source.resolve(strict=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and os.path.samefile(source, destination):
        return
    temporary = destination.with_name(destination.name + ".link-tmp")
    try:
        os.link(source, temporary)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    if not os.path.samefile(source, destination):
        raise RuntimeError("hardlink did not preserve identity; no copy fallback")


def create_shortcut(project: Path, executable: Path):
    shortcut = ROOT / "启动移植测试.lnk"
    environment = dict(os.environ)
    environment.update(RURI_SHORTCUT=str(shortcut), RURI_PROJECT=str(project), RURI_ENGINE=str(executable))
    # Paths cross the PowerShell boundary through Unicode environment values.
    command = (
        "$shell = New-Object -ComObject WScript.Shell; "
        "$link = $shell.CreateShortcut($env:RURI_SHORTCUT); "
        "if ($link.TargetPath -ne $env:RURI_ENGINE -or $link.WorkingDirectory -ne $env:RURI_PROJECT) { "
        "$link.TargetPath = $env:RURI_ENGINE; $link.WorkingDirectory = $env:RURI_PROJECT; "
        "$link.Description = 'Ruri Artemis initialization development test'; $link.Save() }"
    )
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                   env=environment, check=True, capture_output=True)
    if not shortcut.exists():
        raise RuntimeError("development shortcut was not created")
    return shortcut


def build():
    settings = config()
    project = ROOT / "projects/windows"
    project.mkdir(parents=True, exist_ok=True)
    engine_source = ROOT / settings["windows_engine"]
    if not engine_source.is_file():
        raise FileNotFoundError(engine_source)
    engine_target = project / "Ruri_Artemis.exe"
    # The user explicitly requested a local copy of the required engine.
    if not engine_target.exists() or sha256(engine_source) != sha256(engine_target):
        atomic_bytes(engine_target, engine_source.read_bytes())
    for name, target in (("runtime", ROOT / "runtime"),
                         ("scripts", ROOT / "resources/scripts"),
                         ("native", ROOT / "resources/native"),
                         ("image", ROOT / "resources/image")):
        target.mkdir(parents=True, exist_ok=True)
        junction(project / name, target)
    (ROOT/"resources/audio").mkdir(parents=True,exist_ok=True)
    junction(project/"audio",ROOT/"resources/audio")
    junction(project/"movie",ROOT/"movie")
    font_source = ROOT / settings["font_source"]
    hardlink(font_source, project / "font/SourceHanSansSC-Regular.otf")
    font_license = font_source.with_name("SourceHanSansSC-LICENSE.txt")
    if font_license.exists():
        hardlink(font_license, project / "font/SourceHanSansSC-LICENSE.txt")
    ui = read_json(ROOT / "localization/ui_zh.json")
    atomic_bytes(project / "config/ui_zh.lua", ("return " + lua_value(ui) + "\n").encode("utf-8"))
    hardlink(ROOT / "runtime/first.iet", project / "system/first.iet")
    hardlink(ROOT / "runtime/movie.iet", project / "system/movie.iet")
    from majiro_movie import catalogue as movies
    atomic_bytes(project/"config/movies.lua",("return "+lua_value(movies())+"\n").encode("utf8"))
    aliases=read_json(ROOT/"config/resource_aliases.json")
    from majiro_arc import Catalogue
    assert Catalogue(ROOT).get("config.cfg").digest()==aliases["config_sha256"],"CONFIG alias source changed"
    atomic_bytes(project/"config/resource_aliases.lua",
                 ("return "+lua_value(aliases["audio"])+"\n").encode("utf8"))
    assets_path = ROOT / "resources/image_catalogue.json"
    assets = read_json(assets_path) if assets_path.exists() else {}
    if "title_back.rct" not in assets:
        assets["title_back.rct"] = Decoder().convert("title_back")
    write_json(assets_path, assets)
    atomic_bytes(ROOT / "resources/scripts/image_catalogue.lua",
                 ("return " + lua_value(assets) + "\n").encode("utf-8"))
    inputs = {}
    for folder, pattern in (("runtime", "*"), ("localization", "*.json"), ("config", "*.json"),
                            ("scripts/convert","*.py")):
        paths = (ROOT / folder).rglob(pattern) if folder == "runtime" else (ROOT / folder).glob(pattern)
        for path in sorted(paths):
            if path.is_file():
                if folder == "localization" and path.name == "proofread_map.json":
                    # Review bundle metadata is not a game runtime input.
                    continue
                inputs[path.relative_to(ROOT).as_posix()] = sha256(path)
    source_manifest = read_json(ROOT / "resources/source_manifest.json")
    inputs["source"] = source_manifest["source_identity"]
    inputs["engine"] = sha256(engine_source)
    inputs["font"] = sha256(font_source)
    inputs["image_catalogue"] = sha256(assets_path)
    inputs["native_pixels"] = sha256(native_pixels_library())
    inputs["compiled_manifest"] = sha256(ROOT / "resources/source_manifest.json")
    inputs["reference_lock"] = sha256(ROOT / "config/references.lock.json")
    inputs["image_catalogue_lua"] = sha256(ROOT / "resources/scripts/image_catalogue.lua")
    inputs["audio_catalogue"] = sha256(ROOT/"resources/audio_catalogue.json")
    inputs["audio_catalogue_lua"] = sha256(ROOT/"resources/scripts/audio_catalogue.lua")
    build_id = hashlib.sha256(lua_value(inputs).encode("utf-8")).hexdigest()
    atomic_bytes(project / "config/build.lua", ("return " + lua_value({
        "build_id":build_id,"source_identity":inputs["source"],
        "save_identity":source_manifest.get("save_identity",inputs["source"]),
        "save_compatible_identities":source_manifest.get("save_compatible_identities",[])}) + "\n").encode())
    atomic_bytes(project / "system.ini", (
        "; Development initialization test. Full gameplay is not implemented.\n"
        "[WINDOWS]\nBOOT = system/first.iet\nWIDTH = 1280\nHEIGHT = 720\n"
        "CHARSET = UTF-8\nSAVEPATH = savedata-dev\nSURFACE_CACHE_SIZE = 67108864\n"
        "FRAMELESS = 0\nRESIZABLE = 1\nFIXED_ASPECT_RATIO = 1\n"
    ).encode("utf-8"))
    atomic_bytes(project / "README.txt", (
        "Artemis 移植开发测试\n\n双击 Ruri_Artemis.exe。\n"
        "可测试原标题、正文翻页、设置、存档及重新启动后的读档。\n"
        "正文右键打开菜单；返回标题和正常退出均使用原版菜单。当前完整剧情尚未验收。\n"
        "存档写入 savedata-dev/port-data，图像音频仍引用共享资源。\n"
        "开发诊断出现时 Enter 重新启动，Esc 退出。回想与全路线仍在开发。\n"
        "引擎已放入本目录；脚本和图像引用工程的唯一一份数据，请保留目录结构。\n"
    ).encode("utf-8-sig"))
    record = {"schema": 1, "build_id": build_id, "inputs": inputs,
              "executable": engine_target.relative_to(ROOT).as_posix(),
              "engine_copy_bytes": engine_target.stat().st_size,
              "font_shared_bytes": font_source.stat().st_size,
              "resources_copied": False, "copy_fallback": False,
              "gameplay_status": "story_settings_save_load_development", "release_allowed": False}
    record["deployed"] = {
        path.relative_to(project).as_posix(): sha256(path)
        for path in (project / "system.ini", project / "system/first.iet",
                     project / "config/build.lua", project / "config/ui_zh.lua",
                     project / "config/movies.lua", project / "system/movie.iet",project/"config/resource_aliases.lua")
    }
    record["shortcut"] = create_shortcut(project, engine_target).relative_to(ROOT).as_posix()
    write_json(ROOT / "reports/windows_build.json", record)
    return record


def verify():
    record = read_json(ROOT / "reports/windows_build.json")
    inputs = record["inputs"]
    project = ROOT / "projects/windows"
    for name, expected in inputs.items():
        if name.startswith(("runtime/", "localization/", "config/", "scripts/")):
            if sha256(ROOT / name) != expected:
                raise ValueError("project source changed: " + name)
    paths = {
        "engine": project / "Ruri_Artemis.exe",
        "font": project / "font/SourceHanSansSC-Regular.otf",
        "image_catalogue": ROOT / "resources/image_catalogue.json",
        "native_pixels": native_pixels_library(),
        "compiled_manifest": ROOT / "resources/source_manifest.json",
        "reference_lock": ROOT / "config/references.lock.json",
        "image_catalogue_lua": ROOT / "resources/scripts/image_catalogue.lua",
        "audio_catalogue":ROOT/"resources/audio_catalogue.json",
        "audio_catalogue_lua":ROOT/"resources/scripts/audio_catalogue.lua",
    }
    for key, path in paths.items():
        if sha256(path) != inputs[key]:
            raise ValueError("project input changed: " + key)
    for path, expected in record["deployed"].items():
        if sha256(project / path) != expected:
            raise ValueError("project generated file changed: " + path)
    native_record = read_json(ROOT / "reports/native_build.json")
    if native_record["source_sha256"] != sha256(ROOT / "runtime/native/majiro_pixels.cpp"):
        raise ValueError("native provider source changed")
    for name, target in (("runtime", ROOT / "runtime"), ("scripts", ROOT / "resources/scripts"),
                         ("native", ROOT / "resources/native"), ("image", ROOT / "resources/image")):
        if not os.path.samefile(project / name, target):
            raise ValueError("shared project path changed: " + name)
    catalogue = read_json(ROOT / "resources/image_catalogue.json")
    from majiro_arc import Catalogue
    sources = Catalogue(ROOT)
    identities = {m.identity: m for a in sources.archives for m in a.members}
    for asset in catalogue.values():
        if sha256(ROOT / "resources" / asset["file"]) != asset["sha256"]:
            raise ValueError("converted image mismatch: " + asset["name"])
        for dep in asset["sources"]:
            if identities[dep["member"]].digest() != dep["sha256"]:
                raise ValueError("image source mismatch: " + dep["name"])
    for asset in read_json(ROOT/"resources/audio_catalogue.json").values():
        if sha256(ROOT/"resources"/asset["file"])!=asset["sha256"]:
            raise ValueError("audio resource mismatch: "+asset["name"])
    return {"status": "passed", "build_id": record["build_id"], "images": len(catalogue)}


if __name__ == "__main__":
    print(verify() if "--verify-only" in sys.argv else build()["executable"])
