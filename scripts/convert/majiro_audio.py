"""Extract only requested audio members, once, retaining original encoded bytes."""
from __future__ import annotations
import hashlib
from pathlib import Path

import soundfile
from majiro_arc import Catalogue
from port_common import ROOT, atomic_bytes, sha256


class Audio:
    def __init__(self, catalogue=None):
        self.catalogue = catalogue or Catalogue(ROOT)

    def prepare(self, name):
        member = self.catalogue.resolve(name, (".ogg", ".wav"))
        key = hashlib.sha256(member.name.encode("utf-8")).hexdigest()[:20]
        output = ROOT / "resources/audio" / (key + member.extension)
        source_hash = member.digest()
        if not output.exists() or sha256(output) != source_hash:
            atomic_bytes(output, member.read(limit=256 * 1024 * 1024))
        info = soundfile.info(output)
        return {"name": member.name, "file": "audio/" + output.name,
                "bytes": output.stat().st_size, "sha256": source_hash,
                "member": member.identity, "sample_rate": info.samplerate,
                "channels": info.channels, "frames": info.frames,
                "duration_ms": info.frames * 1000 / info.samplerate,
                "loop_metadata": "original encoded file preserved; native loop behavior pending"}
