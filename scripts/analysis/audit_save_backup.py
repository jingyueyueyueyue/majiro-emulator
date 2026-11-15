"""Read and validate a player's backed-up save objects without changing them."""
from pathlib import Path, PurePosixPath
import argparse
import hashlib
import json
import sys
import tarfile

from lupa.lua51 import LuaRuntime
sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "convert")]
from port_common import ROOT, timestamp, write_json


def read_backup(path, orphan_indexes=False):
    lua = LuaRuntime(encoding=None, unpack_returned_tuples=True)
    codec = lua.execute((ROOT / "player/runtime/majiro_codec.lua").read_bytes())
    with tarfile.open(path) as archive:
        objects = {m.name: archive.extractfile(m).read() for m in archive
                   if m.isfile() and m.name.endswith((".mjs", ".mjb"))}
    rows = []
    for name, raw in objects.items():
        orphan = (orphan_indexes and name.endswith(".mjb") and raw.startswith(b"MJS1\n")
                  and b"S5\n736c6f7473" in raw)
        if not name.endswith(".mjs") and not orphan:
            continue
        base = PurePosixPath(name).parent

        def get(descriptor):
            pieces = []
            for digest in descriptor[b"parts"].values():
                data = objects[str(base / (digest.decode("ascii") + ".mjb"))]
                assert hashlib.sha256(data).hexdigest().encode() == digest
                pieces.append(data)
            result = b"".join(pieces)
            assert len(result) == descriptor[b"bytes"]
            return result

        try:
            index = codec[b"decode"](raw) if orphan else codec[b"decode"](get(codec[b"decode"](raw)))
            if orphan and index[b"slots"] is None:
                continue
            assert index[b"schema"] == 1
        except Exception as error:
            rows.append(dict(root=str(base), index=name, status="failed", error=str(error)))
            continue

        def read_slot(slot, record):
            packet = codec[b"decode"](get(record[b"snapshot"]))
            assert packet[b"identity"] == index[b"identity"]
            commands = []
            for graphic in record[b"graphics"].values():
                data = get(graphic)
                batch = codec[b"decode"](data) if data.startswith(b"MJS1\n") else data
                assert batch.startswith(b"batch\n")
                at = 6
                while at < len(batch):
                    end = batch.index(b"\n", at)
                    length = int(batch[at:end])
                    assert 0 < length <= len(batch) - end - 1
                    commands.append(batch[end + 1:end + 1 + length])
                    at = end + 1 + length
                assert at == len(batch)
            return dict(root=str(base), index=name, slot=slot.decode("ascii"),
                             time=(record[b"time"] or b"").decode("utf8", "replace"),
                             info=(record[b"info"] or b"").decode("utf8", "replace"),
                        packet=packet, commands=commands, lua=lua, codec=codec, status="passed")
        for slot, record in index[b"slots"].items():
            try:
                rows.append(read_slot(slot, record))
            except Exception as error:
                rows.append(dict(root=str(base), index=name, slot=slot.decode("ascii"),
                                 status="failed", error=str(error)))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--orphan-indexes", action="store_true")
    args = parser.parse_args()
    result = []
    for row in read_backup(args.backup, args.orphan_indexes):
        if row["status"] != "passed":
            result.append(row)
            continue
        packet = row["packet"]
        channels = []
        media = packet[b"state"][b"media"]
        specs = list(media[b"channels"].values())
        specs.extend(value for value in (media[b"music_stack"] or {}).values() if value)
        for spec in specs:
            channels.append({key: (spec[key.encode()] or b"").decode("utf8", "replace")
                             for key in ("id", "name", "resource", "file", "loop_name",
                                         "loop_resource", "loop_file")})
        result.append({key: row[key] for key in ("root", "index", "slot", "time", "info")} |
                      {"channels": channels, "graphics_commands": len(row["commands"]),
                       "scripts": len(packet[b"vm"][b"scripts"]), "status": "passed"})
    failures = sum(row["status"] != "passed" for row in result)
    report = dict(status="failed" if failures else "passed", timestamp=timestamp(), backup=str(args.backup.resolve()),
                  slots=result, slot_count=len(result), source_modified=False)
    write_json(args.report, report)
    print(json.dumps(dict(status=report["status"], slots=len(result), failures=failures,
                          report=str(args.report.resolve()))))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
