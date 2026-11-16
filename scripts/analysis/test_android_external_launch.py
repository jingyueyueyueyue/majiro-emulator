"""Check the public TyranorNext runtime module contract without starting an app."""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ANDROID_NS = "http://schemas.android.com/apk/res/android"
ACTION = "com.tyranor.runtime.action.LAUNCH"
PACKAGE = "com.tyranor.runtime.majiro"


def main() -> int:
    manifest = ET.parse(ROOT / "player/android/AndroidManifest.xml").getroot()
    main_activity = next(
        activity
        for activity in manifest.findall("./application/activity")
        if activity.get(f"{{{ANDROID_NS}}}name") == "org.majiro.player.MainActivity"
    )
    actions = {
        action.get(f"{{{ANDROID_NS}}}name")
        for action in main_activity.findall("./intent-filter/action")
    }
    if ACTION not in actions:
        raise AssertionError("MainActivity does not expose the external launch action")
    if main_activity.get(f"{{{ANDROID_NS}}}exported") != "true":
        raise AssertionError("MainActivity must be exported for external launch")

    descriptor = json.loads(
        (ROOT / "player/android/res/raw/tyranor_runtime.json").read_text(
            encoding="utf-8",
        ),
    )
    scan = descriptor.get("scan", {})
    if set(scan.get("extensions", ())) != {".mjo", ".arc"}:
        raise AssertionError("runtime scan descriptor must expose Majiro archive extensions")
    if "majiro.env" not in scan.get("markers", ()):
        raise AssertionError("runtime scan descriptor must keep the majiro.env marker")

    source = (ROOT / "player/android/src/org/majiro/player/MainActivity.java").read_text(
        encoding="utf-8",
    )
    for marker in (
        "ExternalLaunchContract.ACTION_LAUNCH",
        "EXTRA_PROTOCOL_VERSION",
        "EXTRA_FOLDER_PATH",
        "EXTRA_GAME_ROOT",
        "getData",
        "FLAG_GRANT_READ_URI_PERMISSION",
        "setData",
        "game_tree",
        "game_root",
        "game_path",
        "getCanonicalFile",
        "GameActivity.class",
    ):
        if marker not in source:
            raise AssertionError(f"external launch source contract is missing {marker}")

    contract = (
        ROOT / "player/android/src/org/majiro/player/ExternalLaunchContract.java"
    ).read_text(encoding="utf-8")
    for marker in (
        f'"{ACTION}"',
        '"com.tyranor.runtime.extra.FOLDER_PATH"',
        '"com.tyranor.runtime.extra.GAME_ROOT"',
        '"com.tyranor.runtime.extra.GAME_TITLE"',
        '"com.tyranor.runtime.extra.PROTOCOL_VERSION"',
        "PROTOCOL_VERSION = 1",
    ):
        if marker not in contract:
            raise AssertionError(f"external launch protocol is missing {marker}")

    print(f"Android external launch contract passed: {PACKAGE} / {ACTION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
