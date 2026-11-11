"""Export editable display text with stable locations; import reviewed text into the port."""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
import re
import sys

from port_common import ROOT, archive_dir, atomic_bytes, read_json, sha256, timestamp, write_json

TEXT_PATH = ROOT / "localization/proofread_full.txt"
MAP_PATH = ROOT / "localization/proofread_map.json"
OVERRIDE_PATH = ROOT / "localization/proofread_overrides.json"
PREFIX = "译文："
READONLY = "只读："
MARKER = re.compile(r"@@ ([A-Za-z0-9_]+)$")
PLACEHOLDER = re.compile(r"%(?:[-+ #0]*\d*(?:\.\d+)?[diuoxXfFeEgGcs]|%)|<<[^<>]+>>")


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def escape(text):
    # JSON escaping without quotation marks keeps each entry on one physical line.
    return json.dumps(text, ensure_ascii=False)[1:-1]


def unescape(text):
    result = []
    cursor = 0
    while cursor < len(text):
        if text[cursor] != "\\":
            result.append(text[cursor])
            cursor += 1
            continue
        match = re.match(r'\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4})', text[cursor:])
        if not match:
            raise ValueError("invalid text escape; use \\\\ for a literal backslash")
        result.append(json.loads('"' + match[0] + '"'))
        cursor += len(match[0])
    return "".join(result)


def display(text, characters, labels):
    return labels.get(text, "".join(characters.get(char, char) for char in text))


def empty_overrides():
    return {"schema": 1, "entries": {}}


class Overrides:
    """Keep human edits independent of generated Lua and the read-only source ZIP."""
    def __init__(self, document=None):
        self.document = read_json(OVERRIDE_PATH) if document is None and OVERRIDE_PATH.exists() else (
            document if document is not None else empty_overrides())
        if self.document.get("schema") != 1 or not isinstance(self.document.get("entries"), dict):
            raise ValueError("unsupported proofreading overrides")
        self.entries = self.document["entries"]
        self.used = set()
        self.characters = read_json(ROOT / "localization/character_map.json")
        from majiro_patch import Patch
        self.patch = Patch()
        self.tunnel = {char: list(pair) for pair, char in self.patch.tunnel.items()}
        inverse = {}
        for old, new in self.characters.items():
            inverse.setdefault(new, old)
        self.encoding = {}
        needed = sorted(set("".join(item["value"] for item in self.entries.values())))
        unused_pairs = (list(pair) for pair in self._pairs() if pair not in self.patch.tunnel)
        aliases = (chr(n) for n in range(0xF0000, 0xFFFFE))
        for char in needed:
            if self.characters.get(char, char) == char and self._encodable(char):
                self.encoding[char] = char
            elif char in inverse and self._encodable(inverse[char]):
                self.encoding[char] = inverse[char]
            else:
                # New Chinese characters receive a display alias and a two-byte width.
                # Invalid CP932 trail bytes cannot collide with ordinary source characters.
                alias = next(aliases)
                self.encoding[char] = alias
                self.characters[alias] = char
                self.tunnel[alias] = next(unused_pairs)

    @staticmethod
    def _pairs():
        for high in (*range(0x81, 0xA0), *range(0xE0, 0xFD)):
            for low in range(1, 0x40):
                if low not in (9, 10, 13, 32, 44):
                    yield bytes((high, low))

    def _encodable(self, char):
        if char in self.tunnel:
            return True
        try:
            char.encode("cp932")
            return True
        except UnicodeEncodeError:
            return False

    def encode(self, value):
        return "".join(self.encoding[char] for char in value)

    def replacement(self, ident, source):
        item = self.entries.get(ident)
        if item is None:
            return None
        if item["source"] != source:
            raise ValueError("proofreading source changed: " + ident)
        self.used.add(ident)
        return self.encode(item["value"])

    def apply_script(self, script, data):
        repairs = {item["offset"]: len(item["push_values"]) for item in script.stack_repairs}
        cursor = 0
        for instruction in script.instructions:
            cursor += repairs.get(instruction.offset, 0)
            row = data["code"][cursor]
            if row[1] != instruction.opcode:
                raise ValueError("proofreading runtime instruction alignment changed")
            for index, field in enumerate(instruction.encoding):
                if field != "s" or instruction.name not in ("text", "ldstr"):
                    continue
                ident = script_id(script.id, instruction.offset, index)
                source = {"kind": "script", "name": script.name, "sha256": script.sha256,
                          "offset": instruction.offset, "operand": index, "opcode": instruction.name,
                          "raw": instruction.operands[index]}
                replacement = self.replacement(ident, source)
                if replacement is not None:
                    row[index + 2] = replacement
            cursor += 1
        return data

    def apply_config(self, name, source_sha, text):
        spans = config_spans(text)
        for index, (start, stop, value) in reversed(list(enumerate(spans))):
            ident = config_id(name, index)
            source = {"kind": "config", "name": name, "sha256": source_sha,
                      "token": index, "raw": value}
            replacement = self.replacement(ident, source)
            if replacement is not None:
                quoted = text[start:stop].startswith('"')
                text = text[:start] + ('"' + replacement + '"' if quoted else replacement) + text[stop:]
        return text

    def finish(self):
        missing = set(self.entries) - self.used
        if missing:
            raise ValueError("proofreading targets were not consumed: " + ", ".join(sorted(missing)[:5]))


