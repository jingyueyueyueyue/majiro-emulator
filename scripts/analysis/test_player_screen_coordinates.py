"""All original screen ABIs against player Lua, without compiling pixels."""
import json
from pathlib import Path
import sys

from audit_player_semantics import registered_providers
from original_screen import ScreenOracle

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,write_json


def run():
    samples=json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf8"))["samples"]
    dispatch=json.loads((ROOT/"reports/syscall_dispatch.json").read_text(encoding="utf8"))["samples"]
    report={"status":"passed","timestamp":timestamp(),"samples":[]}
    for sample in samples:
        entries=next(row["entries"]for row in dispatch if row["exe_sha256"]==sample["exe_sha256"])
        oracle=ScreenOracle(sample,entries)
        profile={**oracle.profile(),"cursor_reset_sentinel":oracle.cursor_reset_sentinel}
        lua,_,_,system=registered_providers(overrides={"screen_controls":profile})
        screen=system["screen"];providers=system["providers"];vm=lua.table()
        cases=0
        for home in((0,0),(1,-2),(-19,23),(2147483647,-2147483648)):
            oracle.call_home(*home)
            providers["$grp_home"](vm,lua.table_from(home))
            for point in((0,0),(7,5),(-9,17),(2147483647,-2147483648)):
                expected=oracle.client_to_game(*point)
                assert list(screen["point"](*point))==expected,(sample["sample"],home,point)
                oracle.sample_cursor(*point)
                system["input"]["sample"](lua.table_from({"x":point[0],"y":point[1]}))
                actual=[providers[name](vm,lua.table())for name in("$curpos_x","$curpos_y")]
                assert actual==oracle.cursor(),(sample["sample"],home,point,actual,oracle.cursor())
                cases+=2
            if oracle.has_virtual_cursor:
                for point in((3,-2),(-10000,0),(9,12)):
                    oracle.virtual_cursor(*point)
                    providers[0xb84c9953](vm,lua.table_from(point))
                    actual=[providers[name](vm,lua.table())for name in("$curpos_x","$curpos_y")]
                    assert actual==oracle.cursor(),(sample["sample"],home,point,actual,oracle.cursor())
                    cases+=1
        report["samples"].append({"sample":sample["sample"],"exe_sha256":sample["exe_sha256"],
                                  "profile":profile,"cases":cases})
        print(sample["sample"],cases,"coordinate comparisons",flush=True)
    report["cases"]=sum(row["cases"]for row in report["samples"])
    folder=archive_dir("screen_coordinates")
    write_json(folder/"report.json",report)
    write_json(ROOT/"reports/player_screen_coordinate_tests.json",report)
    return report


if __name__=="__main__":run()
