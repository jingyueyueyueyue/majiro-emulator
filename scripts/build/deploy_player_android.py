"""Deploy installed game data and verified overlays without launching gameplay."""
from __future__ import annotations
import argparse
import hashlib
import re
from pathlib import Path, PurePosixPath
import shlex
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, read_json, timestamp, write_json

REMOTE = "/sdcard/Download/MajiroGames"
TITLES = {
    "narci_himeko": "水仙 · 姬子", "narci_iris": "水仙 · 爱丽丝",
    "narci_onetwo": "水仙 1 & 2", "narci_sumire": "水仙 · 菫", "narci_zero": "水仙 · 零",
    "RURINOKASANE": "ルリのかさね ～いもうと物語り～", "SWITCH": "すいっチ!!",
    "White": "White ～blanche comme la lune～", "Amber Quartz": "琥珀结晶 · Amber Quartz",
    "BOKUTSUBA": "僕がサダメ 君には翼を。", "happy_princess": "はっぴぃプリンセス",
    "kizoku": "痴漢貴族", "magical_kanon2": "まじかるカナン2",
    "idol_revolution": "あいれぼ ～IDOL☆REVOLUTION～", "oh_pants": "オー！パンツ",
    "reconquista": "レコンキスタ",
}


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def verify_installed_apk(run,apk):
    """A built file is not a delivered fix until the tablet's bytes agree."""
    paths=run(["shell","pm","path","org.majiro.player"],timeout=20).splitlines()
    base=next((line[len("package:"):]for line in paths
               if line.startswith("package:")and line.endswith("/base.apk")),None)
    if not base:raise RuntimeError("installed player base APK is unavailable")
    installed=run(["shell","sha256sum "+shlex.quote(base)],timeout=30).split()[0]
    expected=digest(apk)
    if installed!=expected:raise RuntimeError("tablet still has a different APK after installation")
    package=run(["shell","dumpsys","package","org.majiro.player"],timeout=20)
    version=re.search(r"\bversionName=(\S+)",package)
    code=re.search(r"\bversionCode=(\d+)",package)
    if not version or not code:raise RuntimeError("installed player version could not be verified")
    return{"installed_apk_sha256":installed,"installed_version":version.group(1),
           "installed_version_code":int(code.group(1)),"installed_apk_bytes_verified":True}


def games():
    result = []
    for name in ("player_samples.json", "player_new_samples.json"):
        result.extend(g for g in read_json(ROOT / "config" / name)["games"] if g.get("enabled", True))
    if len({g["id"] for g in result}) != len(result):
        raise ValueError("duplicate sample id")
    return result