def script_id(script, offset, operand):
    return f"S_{script[2:]}_{offset:08x}_{operand}"


def config_id(name, token):
    return f"C_{digest(name)[:16]}_{token:05d}"


def config_spans(text):
    """Match config_tokens exactly while retaining ranges for line-reader synchronization."""
    result = []
    offset = 0
    for line in text.splitlines(keepends=True):
        quoted = False
        stop = len(line)
        for index, char in enumerate(line):
            if char == '"':
                quoted = not quoted
            if not quoted and line[index:index + 2] == "//":
                stop = index
                break
        content = line[:stop].strip()
        if not content:
            offset += len(line)
            continue
        base = offset + len(line[:stop]) - len(line[:stop].lstrip())
        quoted = False
        start = 0
        for index, char in enumerate(content + ","):
            if char == '"':
                quoted = not quoted
            if char == "," and not quoted:
                part = content[start:index]
                left = base + start + len(part) - len(part.lstrip())
                right = max(left, base + index - (len(part) - len(part.rstrip())))
                value = text[left:right]
                if value.startswith('"') and value.endswith('"') and len(value) >= 2:
                    value = value[1:-1]
                result.append((left, right, value))
                start = index + 1
        offset += len(line)
    from majiro_config import config_tokens
    if [item[2] for item in result] != config_tokens(text):
        raise ValueError("configuration token/span disagreement")
    return result


def technical_literal(text, name, resource_names):
    clean = text.lstrip("!")
    if not text or text.isascii():
        return True
    if clean.casefold() in resource_names or Path(clean).stem.casefold() in resource_names:
        return True
    if re.search(r"\.(?:txt|mjo|cfg|rct|rc8|ogg|wav|wmv)$", clean, re.I):
        return True
    if re.search(r"[/\\_*<>]|^[@#$%]|_spr$|ＭＳ |MS UI ", text):
        return True
    if not any(char.isalnum() for char in text):
        return True
    if name in ("kaoire.mjo", "transit.mjo", "transit_top.mjo", "yazlib.mjo"):
        return True
    if text in ("文中ハート", "submenu_マーク", "裸マスク", "思い出し枠", "ＣＧモード数字",
                "★ブランドロゴ", "ドラッグスタート", "シナリオセレクト"):
        return True
    return False


