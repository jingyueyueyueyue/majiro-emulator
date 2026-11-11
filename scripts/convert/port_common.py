"""Shared paths and atomic generated-file updates. Never mutate source assets."""
from __future__ import annotations
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for data in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(data)
    return digest.hexdigest()


def atomic_bytes(path: Path, value: bytes) -> bool:
    if path.is_file() and path.stat().st_size == len(value) and path.read_bytes() == value:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".build-tmp")
    try:
        with temporary.open("wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return True


def write_json(path: Path, value) -> bool:
    return atomic_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def native_pixels_library() -> Path:
    """Use the verified current build, independently of DLLs held by live hosts."""
    record=read_json(ROOT/"reports/native_build.json")
    path=Path(record.get("library",ROOT/"resources/native/majiro_pixels.dll"))
    if path.resolve().parent!=(ROOT/"resources/native").resolve()or sha256(path)!=record["sha256"]:
        raise ValueError("native pixel library does not match its build receipt")
    for name,digest in record.get("input_sha256",{}).items():
        if sha256(ROOT/name)!=digest:raise ValueError("native pixel source changed; rebuild before testing")
    return path


def player_pixels_library() -> Path:
    """Independent emulator debug artifact; never resolves into the old port."""
    record=read_json(ROOT/"reports/player_native_build.json")
    path=Path(record["library"])
    if path.resolve().parent!=(ROOT/"resources/player/native").resolve()or sha256(path)!=record["sha256"]:
        raise ValueError("independent player pixel library differs from its receipt")
    for name,digest in record.get("input_sha256",{}).items():
        if sha256(ROOT/name)!=digest:raise ValueError("player pixel source changed; rebuild its debug provider")
    return path


def config():
    return read_json(ROOT / "config/port.json")


def timestamp():
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def archive_dir(label: str) -> Path:
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    path = ROOT / "archive" / now.strftime("%Y-%m") / (now.strftime("%Y%m%d_%H%M%S_%f_") + label)
    path.mkdir(parents=True, exist_ok=False)
    return path


def lua_string(value: str) -> str:
    result = ['"']
    for char in value:
        n = ord(char)
        if char in ('"', "\\"):
            result.append("\\" + char)
        elif n < 32 or n == 127:
            result.append("\\%03d" % n)
        else:
            result.append(char)
    return "".join(result) + '"'


def lua_value(value) -> str:
    if value is None:
        return "nil"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return lua_string(value)
    if isinstance(value, (int, float)):
        import math
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("non-finite float needs a bit-preserving runtime representation")
        return repr(value)
    if isinstance(value, (tuple, list)):
        return "{" + ",".join(lua_value(v) for v in value) + "}"
    if isinstance(value, dict):
        return "{" + ",".join("[" + lua_value(k) + "]=" + lua_value(v) for k, v in value.items()) + "}"
    raise TypeError(type(value))


def generated_size():
    result = {}
    for name in ("config", "docs", "references", "scripts", "runtime", "resources", "reports", "archive"):
        base = ROOT / name
        result[name] = sum(p.stat().st_size for p in base.rglob("*") if p.is_file()) if base.exists() else 0
    return result
