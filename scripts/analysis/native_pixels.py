"""Own DLL ABI adapter; returned native buffers are always released."""
from __future__ import annotations
import ctypes
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT,native_pixels_library,player_pixels_library


class Pixels:
    def __init__(self,port=False):
        self.path=native_pixels_library()if port else player_pixels_library()
        self.dll = ctypes.CDLL(str(self.path))
        self.dll.RuriPixels.argtypes = [ctypes.c_char_p]
        self.dll.RuriPixels.restype = ctypes.c_void_p
        self.dll.Delete.argtypes = [ctypes.c_void_p]
        self.dll.Delete.restype = None
        self("reset")
        reply=self("fontfile "+str(ROOT/"resources/font/SourceHanSansSC-Regular.otf"))
        if reply!="OK":raise RuntimeError(reply)

    def __call__(self, request: str):
        if request.startswith("batch\n"):
            data=request.encode("utf-8");offset=6
            while offset<len(data):
                ending=data.index(b"\n",offset);length=int(data[offset:ending])
                offset=ending+1
                result=self(data[offset:offset+length].decode("utf-8"))
                if not result.startswith("OK"):return result
                offset+=length
            return "OK"
        if request.startswith(("load ", "load8 ")):
            operation, handle, alpha, path = request.split(" ", 3)
            if path.startswith("image/"):
                path = str(ROOT / "resources" / path)
            request = " ".join((operation, handle, alpha, path))
        pointer = self.dll.RuriPixels(request.encode("utf-8"))
        if not pointer:
            raise MemoryError("native page provider allocation failed")
        try:
            return ctypes.string_at(pointer).decode("utf-8")
        finally:
            self.dll.Delete(pointer)
