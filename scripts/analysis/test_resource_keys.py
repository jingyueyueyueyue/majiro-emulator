"""Native resource lookup agrees with the shared catalogue for fullwidth CG names."""
from lua_bridge import create_lua
from port_common import ROOT, read_json, write_json


def run():
    lua = create_lua()
    key = lua.execute((ROOT / "runtime/majiro_strings.lua").read_text(encoding="utf-8"))["resource_key"]
    cases = []
    for text in ("ＣＧモード数字", "ｃｇモード数字", "TITLE_BASE.RCT",
                 "IMAGE\\ＣＧモード数字.RCT", "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"):
        expected = text.casefold().replace("\\", "/")
        actual = key(text)
        assert actual == expected, (text, expected, actual)
        cases.append({"request": text, "key": actual})
    assets = read_json(ROOT / "resources/image_catalogue.json")
    assert all(key(name) == name.casefold().replace("\\", "/") for name in assets)
    lookup = {key(name): asset for name, asset in assets.items()}
    asset = lookup[key("ＣＧモード数字") + ".rct"]
    assert (ROOT / "resources" / asset["file"]).is_file()
    write_json(ROOT / "reports/resource_key_tests.json", {
        "status": "passed", "cases": cases, "actual_shared_image": asset["file"],
        "copies": 0, "catalogue_keys": len(assets),
        "scope": "fullwidth Latin case and separators against every existing image catalogue key"})
    print("resource keys:", len(cases), "passed, shared CG numeral image resolved")


if __name__ == "__main__":
    run()
