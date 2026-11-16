"""Check the public Android launch contract without starting an app."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ANDROID_NS = "http://schemas.android.com/apk/res/android"
ACTION = "org.majiro.player.action.LAUNCH"
PACKAGE = "org.majiro.player"


def main() -> int:
    manifest = ET.parse(ROOT / "player/android/AndroidManifest.xml").getroot()
    main_activity = next(
        activity
        for activity in manifest.findall("./application/activity")
        if activity.get(f"{{{ANDROID_NS}}}name") == ".MainActivity"
    )
    actions = {
        action.get(f"{{{ANDROID_NS}}}name")
        for action in main_activity.findall("./intent-filter/action")
    }
    if ACTION not in actions:
        raise AssertionError("MainActivity does not expose the external launch action")
    if main_activity.get(f"{{{ANDROID_NS}}}exported") != "true":
        raise AssertionError("MainActivity must be exported for external launch")

    source = (ROOT / "player/android/src/org/majiro/player/MainActivity.java").read_text(
        encoding="utf-8",
    )
    for marker in (
        "ExternalLaunchContract.ACTION_LAUNCH",
        "EXTRA_PROTOCOL_VERSION",
        "game_tree",
        "game_root",
        "FLAG_GRANT_READ_URI_PERMISSION",
        "FLAG_GRANT_PREFIX_URI_PERMISSION",
        "takePersistableUriPermission",
        "DocumentsContract.isChildDocument",
        "GameActivity.class",
    ):
        if marker not in source:
            raise AssertionError(f"external launch source contract is missing {marker}")

    contract = (
        ROOT / "player/android/src/org/majiro/player/ExternalLaunchContract.java"
    ).read_text(encoding="utf-8")
    for marker in (
        f'"{ACTION}"',
        '"game_root"',
        '"game_title"',
        '"protocol_version"',
        "PROTOCOL_VERSION = 1",
    ):
        if marker not in contract:
            raise AssertionError(f"external launch protocol is missing {marker}")

    print(f"Android external launch contract passed: {PACKAGE} / {ACTION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
