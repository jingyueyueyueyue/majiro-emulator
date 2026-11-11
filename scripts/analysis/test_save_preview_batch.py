"""Check current/saved thumbnails against real composed scenes across the library."""
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor,as_completed
import json
import sys
import hashlib
from PIL import Image,ImageChops
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/"convert")]
from port_common import ROOT,archive_dir,read_json,write_json,timestamp
from desktop_pixel_session import PixelSession
from test_story_entry_regressions import run_story
from test_desktop_game_batch import files_for,snapshot,tick


def worker(game,folder):
    session=PixelSession(game,files_for(game),"original",Path(folder)/game["id"].replace(" ","_"))
    story=run_story(game,session.files,"original",12,60,session=session)
    row={"game":game["id"],"status":"running","story_status":story["status"]}
    if story["status"]!="passed":return row|{"status":"setup_boundary","reason":story.get("error")}
    state=session.system["state"];providers=session.system["providers"];vm=session.vm
    width,height=state["pages"][state["frontbuffer"]]["width"],state["pages"][state["frontbuffer"]]["height"]
    # A layer with a distinctive color detects "base page only" captures even
    # in story scenes which legitimately have a black background.
    overlay=providers["$page_create"](vm,session.lua.table_from([64,48]))
    providers["$grp_boxfill"](vm,session.lua.table_from([overlay,0,0,64,48,0x00ff00]))
    sprite=providers["$sprite_create"](vm,session.lua.table_from([overlay]))
    providers["$sprite_move"](vm,session.lua.table_from([sprite,width//2,height//2]))
    # frontbuffer -> preview uses the same provider as real loadsave.mjo.
    providers[0x9143dc9e](vm,session.lua.table_from([250,141]))
    thumbnail=state["current_thumbnail"];before=session.folder/"thumb_before.png"
    session.native("png "+str(thumbnail)+" "+str(before))
    snapshot(session,703)
    slot=state["save_catalog"]["slot_00703"]
    assert slot and slot["thumbnail"],"save slot did not preserve thumbnail"
    saved=session.local_path("save://"+slot["thumbnail"]);assert saved.is_file()
    destination=providers["$page_create"](vm,session.lua.table_from([250,141]))
    providers["$copy_thumbnail"](vm,session.lua.table_from([703,destination,0,0]))
    after=session.folder/"thumb_saved_copy.png"
    session.native("png "+str(destination)+" "+str(after))
    with Image.open(before)as expected,Image.open(after)as actual:
        assert expected.convert("RGB").tobytes()==actual.convert("RGB").tobytes()
        pixels=list(expected.convert("RGB").getdata())
        marker=sum(g>180 and r<40 and b<40 for r,g,b in pixels)
    row.update(status="passed",marker_green_pixels=marker,
               current_preview=str(before),saved_preview=str(after),
               composed_sprite_present=marker>0,
               saved_preview_bytes_equal=True,
               actual_native_pixels=True,player_saves_used=False)
    if marker==0:row.update(status="failed",error="thumbnail omitted visible sprite layer")
    return row


def main():
    folder=archive_dir("save_preview_batch")
    report={"status":"running","timestamp":timestamp(),"cases":[],"device_accessed":False,
            "evidence":str(folder.relative_to(ROOT))}
    with ProcessPoolExecutor(max_workers=4)as pool:
        jobs={pool.submit(worker,g,str(folder)):g["id"]for g in read_json(ROOT/"config/recovered_library.json")["games"]}
        for future in as_completed(jobs):
            try:row=future.result()
            except Exception as error:row={"game":jobs[future],"status":"failed","error":str(error)}
            report["cases"].append(row);print(json.dumps(row,ensure_ascii=True),flush=True)
            write_json(folder/"report.json",report);write_json(ROOT/"reports/save_preview_batch.json",report)
    report.update(status="complete",timestamp=timestamp())
    write_json(folder/"report.json",report);write_json(ROOT/"reports/save_preview_batch.json",report)
    return int(any(c["status"]=="failed"for c in report["cases"]))


if __name__=="__main__":raise SystemExit(main())