def payload(evidence, *, base_only=False):
    """Explicit runtime layers; source saves, installers and project files stay local."""
    files, roots = {}, []
    def add(source, target, expected=None):
        source = Path(source)
        relative = PurePosixPath(target)
        if (not source.is_file() or source.is_symlink() or relative.is_absolute()
                or ".." in relative.parts or "\\" in target):
            raise ValueError("invalid deployment member: " + target)
        if expected and digest(source) != expected:
            raise ValueError("pinned patch changed: " + target)
        # Verified patch layers deliberately replace the corresponding base member.
        files[target] = source

    for game in games():
        game_id, source = game["id"], Path(game["directory"])
        target = game.get("device_directory",("Narcissu/" if game_id.startswith("narci_") else "") + game_id)
        if PurePosixPath(target).is_absolute()or".."in PurePosixPath(target).parts or"\\"in target:
            raise ValueError("invalid device game directory")
        roots.append({"id": game_id, "directory": target, "title": TITLES.get(game_id, game_id)})
        if game.get("deployed_layout", False):
            # Recovered tablet libraries already contain the verified overlay
            # layout. Preserve those declarations until their source pipeline
            # has been regenerated; never synthesize an empty replacement.
            manifest_path = source / "patches/manifest.json"
            if manifest_path.exists():
                declaration = read_json(manifest_path)
                if declaration.get("schema") != 1:
                    raise ValueError("unknown recovered patch manifest schema: " + game_id)
            for item in source.rglob("*"):
                if not item.is_file() or item.is_symlink():
                    continue
                relative = item.relative_to(source)
                first = relative.parts[0]
                if first.lower() in ("savedata", "save", "saves", "save_chs"):
                    continue
                if base_only and first == "patches":
                    continue
                permitted = first in ("bin", "movie", "patches", "Amber_Quartz_CN")
                permitted |= len(relative.parts) == 1 and (
                    item.suffix.lower() in (".arc", ".env", ".cfg", ".dat", ".exe", ".bin")
                    or item.name == "majiro-game.json")
                if permitted and not any(term in item.name.lower() for term in ("uninst", "thumbs.db")):
                    add(item, target + "/" + relative.as_posix())
            continue
        for item in source.iterdir():
            suffix, name = item.suffix.lower(), item.name.lower()
            if not item.is_file():
                continue
            if suffix == ".arc":
                add(item, target + "/" + item.name)
            elif suffix in (".exe", ".bin") and not any(x in name for x in
                    ("uninst", "install", "setup", "autorun", "menu", "_cn", "_chs", "nodvd")):
                add(item, target + "/" + item.name)
            elif suffix in (".env", ".cfg", ".dat"):
                add(item, target + "/" + item.name)
        for folder in ("bin", "movie"):
            for item in (source / folder).rglob("*"):
                if not item.is_file() or item.is_symlink():
                    continue
                if any(x in item.name.lower() for x in ("uninst", "thumbs.db")):
                    continue
                add(item, target + "/" + item.relative_to(source).as_posix())
        if base_only:
            continue
        patch_manifest={"schema":1,"script_layers":[],"archives":[],"font_directory":"patches/fonts"}
        for item in (ROOT / "resources/font").glob("*"):
            if item.is_file():
                add(item, target + "/patches/fonts/" + item.name)
        if game_id.startswith("narci_"):
            manifest = read_json(ROOT / "localization/narcissu" / game_id / "manifest.json")
            if manifest["runtime_status"] != "original_script_text_adapter_ready":
                raise ValueError("Narcissu patch is not ready: " + game_id)
            for item in manifest["deployment"]:
                overlay=item["target"]
                if overlay.lower().endswith(".arc"):
                    overlay="patches/local/archives/"+PurePosixPath(overlay).name
                    patch_manifest["archives"].append(overlay)
                elif overlay.lower().endswith(".mjo"):
                    overlay="patches/local/scripts/"+PurePosixPath(overlay).name
                add(item["source"], target + "/" + overlay, item["sha256"])
            patch_manifest["script_layers"]=[{"directory":"patches/local/scripts","kind":"source_mjo"}]
            patch_manifest["archives"].sort(reverse=True)
            patch_manifest["image_archives"]=list(patch_manifest["archives"])
            patch_manifest["text_adapter"]={"kind":"sequential_text_v1","path":"patches/text_adapter.lua"}
        elif game_id == "RURINOKASANE":
            lock = read_json(ROOT / "localization/patch.lock.json")
            patch_source = ROOT / read_json(ROOT / "config/translation_source.json")["extracted_scenario"]
            for item in lock["overrides"]:
                add(patch_source / item["name"], target + "/patches/scenario/" + item["name"], item["sha256"])
            for item in (ROOT / "localization/images").rglob("*"):
                if item.is_file():
                    add(item, target + "/patches/images/" + item.relative_to(ROOT / "localization/images").as_posix())
            for name in ("patch.lock.json", "relocations.json", "stack_repairs.json", "character_map.json"):
                add(ROOT / "localization" / name, target + "/patches/" + name)
            metadata = read_json(ROOT / "reports/player_patch_metadata.json")
            add(ROOT / metadata["metadata"], target + "/patches/majiro_metadata.lua", metadata["sha256"])
            patch_manifest.update(script_layers=[{"directory":"patches/scenario","kind":"pinned_raw_patch"}],
                raw_metadata="patches/majiro_metadata.lua",image_directory="patches/images")
        elif game_id == "Amber Quartz":
            for item in (source / "Amber_Quartz_CN").rglob("*"):
                if item.is_file():
                    add(item, target + "/Amber_Quartz_CN/" + item.relative_to(source / "Amber_Quartz_CN").as_posix())
        metadata_file = evidence / (game_id + "_majiro-game.json")
        write_json(metadata_file, {"schema": 1, "id": game_id, "title": TITLES.get(game_id, game_id)})
        add(metadata_file, target + "/majiro-game.json")
        declaration=evidence/(game_id+"_patch_manifest.json")
        write_json(declaration,patch_manifest)
        add(declaration,target+"/patches/manifest.json")
    return sorted(((source, target) for target, source in files.items()), key=lambda item: item[1]), roots


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--base-only", action="store_true", help="Transfer base data while code/patch work continues")
    parser.add_argument("--data-only", action="store_true", help="Do not install an APK")
    parser.add_argument("--overlays-only",action="store_true",help="Update patch manifests/data without rehashing base archives")
    parser.add_argument("--manifests-only",action="store_true",help="Update declarations for already installed resource archives")
    parser.add_argument("--plan", action="store_true", help="Write the payload plan without contacting the tablet")
    args = parser.parse_args()
    evidence = archive_dir("android_deploy")
    files, roots = payload(evidence, base_only=args.base_only)
    if args.overlays_only:
        files=[(source,target)for source,target in files if "/patches/"in target or target.endswith("/majiro-game.json")]
    if args.manifests_only:
        files=[(source,target)for source,target in files if target.endswith("/patches/manifest.json")]
    total = sum(path.stat().st_size for path, _ in files)
    report = {
        "schema": 2, "timestamp": timestamp(), "serial": args.serial, "remote_root": REMOTE,
        "total_files": len(files), "total_bytes": total, "games": roots, "files": [],
        "status": "planned", "base_only": args.base_only,"overlays_only":args.overlays_only,
        "manifests_only":args.manifests_only,"gameplay_started": False,
        "evidence": str(evidence.relative_to(ROOT)),
    }
    receipt = ROOT / ("reports/android_deploy_overlays.json"if args.overlays_only else
                     "reports/android_deploy_base.json" if args.base_only else "reports/android_deploy.json")
    write_json(evidence / "payload.json", {**report, "payload": [
        {"source": str(source), "target": target, "bytes": source.stat().st_size} for source, target in files]})
    write_json(receipt, report)
    print(f"Payload: {len(roots)} games, {len(files)} files, {total / 2**30:.2f} GiB", flush=True)
    if args.plan:
        return 0
    adb = Path(read_json(ROOT / "reports/android_toolchain.json")["sdk"]) / "platform-tools/adb.exe"
    def run(arguments, timeout=1200, check=True):
        for attempt in range(4):
            try:
                result = subprocess.run([str(adb), "-s", args.serial, *arguments],
                                        capture_output=True, timeout=timeout)
            except subprocess.TimeoutExpired:
                if attempt == 3:
                    raise
                continue
            with (evidence / "adb.txt").open("ab") as log:
                log.write(("COMMAND " + repr(arguments) + "\n").encode("utf-8"))
                log.write(result.stdout + result.stderr + b"\n")
            transport_error = result.stderr.decode("utf-8", "replace").lower()
            if result.returncode and attempt < 3 and any(message in transport_error for message in (
                    "cannot connect", "daemon still not running", "device offline", "no devices", "device not found", "closed")):
                subprocess.run([str(adb), "start-server"], capture_output=True, timeout=30)
                subprocess.run([str(adb), "-s", args.serial, "wait-for-device"], capture_output=True, timeout=60)
                time.sleep(.5)
                continue
            break
        if check and result.returncode:
            raise RuntimeError(result.stderr.decode("utf-8", "replace"))
        return result.stdout.decode("utf-8", "replace").strip()

    if run(["get-state"], timeout=20) != "device":
        raise RuntimeError("selected Android device is not ready")
    available = run(["shell", "df -k /sdcard"], timeout=20).splitlines()[-1].split()
    if int(available[3]) * 1024 < total + 1024 * 1024 * 1024:
        raise RuntimeError("device free storage is below conservative payload requirement")
    run(["shell", "am force-stop org.majiro.player"], timeout=30)
    completed, directories = 0, set()
    report["status"] = "transferring"
    try:
        for index, (source, target) in enumerate(files):
            remote = REMOTE + "/" + target
            if args.manifests_only and target.endswith("/patches/manifest.json"):
                declaration=read_json(source)
                game_root=remote[:-len("/patches/manifest.json")]
                for archive in declaration.get("image_archives",[]):
                    if not archive.startswith("patches/")or".."in PurePosixPath(archive).parts:
                        raise ValueError("unsafe declared image archive")
                    run(["shell","test -f "+shlex.quote(game_root+"/"+archive)],timeout=20)
            parent = remote.rsplit("/", 1)[0]
            if parent not in directories:
                run(["shell", "mkdir -p " + shlex.quote(parent)], timeout=30)
                directories.add(parent)
            size, checksum = source.stat().st_size, digest(source)
            existing = run(["shell", "sha256sum " + shlex.quote(remote)], timeout=180, check=False)
            reused = bool(existing) and existing.split()[0] == checksum
            if not reused:
                pending = remote + ".uploading"
                run(["push", str(source), pending])
                received = run(["shell", "sha256sum " + shlex.quote(pending)], timeout=180).split()[0]
                if received != checksum:
                    raise RuntimeError("device checksum mismatch: " + target)
                run(["shell", "mv " + shlex.quote(pending) + " " + shlex.quote(remote)], timeout=30)
            report["files"].append({"path": target, "bytes": size, "sha256": checksum,
                                    "device_verified": True, "reused": reused})
            completed += size
            if size > 32 * 1024 * 1024 or index % 25 == 0 or index + 1 == len(files):
                print(f"Verified {index + 1}/{len(files)}, {completed * 100 // total}%: {target}", flush=True)
                report.update(completed_bytes=completed, timestamp=timestamp())
                write_json(receipt, report)
        if not args.data_only and not args.base_only:
            apk = Path(read_json(ROOT / "reports/android_probe_build.json")["apk"])
            run(["install", "-r", str(apk)], timeout=120)
            report.update(apk=str(apk), apk_sha256=digest(apk), apk_installed=True)
            report.update(verify_installed_apk(run,apk))
            print("Installed player verified:",report["installed_version"],report["installed_apk_sha256"],flush=True)
        report.update(status="passed", completed_bytes=completed, timestamp=timestamp())
    except Exception as error:
        report.update(status="failed", error=str(error), completed_bytes=completed)
        raise
    finally:
        write_json(receipt, report)
        write_json(evidence / "deploy_receipt.json", report)
    print("Device data verified; no gameplay launched:", REMOTE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