def collect(overrides=None):
    from majiro_arc import Catalogue
    from majiro_mjo import Script, opcode_table
    catalogue = Catalogue(ROOT)
    overrides = overrides or Overrides()
    characters = read_json(ROOT / "localization/character_map.json")
    labels = read_json(ROOT / "localization/ui_display.json")
    resources = {v.casefold() for v in catalogue.by_name}
    resources.update(Path(v).stem.casefold() for v in catalogue.by_name)
    entries, sources, coverage = [], [], Counter()
    ops = opcode_table()
    members = sorted((items[0] for items in catalogue.by_name.values()
                      if items[0].extension in (".mjo", ".cfg")),
                     key=lambda member: (member.extension != ".mjo", member.name.casefold()))
    for member in members:
        translated = member.identity.startswith("translation:")
        decode = catalogue.patch.decode if translated else lambda data: data.decode("cp932")
        raw = member.read()
        source_sha = hashlib.sha256(raw).hexdigest()
        sources.append({"name": member.name, "sha256": source_sha})
        if member.extension == ".mjo":
            coverage["scripts"] += 1
            script = Script(member.name, raw, ops, string_decoder=decode,
                            address_repairs=catalogue.patch.repairs(member.name, source_sha) if translated else None,
                            stack_repairs=catalogue.patch.preludes(member.name, source_sha) if translated else None)
            has_body = any(instruction.name == "text" for instruction in script.instructions)
            for instruction in script.instructions:
                if instruction.name not in ("text", "ldstr"):
                    continue
                for index, field in enumerate(instruction.encoding):
                    if field != "s":
                        continue
                    value = instruction.operands[index]
                    coverage["all_" + instruction.name] += 1
                    # Preserve technical literals in the mapping, outside the human TXT.
                    protected = instruction.name == "ldstr" and (
                        not has_body or technical_literal(value, member.name, resources))
                    ident = script_id(script.id, instruction.offset, index)
                    source = {"kind": "script", "name": member.name, "sha256": source_sha,
                              "offset": instruction.offset, "operand": index,
                              "opcode": instruction.name, "raw": value}
                    baseline = display(value, characters, labels)
                    current = overrides.entries.get(ident)
                    if current and current["source"] != source:
                        raise ValueError("proofreading source changed: " + ident)
                    entries.append({"id": ident, "source": source, "editable": not protected,
                                    "category": "正文" if instruction.name == "text" else "选项／界面字符串",
                                    "text": current["value"] if current else baseline})
        else:
            coverage["configs"] += 1
            for index, (_, _, value) in enumerate(config_spans(decode(raw))):
                coverage["all_config_tokens"] += 1
                source = {"kind": "config", "name": member.name, "sha256": source_sha,
                          "token": index, "raw": value}
                ident = config_id(member.name, index)
                current = overrides.entries.get(ident)
                if current and current["source"] != source:
                    raise ValueError("proofreading source changed: " + ident)
                entries.append({"id": ident, "source": source, "editable": False,
                                "category": "配置中的曲名／人物名",
                                "text": current["value"] if current else display(value, characters, labels)})
    for filename in ("ui_zh.json", "ui_display.json", "ui_images.json"):
        document = read_json(ROOT / "localization" / filename)
        values = []
        if filename == "ui_images.json":
            for group_index, group in enumerate(document["groups"]):
                for label_index, label in enumerate(group["labels"]):
                    path = ["groups", group_index, "labels", label_index, "text" if isinstance(label, dict) else 1]
                    values.append((path, label["text"] if isinstance(label, dict) else label[1],
                                   "、".join(group["images"])))
        else:
            values = [([key], value, key) for key, value in document.items() if isinstance(value, str)]
        for path, value, note in values:
            ident = "U_" + digest([filename, path])[:20]
            entries.append({"id": ident, "source": {"kind": "ui", "name": filename, "path": path},
                            "editable": False, "category": "菜单图片文案" if filename == "ui_images.json" else "界面文案",
                            "note": note, "text": value})
    coverage.update({"exported_entries": len(entries),
                     "txt_entries": sum(entry["editable"] for entry in entries),
                     "editable_entries": sum(entry["editable"] for entry in entries),
                     "reference_entries": sum(not entry["editable"] for entry in entries)})
    return {"schema": 1, "sources": sources, "character_map_sha256": sha256(ROOT / "localization/character_map.json"),
            "coverage": dict(coverage), "entries": entries}


def seal(document):
    result = copy.deepcopy(document)
    result.pop("mapping_id", None)
    result["mapping_id"] = digest(result)
    return result


def render(document, pending=None):
    pending = pending or {}
    lines = [
        "# ルリのかさね 移植版｜完整剧情校对文本",
        "# 日期：" + timestamp()[:10],
        "# MAP " + document["mapping_id"],
        "# 只修改“译文：”后面的文字；保留 @@ 编号，不增删条目。",
        "# 每条保持一行，可留空；不要把自动换行改成实际回车。",
        "# \\n 表示原有换行，\\\\ 表示反斜杠，\\t 表示制表符。保留占位符和控制字符。",
        "# 文本为游戏实际显示用字；相同句子的每个出现位置均独立编号，不合并重复。",
        "# 仅含对白、旁白和剧情字符串；正文含全部路线、附加正文、旧稿和测试剧情。",
        "# 菜单、按钮、提示、曲名、配置及技术数据仅保留在 JSON 映射中。",
        "# 共 " + str(document["coverage"]["txt_entries"]) + " 条。",
        "",
    ]
    last = None
    for entry in document["entries"]:
        if not entry["editable"]:
            continue
        source = entry["source"]
        if source["name"] != last:
            lines.extend(["", "# 文件：" + source["name"]])
            last = source["name"]
        if source["kind"] == "script":
            note = f'{entry["category"]} | 0x{source["offset"]:08X}'
        elif source["kind"] == "config":
            note = f'{entry["category"]} | token {source["token"]}'
        else:
            note = entry["category"] + " | " + entry.get("note", "")
        lines.extend(["@@ " + entry["id"], "# " + note,
                      PREFIX + escape(pending.get(entry["id"], entry["text"])), ""])
    return ("\r\n".join(lines) + "\r\n").encode("utf-8-sig")


