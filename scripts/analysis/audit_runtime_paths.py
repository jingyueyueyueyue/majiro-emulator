"""Conservative whole-corpus call graph and live provider coverage, with source locations."""
from collections import defaultdict
from lua_bridge import create_lua, setup_runtime
from port_common import ROOT, read_json, write_json


def run():
    lua = create_lua()
    _, vm, system, _ = setup_runtime(lua, False)
    catalogue = lua.execute((ROOT / "resources/scripts/catalogue.lua").read_text(encoding="utf-8"))
    scripts, nodes, targets, edges = {}, {}, defaultdict(list), defaultdict(set)
    excluded = ("kaoire.mjo", "kaoire_start.mjo", "face_pos_get.mjo", "matoテスト.mjo")
    for name, record in catalogue["scripts"].items():
        script = lua.execute((ROOT / "resources/scripts" / (record["id"] + ".lua")).read_text(encoding="utf-8"))
        scripts[name] = script
        starts = sorted({script["main"], *(f[2] for f in script["functions"].values())})
        for pos in starts:
            node = (name, pos)
            end = next((v for v in starts if v > pos), float("inf"))
            nodes[node] = [r for r in script["code"].values() if pos <= r[1] < end]
        for f in script["functions"].values():
            targets[int(f[1])].append((name, f[2]))
    for node, code in nodes.items():
        for row in code:
            op = vm["opcodes"][row[2]][1]
            if op in ("call", "callp", "ldc.i"):
                for target in targets.get(int(row[3]) % 2**32, ()):
                    edges[node].add(target)
    # Include every non-editor script entry and the GLOBAL initializer.
    roots = {(n, s["main"]) for n, s in scripts.items() if n not in excluded}
    roots.update(n for n in targets[2981858012] if n[0] not in excluded)
    reachable, pending = set(roots), list(roots)
    while pending:
        for target in edges[pending.pop()]:
            if target not in reachable:
                reachable.add(target)
                pending.append(target)
    missing = defaultdict(list)
    for node, code in nodes.items():
        for row in code:
            if vm["opcodes"][row[2]][1] not in ("syscall", "syscallp"):
                continue
            h = int(row[3]) % 2**32
            name = system["state"]["names"][h]
            if system["providers"][h] or (name and system["providers"][name]):
                continue
            missing[(h, name)].append({"script": node[0], "offset": row[1],
                                      "function_offset": node[1], "argc": row[4],
                                      "reachable_function": node in reachable,
                                      "editor": node[0] in excluded})
    report = {"status": "audited", "scripts": len(scripts), "functions": len(nodes),
              "reachable_functions": len(reachable),
              "scope": "conservative entry/call/callback graph; data-dependent branch reachability needs execution",
              "unsupported": [{"hash": "%08x" % h, "name": name, "sites": sites}
                              for (h, name), sites in sorted(missing.items())]}
    write_json(ROOT / "reports/runtime_path_audit.json", report)
    for row in report["unsupported"]:
        active = [s for s in row["sites"] if s["reachable_function"]]
        print(row["hash"], row["name"], "sites", len(row["sites"]),
              "reachable", [(s["script"], hex(s["offset"]), hex(s["function_offset"])) for s in active])
    return report


if __name__ == "__main__":
    run()
