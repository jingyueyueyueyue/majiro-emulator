"""Fast implementation backlog; no games, semantic suites or device calls."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import sys

from audit_player_semantics import registered_providers,source_identity

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,atomic_bytes,read_json,timestamp,write_json


def family(name):
    if not name:return"unnamed"
    for key in("movie","music","voice","sound","console","font","sprite","grp","file","dialog",
               "mouse","joy","clipbord"):
        if key in name:return key
    return"other"


def run():
    before=source_identity()
    lua,names,providers,system=registered_providers()
    games={row.get("id")or Path(row["directory"]).name:row for row in read_json(ROOT/"reports/majiro_samples.json")["games"]}
    dispatch=list(read_json(ROOT/"reports/syscall_dispatch.json")["samples"])
    supplemental=ROOT/"reports/new_sample_scan.json"
    if supplemental.exists():
        extra=read_json(supplemental)
        by_directory={row["directory"]:row for row in extra["games"]}
        for game in extra["games"]:
            games[game.get("id")or Path(game["directory"]).name]=game
        for row in extra.get("differences",[]):
            games[row["sample"]]=by_directory[row["directory"]]
        existing={row["exe_sha256"]for row in dispatch}
        dispatch.extend(row for row in extra["syscall_samples"]if row["exe_sha256"]not in existing)
    rows={}
    samples=[]
    for sample in dispatch:
        absent=[]
        for entry in sample["entries"]:
            key=entry["hash"];number=int(key,16);name=names[number]or entry["name"]
            provider=providers.get(number)or providers.get(name)
            row=rows.setdefault(key,{"hash":key,"name":name,"family":family(name),
                "registered":provider is not None,"implementation":provider,
                "samples":[],"script_used_samples":[]})
            row["samples"].append(sample["sample"])
            if key in games[sample["sample"]]["syscalls"]:
                row["script_used_samples"].append(sample["sample"])
            if not provider:absent.append(key)
        samples.append({"sample":sample["sample"],"recovered":len(sample["entries"]),"missing":absent})
    missing=[row for row in rows.values()if not row["registered"]]
    missing.sort(key=lambda row:(not bool(row["script_used_samples"]),row["family"],row["hash"]))
    counts=Counter(row["family"]for row in missing)
    report={"schema":1,"timestamp":timestamp(),"source_identity":before,
        "source_changed_during_inventory":before!=source_identity(),"recovered":len(rows),
        "registered":len(rows)-len(missing),"missing":len(missing),
        "script_used_missing":sum(bool(row["script_used_samples"])for row in missing),
        "missing_by_family":dict(counts),"missing_entries":missing,"samples":samples,
        "full_semantic_suites_executed":False,"device_execution":False,
        "scope":"provider registration and source locations only; host behavior/semantic acceptance are separate",
        "complete":False}
    write_json(ROOT/"reports/player_entry_progress.json",report)
    lines=["# Majiro 入口实现进度","",f"更新时间：{report['timestamp']}","",
        f"已恢复 {report['recovered']}，已注册 {report['registered']}，缺失 {report['missing']}；"
        f"其中样本脚本使用而仍缺失 {report['script_used_missing']}。","",
        "此清单只盘点入口与源位置，不把注册当作语义验证或完整宿主验收。",
        "按当前要求优先补齐入口，不在此工具触发全量测试。","",
        "| 哈希 | 名称 | 类别 | 脚本使用样本 |","| --- | --- | --- | --- |"]
    for row in missing:
        lines.append(f"| `{row['hash']}` | {row['name']or'尚未命名'} | {row['family']} | "
            f"{', '.join(row['script_used_samples'])or'无'} |")
    atomic_bytes(ROOT/"reports/player_entry_progress.md",("\n".join(lines)+"\n").encode("utf8"))
    print(f"entries: {report['registered']}/{report['recovered']} registered, "
        f"{report['missing']} missing, {report['script_used_missing']} script-used missing")
    return report


if __name__=="__main__":run()