def parse_document(text, mapping):
    if seal(mapping)["mapping_id"] != mapping.get("mapping_id"):
        raise ValueError("mapping integrity mismatch")
    expected = {item["id"]: item for item in mapping["entries"] if item["editable"]}
    found = {}
    pending = None
    map_ids = []
    for number, line in enumerate(text.lstrip("\ufeff").splitlines(), 1):
        if line.startswith("# MAP "):
            map_ids.append(line[6:])
        if line.startswith("@@"):
            match = MARKER.fullmatch(line)
            if not match or pending is not None:
                raise ValueError(f"invalid/missing text entry near line {number}")
            pending = match[1]
            if pending not in expected or pending in found:
                raise ValueError(f"unknown/duplicate ID at line {number}: {pending}")
        elif line.startswith((PREFIX, READONLY)):
            if pending is None:
                raise ValueError(f"text without ID at line {number}")
            prefix = PREFIX if expected[pending]["editable"] else READONLY
            if not line.startswith(prefix):
                raise ValueError("entry editability changed: " + pending)
            found[pending] = unescape(line[len(prefix):])
            pending = None
        elif line and not line.startswith("#"):
            raise ValueError(f"unrecognized line {number}; preserve one physical line per entry")
    if map_ids != [mapping["mapping_id"]]:
        raise ValueError("TXT belongs to a different mapping")
    if pending is not None or set(found) != set(expected):
        raise ValueError(f"missing entries: expected {len(expected)}, got {len(found)}")
    return found


def validate_edit(entry, old, new):
    if not entry["editable"] and new != old:
        raise ValueError("read-only reference changed: " + entry["id"])
    if Counter(PLACEHOLDER.findall(old)) != Counter(PLACEHOLDER.findall(new)):
        raise ValueError("format placeholders changed: " + entry["id"])
    if Counter(char for char in old if ord(char) < 32) != Counter(char for char in new if ord(char) < 32):
        raise ValueError("control characters changed: " + entry["id"])
    if any(0xD800 <= ord(char) <= 0xDFFF or 0xF0000 <= ord(char) <= 0xFFFFD for char in new):
        raise ValueError("reserved or invalid Unicode character: " + entry["id"])
    if entry["source"]["kind"] == "config" and new != old:
        if any(char in new for char in ',"\r\n\t') or "//" in new or new.strip() != new:
            raise ValueError("configuration text contains a delimiter: " + entry["id"])


def plan_import(mapping, values, current=None):
    current = current or collect()
    if mapping["sources"] != current["sources"] or mapping["character_map_sha256"] != current["character_map_sha256"]:
        raise ValueError("source scripts/configs/character map changed since export")
    by_id = {entry["id"]: entry for entry in current["entries"]}
    if set(by_id) != {entry["id"] for entry in mapping["entries"]}:
        raise ValueError("proofreading target set changed")
    overrides = copy.deepcopy(read_json(OVERRIDE_PATH) if OVERRIDE_PATH.exists() else empty_overrides())
    ui = {}
    changes = []
    characters = read_json(ROOT / "localization/character_map.json")
    labels = read_json(ROOT / "localization/ui_display.json")
    for entry in mapping["entries"]:
        if not entry["editable"]:
            continue
        ident = entry["id"]
        before, after = entry["text"], values[ident]
        validate_edit(entry, before, after)
        live = by_id[ident]
        if live["source"] != entry["source"]:
            raise ValueError("mapping target changed: " + ident)
        if before == after or live["text"] == after:
            continue
        if live["text"] != before:
            raise ValueError("conflicting edit since export: " + ident)
        source = entry["source"]
        if source["kind"] == "ui":
            filename = source["name"]
            if filename not in ui:
                ui[filename] = read_json(ROOT / "localization" / filename)
            parent = ui[filename]
            for key in source["path"][:-1]:
                parent = parent[key]
            parent[source["path"][-1]] = after
        else:
            baseline = display(source["raw"], characters, labels)
            if after == baseline:
                overrides["entries"].pop(ident, None)
            else:
                overrides["entries"][ident] = {"source": source, "value": after}
        changes.append(ident)
    # Constructing the encoder now catches exhausted aliases before any file is written.
    Overrides(overrides)
    return overrides, ui, changes


