"""Batch original story media capture, replay arguments and context resets."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import unicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, timestamp, write_json
from native_function import NativeFunctions
from original_story_media import PROFILES, StoryMediaOracle, StoryResetOracle, StoryStartupOracle


def expected_capture(profile, spec):
    music = [b"bgm-intro.ogg".hex(), b"bgm-loop.ogg".hex()] if spec["music"] else ["", ""]
    channels = [["", ""] for _ in range(4)]
    active = bool(spec["flags"]) and spec["stream_present"] and bool(spec["running"])
    if profile["skip_bit30"] and spec["flags"] & 0x40000000:
        active = False
    index = spec["channel"]
    if active:
        channels[index] = [f"se{index+1}-intro.ogg".encode().hex(), f"se{index+1}-loop.ogg".encode().hex()]
    return {"music": music, "channels": channels,
            "integers": [0x10203040+i*0x101 for i in range(7)]}


def main():
    evidence = archive_dir("story_media_layout_tests")
    names = ("scripts/analysis/original_story_media.py", "scripts/analysis/test_story_media_layout.py",
             "scripts/analysis/original_story_save.py", "scripts/analysis/test_native_saves.py")
    before = {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in names}
    samples = json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf8"))["samples"]
    cases, profile_evidence = [], []
    for label, profile in PROFILES.items():
        sample = next(s for s in samples if s["sample"] == label)
        oracle = StoryMediaOracle(sample)
        baseline = None
        function_addresses = [profile["capture"]]
        if label == "narci_onetwo":
            function_addresses += [profile["restore"], 0x491650, 0x4900A0]
        else:
            function_addresses += [0x45EE00]
        disassembly = []
        for address in function_addresses:
            disassembly.append(f"function {address:08x}")
            disassembly += [f"{i.address:08x} {i.mnemonic:8s} {i.op_str}"
                            for i in oracle.functions.body(address)]
        file = evidence/("20261110_"+label+"_story_media_original.txt")
        file.write_text("\n".join(disassembly)+"\n", encoding="utf8")
        profile_evidence.append({"sample": label, "exe_sha256": sample["exe_sha256"],
                                 "layout": profile, "disassembly": str(file.relative_to(ROOT))})
        for music in (False, True):
            for channel in range(4):
                for flags in (0, 1, 2, 0x40000000, 0x40000001, 0x80000000, 0xFFFFFFFF):
                    for present, running, completion in ((False, 1, 0), (True, 0, 0),
                                                          (True, 1, 0), (True, 1, 1)):
                        spec = oracle.fixture(music, channel, flags, present, running, completion)
                        written = set()
                        header = profile["header"]
                        def record(uc, access, at, size, value, unused):
                            written.update(range(at-header, at-header+size))
                        hook = oracle.machine.hook_add(unicorn.UC_HOOK_MEM_WRITE, record,
                                                       begin=header, end=header+0xFFF)
                        try:
                            actual = oracle.capture()
                        finally:
                            oracle.machine.hook_del(hook)
                        expected = expected_capture(profile, spec)
                        assert actual == expected, (label, spec, actual, expected)
                        allowed = set()
                        for offset in profile["music"][:2]:
                            allowed.update(range(offset, offset+256))
                        for row in profile["channels"]:
                            allowed.update(range(row[0], row[0]+128))
                            allowed.update(range(row[1], row[1]+128))
                        for offset, _ in profile["integers"]:
                            allowed.update(range(offset, offset+4))
                        assert written <= allowed, "media capture wrote unknown header fields"
                        requests = None
                        if profile.get("restore_helpers"):
                            requests = oracle.restore()
                            assert requests[0] == {
                                "channel": 0, "first_bytes_hex": actual["music"][0],
                                "second_bytes_hex": actual["music"][1],
                                "request_flags": expected["integers"][1],
                                "preplay_gain_bits": expected["integers"][0]}
                            present_channels = [i for i, row in enumerate(actual["channels"]) if row[0]]
                            assert [row["channel"] for row in requests[1:]] == [i+1 for i in present_channels]
                            for row, index in zip(requests[1:], present_channels):
                                assert row["first_bytes_hex"] == actual["channels"][index][0]
                                assert row["second_bytes_hex"] == actual["channels"][index][1]
                                assert row["request_flags"] == 1
                                assert row["preplay_gain_bits"] == expected["integers"][3+index]
                        cases.append({"sample": label, "source": spec, "capture": actual,
                                      "replay_requests": requests, "status": "passed"})
        if label == "narci_onetwo":
            audio_defaults = StoryMediaOracle(sample).audio_reset_defaults()
            assert set(audio_defaults["gain_defaults"].values()) == {1000}
            assert audio_defaults["capture_after_reset"]["music"] == ["", ""]
            assert audio_defaults["capture_after_reset"]["channels"] == [["", ""] for _ in range(4)]
            assert audio_defaults["capture_after_reset"]["integers"] == [0x10203040+i*0x101 for i in range(7)]
            markers = StoryMediaOracle(sample).visual_reset_markers()
            assert all(value == (100000 if key in ("018", "01c") else 0)
                       for key, value in markers["graphics_fields"].items())
            assert not any(markers["console_control_fields"].values())
            reset = StoryResetOracle(sample).execute_reset()
            assert reset["context_count"] == 1 and reset["head_id"] == 0
            assert not any(reset[key] for key in ("head_previous", "head_next", "head_wait_marker",
                                                  "head_frame_depth", "event_head", "event_tail"))
            assert reset["all_old_contexts_freed"] and reset["all_old_events_freed"]
            startup = StoryStartupOracle(sample).execute_startup()
            assert startup["media_replay_driver_calls"] == 1
            assert startup["callback"] == {"hash": "38579896", "argument_count": 3,
                                           "arguments": [{"tag": 0, "value": 0x12345678},
                                                         {"tag": 2, "value": ""},
                                                         {"tag": 0, "value": 1}]}
    assert before == {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in names}
    report = {"schema": 1, "timestamp": timestamp(), "status": "passed",
              "source_sha256": before, "source_changed_during_test": False,
              "profiles": profile_evidence, "cases": cases, "case_count": len(cases),
              "visual_reset_markers": markers, "context_event_reset": reset,
              "audio_reset_defaults": audio_defaults,
              "post_reader_startup": startup,
              "runtime_import_integrated": False, "complete_story_restore_verified": False,
              "device_tests_executed": False, "native_game_process_launched": False,
              "scope": "original audio-header writes, stream-gated copies, replay arguments and reset state",
              "replay_transport_executed": False,
              "important_findings": [
                  "V1.105 skips sound capture when request flags include bit30; V1.103 does not.",
                  "A stream status of -1 still counts nonzero and is copied; running=0 or missing handle is omitted.",
                  "BGM capture depends on stored name only; it does not query stream status.",
                  "Replay always calls BGM helper, even with empty name; sound helpers require nonempty names.",
                  "Original context reset removes old threads/events and creates a fresh context id0.",
                  "Main startup caller replays media after native reader; reader UI refresh is not media restore.",
                  "Main startup then requests HOT_RESET callback hash38579896 with three arguments."],
              "limits": ["Audio seek positions/pause lifecycle are not stored by this capture function.",
                         "Other header integer semantics remain raw until corresponding setters are validated.",
                         "Only marker reset, not rendered pixels or console body text, was verified.",
                         "V1.103 media replay remains unexecuted; only its capture is compared."],
              "evidence": str(evidence.relative_to(ROOT))}
    write_json(ROOT/"reports/story_media_layout_tests.json", report)
    write_json(evidence/"20261110_story_media_layout_tests.json", report)
    print(f"story media layout: {len(cases)} native capture cases and reset/replay marker checks passed")


if __name__ == "__main__":
    main()
