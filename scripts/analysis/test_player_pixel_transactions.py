"""Real independent native/portable page transactions and alpha lifetimes."""
from pathlib import Path
import sys

from native_pixels import Pixels
from test_screen_semantics import Portable

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,sha256,timestamp,write_json


def run():
    folder=archive_dir("pixel_transactions")
    native=Pixels();portable=Portable(folder)
    count=0
    def command(request,wanted="OK"):
        nonlocal count
        desktop=native(request)
        try:other=portable(request)[0]
        except ValueError as error:other=str(error)
        assert desktop==other,(request,desktop,other)
        assert desktop.startswith(wanted),(request,desktop,wanted)
        count+=1
        return desktop
    try:
        for request in("reset","create 1 2 1 0","fill 1 0 0 2 1 197121",
                       "alpha 1 2 0","fill 2 0 0 2 1 127","drawmode 1 3"):
            command(request)
        before=[command(f"pixel {page} {x} 0")for page in(1,2)for x in(0,1)]
        command("checkpoint_commit","ERR")
        command("checkpoint_rollback","ERR")
        command("checkpoint_begin")
        command("checkpoint_begin","ERR")
        command("fill 1 0 0 2 1 255")
        command("fill 2 0 0 2 1 255")
        command("release 1")
        command("pixel 2 0 0","ERR")
        command("reset_pages")
        command("create 1 2 1 0")
        command("create 3 1 1 0")
        command("checkpoint_rollback")
        after=[command(f"pixel {page} {x} 0")for page in(1,2)for x in(0,1)]
        assert before==after
        command("pixel 3 0 0","ERR")
        command("checkpoint_begin")
        command("fill 2 0 0 2 1 200")
        command("checkpoint_commit")
        assert command("pixel 1 0 0")=="OK 1 2 3 55"
        command("checkpoint_rollback","ERR")
        # Restore an externally owned, released-but-referenced mask.
        command("create 3 2 1 0 8")
        command("fill 3 0 0 2 1 63")
        command("antidata 1 3")
        command("release 3")
        command("checkpoint_begin")
        command("antidata 1 0")
        command("pixel 3 0 0","ERR")
        command("checkpoint_rollback")
        assert command("pixel 1 0 0")=="OK 1 2 3 192"
        assert command("pixel 3 0 0")=="OK 63 63 63 255"
        command("antidata 1 0")
        command("pixel 3 0 0","ERR")
    finally:
        portable.close()
    report={"status":"passed","timestamp":timestamp(),"command_comparisons":count,
            "scope":"deep page/mask rollback, reset/recreate handles, commit, nested/absent transactions, deferred-mask-release restoration",
            "native_source_sha256":sha256(ROOT/"player/native/majiro_pixels.cpp"),
            "portable_source_sha256":sha256(ROOT/"player/core/portable_pixels_wrapper.inc")}
    write_json(folder/"report.json",report)
    write_json(ROOT/"reports/player_pixel_transaction_tests.json",report)
    print(report)
    return report


if __name__=="__main__":run()