def export(force=False, text_path=TEXT_PATH, map_path=MAP_PATH):
    mapping = seal(collect())
    pending = {}
    if text_path.exists() or map_path.exists():
        if not force:
            raise ValueError("export exists; use --force to archive it before replacement")
        if text_path.exists() and map_path.exists():
            previous = read_json(map_path)
            previous_values = parse_document(text_path.read_text(encoding="utf-8-sig"), previous)
            new_entries = {entry["id"]: entry for entry in mapping["entries"] if entry["editable"]}
            for entry in previous["entries"]:
                ident = entry["id"]
                if not entry["editable"] or ident not in new_entries:
                    continue
                value = previous_values[ident]
                if value == entry["text"]:
                    continue
                live = new_entries[ident]
                if entry["source"] != live["source"] or live["text"] not in (entry["text"], value):
                    raise ValueError("conflicting pending review edit: " + ident)
                validate_edit(entry, entry["text"], value)
                pending[ident] = value
        evidence = archive_dir("proofread_export")
        for path in (text_path, map_path):
            if path.exists():
                atomic_bytes(evidence / (timestamp()[:10].replace("-", "") + "_" + path.name), path.read_bytes())
    write_json(map_path, mapping)
    atomic_bytes(text_path, render(mapping, pending))
    return {"status": "passed", **mapping["coverage"], "mapping_id": mapping["mapping_id"],
            "preserved_pending_edits": len(pending),
            "txt": str(text_path.relative_to(ROOT)), "map": str(map_path.relative_to(ROOT))}


def import_text(text_path=TEXT_PATH, map_path=MAP_PATH, dry_run=False):
    mapping = read_json(map_path)
    values = parse_document(text_path.read_text(encoding="utf-8-sig"), mapping)
    overrides, ui, changes = plan_import(mapping, values)
    report = {"status": "passed", "entries_checked": len(values), "changed_entries": len(changes),
              "script_config_overrides": len(overrides["entries"]), "ui_files": sorted(ui),
              "dry_run": dry_run}
    if dry_run or not changes:
        return report
    evidence = archive_dir("proofread_import")
    updates = {OVERRIDE_PATH: overrides, **{ROOT / "localization" / name: value for name, value in ui.items()}}
    for path in updates:
        if path.exists():
            atomic_bytes(evidence / (timestamp()[:10].replace("-", "") + "_" + path.name), path.read_bytes())
    for path, value in updates.items():
        write_json(path, value)
    changelog = ROOT / "localization/CHANGELOG.md"
    log = changelog.read_text(encoding="utf-8")
    item = (f'- {timestamp()[:10]}：通过固定编号映射导入校对文本 {len(changes)} 条；'
            f'正式脚本／配置覆盖共 {len(overrides["entries"])} 条，界面文件 {len(ui)} 份。'
            '校对覆盖已接入主构建，原 ZIP 与 ARC 保持只读。\n')
    title, _, rest = log.partition("\n")
    atomic_bytes(changelog, (title + "\n\n" + item + rest.lstrip("\n")).encode("utf-8"))
    # Rebuild the actual formal data and shared Windows project immediately.
    sys.path.insert(0, str(ROOT / "scripts/build"))
    from prepare_sources import prepare
    from build_localized_ui import run as build_ui
    from build_windows import build
    prepare()
    if "ui_images.json" in ui:
        build_ui()
    build()
    report["archive"] = evidence.relative_to(ROOT).as_posix()
    write_json(ROOT / "reports/proofread_import.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    output = sub.add_parser("export")
    output.add_argument("--force", action="store_true")
    incoming = sub.add_parser("import")
    incoming.add_argument("--text", type=Path, default=TEXT_PATH)
    incoming.add_argument("--map", type=Path, default=MAP_PATH)
    incoming.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.command == "export":
        result = export(args.force)
        write_json(ROOT / "reports/proofread_export.json", result)
    else:
        result = import_text(args.text.resolve(), args.map.resolve(), args.dry_run)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
