"""Exercise full proofreading round trips, safe rejection and actual Lua text projection."""
from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, lua_value, read_json, write_json
from proofread_text import (MAP_PATH, TEXT_PATH, Overrides, collect, config_spans, display,
                            empty_overrides, escape, parse_document, plan_import,
                            render, seal, unescape, validate_edit)
from lua_bridge import create_lua


class ProofreadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Fixtures are generated in memory; the user's working TXT may contain
        # unfinished edits and is never overwritten or required to match an old export.
        cls.current = collect()
        cls.mapping = seal(cls.current)
        cls.text = render(cls.mapping).decode("utf-8-sig").replace("\r\n", "\n")
        cls.values = parse_document(cls.text, cls.mapping)

    def test_full_export_has_every_text_string_and_config_token(self):
        counts = self.mapping["coverage"]
        self.assertEqual(counts["all_text"], 33742)
        self.assertEqual(counts["scripts"], 121)
        self.assertEqual(len(self.values), counts["editable_entries"])
        self.assertNotIn("只读：", self.text)
        self.assertEqual(sum(e["source"]["kind"] == "script" for e in self.mapping["entries"]),
                         counts["all_text"] + counts["all_ldstr"])
        self.assertEqual(sum(e["source"]["kind"] == "config" for e in self.mapping["entries"]),
                         counts["all_config_tokens"])
        self.assertEqual(render(self.mapping).decode("utf-8-sig").replace("\r\n", "\n"), self.text)
        _, _, changes = plan_import(self.mapping, self.values, self.current)
        self.assertEqual(changes, [])

    def test_unicode_quotes_and_backslashes_round_trip(self):
        for value in ('中文 "引号"「括号」', 'a\\b\\n\t\r\n\x01', "", "龘𠮷", '""\\'):
            self.assertEqual(unescape(escape(value)), value)
        self.assertEqual(unescape('普通"引号"'), '普通"引号"')
        with self.assertRaises(ValueError):
            unescape(r"\invalid")

    def test_missing_duplicate_and_unknown_ids_fail(self):
        first = next(e["id"] for e in self.mapping["entries"] if e["editable"])
        marker = "@@ " + first
        for broken in (self.text.replace(marker, "@@ UNKNOWN", 1),
                       self.text + "\n" + marker + "\n译文：重复\n",
                       self.text.replace(marker, "# deleted", 1)):
            with self.assertRaises(ValueError):
                parse_document(broken, self.mapping)

    def test_wrong_or_modified_mapping_fails(self):
        with self.assertRaises(ValueError):
            parse_document(self.text.replace("# MAP ", "# MAP WRONG", 1), self.mapping)
        modified = copy.deepcopy(self.mapping)
        modified["entries"][0]["source"]["offset"] += 1
        with self.assertRaisesRegex(ValueError, "integrity"):
            parse_document(self.text, modified)

    def test_readonly_and_placeholders_fail(self):
        entry = next(e for e in self.mapping["entries"] if not e["editable"])
        with self.assertRaisesRegex(ValueError, "read-only"):
            validate_edit(entry, entry["text"], entry["text"] + "修改")
        entry = {"id": "fixture", "editable": True, "source": {"kind": "script"}}
        with self.assertRaisesRegex(ValueError, "placeholders"):
            validate_edit(entry, "数值 %d", "数值")
        with self.assertRaisesRegex(ValueError, "control"):
            validate_edit(entry, "两\n行", "一行")
        with self.assertRaisesRegex(ValueError, "reserved"):
            validate_edit(entry, "文本", "\U000f0000")

    def test_conflicts_fail_and_old_unchanged_entries_preserve_current(self):
        entry = next(e for e in self.mapping["entries"] if e["editable"])
        current = copy.deepcopy(self.current)
        live = next(e for e in current["entries"] if e["id"] == entry["id"])
        live["text"] = "其他版本的校对"
        values = dict(self.values)
        values[entry["id"]] = "本次校对"
        with self.assertRaisesRegex(ValueError, "conflicting"):
            plan_import(self.mapping, values, current)
        _, _, changes = plan_import(self.mapping, self.values, current)
        self.assertEqual(changes, [])
        values[entry["id"]] = live["text"]
        _, _, changes = plan_import(self.mapping, values, current)
        self.assertEqual(changes, [])

    def test_changed_source_is_rejected(self):
        current = copy.deepcopy(self.current)
        current["sources"][0]["sha256"] = "changed"
        with self.assertRaisesRegex(ValueError, "source scripts"):
            plan_import(self.mapping, self.values, current)

    def test_script_choice_and_new_hanzi_apply_at_exact_locations(self):
        from majiro_arc import Catalogue
        from majiro_mjo import Script
        catalogue = Catalogue(ROOT)
        entries = [next(e for e in self.mapping["entries"]
                        if e["editable"] and e["source"]["kind"] == "script"
                        and e["source"]["opcode"] == kind) for kind in ("text", "ldstr")]
        values = dict(self.values)
        for index, entry in enumerate(entries):
            values[entry["id"]] = f'校对测试{index}："龘𠮷"。'
        document, _, changes = plan_import(self.mapping, values, self.current)
        self.assertEqual(set(changes), {e["id"] for e in entries})
        # This test applies two selected source locations. Production now has
        # thousands of other formal overrides, which belong to the full
        # preparation/roundtrip checks and must not leak into this fixture.
        projection = Overrides({"schema":document["schema"],"entries":{
            entry["id"]:document["entries"][entry["id"]]for entry in entries}})
        lua = create_lua()
        localization = lua.execute((ROOT / "runtime/majiro_localization.lua").read_text(encoding="utf-8"))
        data = lua.execute("return " + lua_value({"characters": projection.characters,
                                                 "tunnel": projection.tunnel, "labels": {}}))
        localized = localization["new"](data, lambda text: text.encode("cp932"))
        for entry in entries:
            source = entry["source"]
            member = catalogue.get(source["name"])
            script = Script(member.name, member.read(), string_decoder=catalogue.patch.decode,
                            address_repairs=catalogue.patch.repairs(member.name, member.sha256),
                            stack_repairs=catalogue.patch.preludes(member.name, member.sha256))
            before = script.runtime_data()
            after = projection.apply_script(script, copy.deepcopy(before))
            differences = [(a, b) for a, b in zip(before["code"], after["code"]) if a != b]
            self.assertTrue(differences)
            for original, updated in differences:
                self.assertEqual(original[:2], updated[:2])
                self.assertEqual(original[3:], updated[3:])
            row = next(row for row in after["code"] if row[0] == source["offset"])
            self.assertEqual(localized["display"](row[2]), values[entry["id"]])
            # The same encoded text also loads through the generated Lua representation.
            loaded = lua.execute("return " + lua_value(after))
            self.assertEqual(loaded["name"], script.name)
            self.assertEqual(localized["display"](projection.encode(values[entry["id"]])), values[entry["id"]])
            for char in projection.encode(values[entry["id"]]):
                byte_count = lua.eval("function(f,c)return #f(c)end")(localized["cp932"], char)
                self.assertIn(byte_count, (1, 2))
        projection.finish()

    def test_config_tokens_and_line_reader_stay_synchronized(self):
        from majiro_arc import Catalogue
        from majiro_config import config_tokens, line_remainders
        # Config targets remain supported by the projection but are excluded
        # from the story-only review. Exercise one in a separate in-memory fixture.
        fixture = copy.deepcopy(self.mapping)
        entry = next(e for e in fixture["entries"]
                     if e["source"]["name"] == "music.cfg" and e["source"].get("token") == 125)
        entry["editable"] = True
        values = {e["id"]: e["text"] for e in fixture["entries"] if e["editable"]}
        values[entry["id"]] = "校对曲名龘"
        document, _, changes = plan_import(fixture, values, self.current)
        self.assertEqual(changes, [entry["id"]])
        projection = Overrides({"schema":document["schema"],"entries":{
            entry["id"]:document["entries"][entry["id"]]}})
        member = Catalogue(ROOT).get(entry["source"]["name"])
        original = member.read().decode("cp932")
        updated = projection.apply_config(member.name, entry["source"]["sha256"], original)
        before, after = config_tokens(original), config_tokens(updated)
        self.assertEqual(len(before), len(after))
        self.assertEqual([i for i, (a, b) in enumerate(zip(before, after)) if a != b],
                         [entry["source"]["token"]])
        replacement = after[entry["source"]["token"]]
        self.assertTrue(any(replacement in item["text"] for item in line_remainders(updated).values()))
        projection.finish()
        for fixture in ('a,,"b,c", // comment\n', '"http://host", x\r\n', ' // 空\n a, b, \n'):
            self.assertEqual([item[2] for item in config_spans(fixture)], config_tokens(fixture))
        with self.assertRaisesRegex(ValueError, "delimiter"):
            validate_edit(entry, entry["text"], "错误,逗号")

    def test_story_txt_excludes_all_ui_and_config_content(self):
        self.assertNotIn("@@ U_", self.text)
        self.assertNotIn("@@ C_", self.text)
        body_scripts = {e["source"]["name"] for e in self.mapping["entries"]
                        if e["source"]["kind"] == "script" and e["source"]["opcode"] == "text"}
        for entry in self.mapping["entries"]:
            if entry["editable"]:
                self.assertEqual(entry["source"]["kind"], "script")
                self.assertIn(entry["source"]["name"], body_scripts)
            if entry["source"]["kind"] in ("config", "ui"):
                self.assertNotIn(entry["id"], self.values)

    def test_numeric_repair_anchor_is_not_overwritten(self):
        from types import SimpleNamespace
        source = {"kind": "script", "name": "fixture.mjo", "sha256": "fixture",
                  "offset": 12, "operand": 0, "opcode": "text", "raw": "original"}
        from proofread_text import script_id
        ident = script_id("m_fixture", 12, 0)
        projection = Overrides({"schema": 1, "entries": {ident: {"source": source, "value": "新文本"}}})
        instruction = SimpleNamespace(offset=12, opcode=0x840, encoding="s", name="text", operands=("original",))
        script = SimpleNamespace(name="fixture.mjo", sha256="fixture", id="m_fixture",
                                 stack_repairs=[{"offset": 12, "push_values": [7, 9]}], instructions=[instruction])
        data = {"code": [[12, 0x800, 7], [30, 0x800, 9], [31, 0x840, "original"]]}
        updated = projection.apply_script(script, copy.deepcopy(data))
        self.assertEqual(updated["code"][:2], data["code"][:2])
        self.assertEqual(updated["code"][2][:2], [31, 0x840])
        self.assertNotEqual(updated["code"][2][2], "original")
        projection.finish()


def run():
    result = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromTestCase(ProofreadTests))
    report = {"status": "passed" if result.wasSuccessful() else "failed",
              "tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors)}
    write_json(ROOT / "reports/proofread_tests.json", report)
    if not result.wasSuccessful():
        raise SystemExit(1)
    return report


if __name__ == "__main__":
    run()
