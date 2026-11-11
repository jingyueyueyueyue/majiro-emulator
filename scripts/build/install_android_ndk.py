"""Install the pinned official NDK using verified ranges through existing proxy settings."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
from pathlib import Path, PurePosixPath
import shutil
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, timestamp, write_json

VERSION = "28.2.13676358"
BASE = "https://dl.google.com/android/repository/"


def metadata():
    request = urllib.request.Request(BASE + "repository2-3.xml", headers={"User-Agent": "MajiroBuild/1.0"})
    with urllib.request.urlopen(request, timeout=45) as response:
        document = ET.fromstring(response.read())
    package = next(row for row in document.iter() if row.attrib.get("path") == "ndk;" + VERSION)
    archive = next(row for row in package.findall("./archives/archive") if row.findtext("host-os") == "windows")
    complete = archive.find("complete")
    checksum = complete.find("checksum")
    if checksum.attrib.get("type", "sha1") not in ("sha1", "sha-1"):
        raise ValueError("unsupported official checksum type")
    return {
        "url": BASE + complete.findtext("url"),
        "bytes": int(complete.findtext("size")),
        "sha1": checksum.text.strip().lower(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path, required=True)
    args = parser.parse_args()
    sdk = args.sdk.resolve()
    if not (sdk / "cmdline-tools").is_dir():
        parser.error("an existing Android SDK is required")
    destination = (sdk / "ndk" / VERSION).resolve()
    if not destination.is_relative_to(sdk):
        raise ValueError("NDK target escapes SDK")
    properties = destination / "source.properties"
    if properties.is_file() and VERSION in properties.read_text(encoding="utf-8"):
        compiler = destination / "toolchains/llvm/prebuilt/windows-x86_64/bin/clang++.exe"
        if compiler.is_file():
            print("Pinned NDK already available:", destination)
            return 0
    record = metadata()
    evidence = archive_dir("ndk_verified_download")
    archive_path = evidence / "android-ndk-r28c-windows.zip"
    chunk_size = 8 * 1024 * 1024
    with archive_path.open("wb") as stream:
        stream.truncate(record["bytes"])

    def download(start):
        end = min(record["bytes"], start + chunk_size) - 1
        for attempt in range(3):
            try:
                request = urllib.request.Request(record["url"], headers={
                    "Range": f"bytes={start}-{end}", "User-Agent": "MajiroBuild/1.0"})
                with urllib.request.urlopen(request, timeout=60) as response:
                    expected = f"bytes {start}-{end}/{record['bytes']}"
                    if response.status != 206 or response.headers.get("Content-Range") != expected:
                        raise ValueError("range response does not match requested archive bytes")
                    data = response.read(end - start + 2)
                    if len(data) != end - start + 1:
                        raise ValueError("short or oversized range")
                with archive_path.open("r+b") as stream:
                    stream.seek(start)
                    stream.write(data)
                return len(data)
            except Exception:
                if attempt == 2:
                    raise

    done, next_progress = 0, 64 * 1024 * 1024
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = [pool.submit(download, start) for start in range(0, record["bytes"], chunk_size)]
        for job in as_completed(jobs):
            done += job.result()
            if done >= next_progress or done == record["bytes"]:
                print(f"NDK download: {done * 100 // record['bytes']}%", flush=True)
                next_progress += 64 * 1024 * 1024
    sha1, sha256 = hashlib.sha1(), hashlib.sha256()
    with archive_path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            sha1.update(block); sha256.update(block)
    if sha1.hexdigest() != record["sha1"]:
        raise ValueError("official NDK archive checksum mismatch; preserved diagnostic download")
    with zipfile.ZipFile(archive_path) as archive:
        files = []
        for entry in archive.infolist():
            path = PurePosixPath(entry.filename)
            if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "android-ndk-r28c":
                raise ValueError("unexpected NDK archive path")
            if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("unexpected NDK archive symlink")
            if len(path.parts) == 1:
                continue
            target = destination.joinpath(*path.parts[1:]).resolve()
            if not target.is_relative_to(destination):
                raise ValueError("NDK extraction target escapes installation directory")
            files.append((entry, target))
        print("NDK checksum verified; extracting pinned toolchain", flush=True)
        for entry, target in files:
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
    compiler = destination / "toolchains/llvm/prebuilt/windows-x86_64/bin/clang++.exe"
    if not compiler.is_file() or VERSION not in properties.read_text(encoding="utf-8"):
        raise ValueError("extracted NDK revision or compiler validation failed")
    record.update(status="installed", timestamp=timestamp(), version=VERSION,
                  destination=str(destination), sha256=sha256.hexdigest())
    write_json(ROOT / "reports/android_ndk_install.json", record)
    write_json(evidence / "install_receipt.json", record)
    archive_path.unlink()
    print("Pinned NDK installed:", destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
