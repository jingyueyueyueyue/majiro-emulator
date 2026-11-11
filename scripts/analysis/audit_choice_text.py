"""Align every story string with original ARC operands and audit reviewed choices."""
from __future__ import annotations
import argparse
import difflib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, read_json, write_json, archive_dir, atomic_bytes
from majiro_arc import Catalogue
from majiro_mjo import Script, opcode_table
from proofread_text import parse_document, MAP_PATH, TEXT_PATH, escape, PREFIX


def collect():
    mapping = read_json(MAP_PATH)
    current = parse_document(TEXT_PATH.read_text(encoding="utf-8-sig"), mapping)
    catalogue, ops = Catalogue(ROOT), opcode_table()
    cache, result = {}, []
    for entry in mapping["entries"]:
        src = entry["source"]
        if not entry["editable"] or src.get("opcode") != "ldstr":
            continue
        name = src["name"]
        if name not in cache:
            members = catalogue.by_name[name]
            original = next(m for m in members if not m.identity.startswith("translation:"))
            patched = members[0]
            old = Script(name, original.read(), ops)
            new = Script(name, patched.read(), ops, string_decoder=catalogue.patch.decode,
                         address_repairs=catalogue.patch.repairs(name, patched.digest()))
            def filtered(script):
                return [i for i in script.instructions if i.name not in ("text", "proc", "line")
                        and not (i.name == "ctrl" and i.operands == ("n",))]
            def key(i):
                return i.name, tuple(repr(v) for e, v in zip(i.encoding, i.operands)
                                     if e not in ("s", "j", "c"))
            a, b = filtered(old), filtered(new)
            matcher = difflib.SequenceMatcher(None, list(map(key, a)), list(map(key, b)), autojunk=False)
            aligned = {}
            for tag, i, j, k, end in matcher.get_opcodes():
                if tag == "equal":
                    aligned.update({right.offset: left for left, right in zip(a[i:j], b[k:end])})
                else:
                    assert tag == "delete", ("non-text alignment differs", name, tag)
            cache[name] = old, new, aligned
        old, new, aligned = cache[name]
        original = aligned[src["offset"]]
        near = [i for i in old.instructions if original.offset < i.offset][:20]
        prior = [i.operands[0] for i in old.instructions
                 if i.name == "text" and i.offset < original.offset][-5:]
        result.append({"id": entry["id"], "script": name, "offset": src["offset"],
                       "original_offset": original.offset, "original": original.operands[0],
                       "translation": current[entry["id"]], "context": prior,
                       "following": [[i.name, list(i.operands)] for i in near[:8]]})
    return result


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--export", action="store_true")
    parser.add_argument("--apply-reviewed", action="store_true")
    args = parser.parse_args()
    entries = collect()
    path = ROOT / "localization/choice_review.json"
    if args.export:
        write_json(path, {"date": "2026-11-09", "status": "in_review", "entries": entries})
    elif args.apply_reviewed:
        changes = read_json(ROOT / "localization/choice_corrections.json")["by_original"]
        replacements = {}
        for entry in entries:
            calls = [v[1][0] for v in entry["following"] if v[0] in ("call", "callp")]
            kind = ("technical" if 2390765889 in calls else
                    "rubric" if 832109825 in calls else
                    "test_choice" if entry["script"].startswith("mato") else
                    "staff" if entry["script"] == "スタッフコメント.mjo" else "choice")
            before = entry["translation"]
            after = (entry["original"] if kind == "technical" else
                     changes.get(entry["original"], before) if kind == "choice" else before)
            entry.update(kind=kind, before=before, translation=after,
                         verdict="corrected" if before != after else "retained")
            if before != after:
                replacements[entry["id"]] = after
        raw = TEXT_PATH.read_bytes()
        evidence = archive_dir("choice_review")
        atomic_bytes(evidence / "20261109_proofread_before.txt", raw)
        lines = raw.decode("utf-8-sig").splitlines()
        ident = None
        for i, line in enumerate(lines):
            if line.startswith("@@ "):
                ident = line[3:]
            elif line.startswith(PREFIX) and ident in replacements:
                lines[i] = PREFIX + escape(replacements[ident])
        atomic_bytes(TEXT_PATH, ("\r\n".join(lines) + "\r\n").encode("utf-8-sig"))
        write_json(path, {"date": "2026-11-09", "status": "reviewed", "entries": entries,
                         "text_changes": len(replacements),
                         "source_alignment": "non-text opcode/operand alignment with original ARC"})
        print("reviewed text updates:", len(replacements))
    else:
        review = read_json(path)
        assert review["status"] == "reviewed"
        assert len(review["entries"]) == len(entries)
        for actual, approved in zip(entries, review["entries"]):
            for key in ("id", "script", "offset", "original_offset", "original", "translation"):
                assert actual[key] == approved[key], (key, actual["id"])
            assert approved["kind"] in ("choice", "technical", "rubric", "test_choice", "staff")
            assert approved["verdict"] in ("corrected", "retained")
        write_json(ROOT / "reports/choice_text_audit.json", {
            "status": "passed", "date": "2026-11-09", "entries": len(entries),
            "choices": sum(e["kind"] == "choice" for e in review["entries"]),
            "corrected": sum(e["verdict"] == "corrected" for e in review["entries"]),
            "review": "localization/choice_review.json"})
    print("choice string audit:", len(entries), "exported" if args.export else "passed")


if __name__ == "__main__":
    run()
