"""Replay actual reported integer-only callbacks with fractional SDL coordinates."""
from pathlib import Path
import sys
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/"convert"),
              str(Path(__file__).resolve().parents[1]/"build")]
from audit_startup_resources import Startup
from deploy_player_android import payload
from port_common import ROOT,archive_dir,read_json,write_json,timestamp


def main():
    folder=archive_dir("touch_callback_coordinates")
    files,roots=payload(folder);samples=read_json(ROOT/"config/player_samples.json")["games"]
    rows=[]
    for ident,script,offset in(("SWITCH","buttonmenu.mjo",0xfe3),("White","console.mjo",0x7b72)):
        game=next(g for g in samples if g["id"]==ident)
        prefix=next(r["directory"]for r in roots if r["id"]==ident)+"/"
        source={target[len(prefix):]:path for path,target in files if target.startswith(prefix)}
        session=Startup(game,source,"original",1500000,35);result=session.run();assert not result["error"],result
        session.system["poll"](session.vm,session.lua.table_from({"x":511.75,"y":287.625,"buttons":0}))
        position=session.system["state"]["input"]
        assert position["x"]%1==0 and position["y"]%1==0
        loaded=session.vm["load"](session.vm,script)
        arguments=session.lua.table_from([position["x"],position["y"]])
        session.vm["enter"](session.vm,loaded,offset,arguments,False)
        # Original callback starts with line + strict argcheck. Run these
        # instructions without touching its side effects or bypassing checks.
        session.vm["step"](session.vm);session.vm["step"](session.vm)
        rows.append({"game":ident,"script":script,"offset":offset,"status":"passed",
                     "raw_sdl_coordinates":[511.75,287.625],
                     "script_coordinates":[position["x"],position["y"]]})
    report={"status":"passed","timestamp":timestamp(),"cases":rows,"device_accessed":False,
            "strict_argcheck_retained":True,"evidence":str(folder.relative_to(ROOT))}
    write_json(ROOT/"reports/touch_callback_coordinates_tests.json",report);write_json(folder/"report.json",report)
    print("Fractional touch coordinates:",len(rows),"reported original callbacks passed strict integer argcheck")
    return 0


if __name__=="__main__":raise SystemExit(main())
