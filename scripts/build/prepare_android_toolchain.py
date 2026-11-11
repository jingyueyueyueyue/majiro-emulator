"""Install pinned Android native build prerequisites into an existing SDK."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit
from urllib.request import getproxies

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, timestamp, write_json

PACKAGES = ["ndk;28.2.13676358", "cmake;3.31.6", "platforms;android-36", "build-tools;36.0.0"]
REQUIRED = {
    "ndk;28.2.13676358": ["toolchains/llvm/prebuilt/windows-x86_64/bin/clang++.exe"],
    "cmake;3.31.6": ["bin/cmake.exe"],
    "platforms;android-36": ["android.jar"],
    "build-tools;36.0.0": ["aapt2.exe", "lib/apksigner.jar", "lib/d8.jar"],
}


def installed(package, path):
    metadata = ["source.properties"] if package.startswith("ndk;") else ["package.xml", "source.properties"]
    return all((path / name).is_file() for name in [*metadata, *REQUIRED[package]])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--java", type=Path, required=True)
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()
    sdk, java = args.sdk.resolve(), args.java.resolve()
    manager = sdk / "cmdline-tools/latest/bin/sdkmanager.bat"
    if not manager.is_file() or not (java / "bin/java.exe").is_file():
        parser.error("existing SDK manager and JDK are required")
    expected = [sdk / name.replace(";", "/") for name in PACKAGES]
    needed = [package for package, path in zip(PACKAGES, expected) if not installed(package, path)]
    report = {
        "timestamp": timestamp(), "primary_target": "android-arm64",
        "sdk": str(sdk), "java": str(java), "pinned_packages": PACKAGES,
        "missing_packages": needed, "status": "ready" if not needed else "missing_packages",
        "device_verified": False,
    }
    if needed and args.install:
        folder = archive_dir("android_toolchain_install")
        env = dict(os.environ)
        env["JAVA_HOME"] = str(java)
        proxy_options = []
        proxy = urlsplit(getproxies().get("https", ""))
        # sdkmanager does not inherit HTTPS_PROXY as urllib does. Reuse the
        # existing local HTTP proxy without exposing or copying credentials.
        if (proxy.scheme == "http" and proxy.hostname in ("127.0.0.1", "localhost", "::1")
                and proxy.port and not proxy.username):
            proxy_options = ["--proxy=http", "--proxy_host=" + proxy.hostname,
                             "--proxy_port=" + str(proxy.port)]
        report["existing_local_proxy_used"] = bool(proxy_options)
        print("Installing pinned Android packages; evidence:", folder, flush=True)
        with (folder / "sdkmanager.txt").open("wb") as log:
            process = subprocess.Popen(
                [str(manager), "--sdk_root=" + str(sdk), "--channel=0", *proxy_options, *needed],
                stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT, env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            process.stdin.write(b"y\n" * 12)
            process.stdin.close()
            while process.poll() is None:
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    print("Android prerequisite installation running", flush=True)
        missing = [package for package, path in zip(PACKAGES, expected) if not installed(package, path)]
        report.update(returncode=process.returncode, missing_packages=missing,
                      log=str(folder / "sdkmanager.txt"),
                      status="ready" if process.returncode == 0 and not missing else "install_failed")
    write_json(ROOT / "reports/android_toolchain.json", report)
    print(report["status"], report["missing_packages"])
    return 0 if report["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
