"""Read duration from the original ASF header without extracting video frames."""
from __future__ import annotations
from pathlib import Path
import struct
import uuid
from port_common import ROOT


def catalogue():
    values={}
    header=uuid.UUID("75b22630-668e-11cf-a6d9-00aa0062ce6c").bytes_le
    properties=uuid.UUID("8cabdca1-a947-11cf-8ee4-00c00c205365").bytes_le
    for path in (ROOT/"movie").glob("*.wmv"):
        with path.open("rb")as stream:
            data=stream.read(30)
            assert data[:16]==header,"unsupported movie header"
            total,objects=struct.unpack_from("<QI",data,16)
            assert total<=16*1024*1024
            duration=None
            for _ in range(objects):
                offset=stream.tell()
                obj=stream.read(24)
                assert len(obj)==24
                size=struct.unpack_from("<Q",obj,16)[0]
                assert 24<=size<=total-offset
                if obj[:16]==properties:
                    payload=stream.read(min(size-24,80))
                    play=struct.unpack_from("<Q",payload,40)[0]
                    preroll=struct.unpack_from("<Q",payload,56)[0]
                    duration=play/10000-preroll
                stream.seek(offset+size)
        assert duration and duration>0
        values[path.name.lower()]={"file":"movie/"+path.name,"bytes":path.stat().st_size,
                                  "duration_ms":duration}
    return values
