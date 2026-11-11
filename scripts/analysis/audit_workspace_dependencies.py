"""Verify runtime and pinned game inputs resolve entirely within this workspace."""
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT, read_json, config, write_json, sha256
from majiro_patch import Patch


def inside(path):
    resolved=path.resolve(strict=True)
    assert resolved.is_relative_to(ROOT.resolve()), ("external game dependency",str(path),str(resolved))
    return resolved


def run():
    settings=config()
    files=[ROOT/settings["windows_engine"],ROOT/settings["font_source"]]
    project=ROOT/"projects/windows"
    for name in ("runtime","scripts","native","image","audio","movie","font","system"):
        inside(project/name)
    for name in ("Ruri_Artemis.exe","font/SourceHanSansSC-Regular.otf"):
        files.append(project/name)
    for kind in ("image","audio"):
        for asset in read_json(ROOT/f"resources/{kind}_catalogue.json").values():
            files.append(ROOT/"resources"/asset["file"])
    for record in read_json(ROOT/"config/source.lock.json")["files"]:
        files.append(ROOT/record["path"])
    patch=Patch()
    for member in patch.members:
        assert member.extracted is not None, "game build must use workspace-local translation input"
        files.append(member.extracted)
        member.read()  # The original member hashes remain authoritative.
    for path in files:
        inside(path)
    assert os.path.samefile(ROOT/settings["font_source"],project/"font/SourceHanSansSC-Regular.otf")
    report={"status":"passed","verified_files":len(files),"translation_members":len(patch.members),
            "external_runtime_dependencies":0,"external_game_build_inputs":0,
            "scope":"game runtime/assets/source patch; Python, MSVC and Windows SDK remain installed tools",
            "engine":settings["windows_engine"],"font":settings["font_source"]}
    write_json(ROOT/"reports/workspace_dependencies.json",report)
    print("workspace dependencies:",len(files),"files; external game inputs: 0")


if __name__=="__main__":
    run()
