"""Bounded RCT/RC8 decoding and explicit base/mask composition.

Format research: morkt/GARbro b09ee4570ccb, ArcFormats/Majiro/ImageRCT.cs
and ImageRC8.cs. No errors are silently ignored. Original members stay in ARC.
"""
from __future__ import annotations
from collections import OrderedDict
import hashlib
import io
from pathlib import Path, PurePosixPath
import struct
import zlib

from PIL import Image
from majiro_arc import Catalogue
from port_common import ROOT, atomic_bytes, sha256

MAGIC = b"\x98\x5a\x92\x9a"
RGB_SHIFTS = (-16, -32, -48, -64, -80, -96,
              49, 33, 17, 1, -15, -31, -47,
              50, 34, 18, 2, -14, -30, -46,
              51, 35, 19, 3, -13, -29, -45,
              36, 20, 4, -12, -28)
INDEX_SHIFTS = (-16, -32, -48, -64, 49, 33, 17, 1, -15, -31, -47, 34, 18, 2, -14, -30)


def unpack(data: bytes, width: int, height: int, pixel_bytes: int, trailer=None):
    remaining = width * height
    output = bytearray(remaining * pixel_bytes)
    position, cursor, literal_code = 0, 0, 0
    shifts = RGB_SHIFTS if pixel_bytes == 3 else INDEX_SHIFTS
    count_bits = 2 if pixel_bytes == 3 else 3
    count_mask = (1 << count_bits) - 1

    def word():
        nonlocal cursor
        if cursor + 2 > len(data):
            raise ValueError("truncated image run")
        value, = struct.unpack_from("<H", data, cursor)
        cursor += 2
        return value

    while remaining:
        count = literal_code + 1
        size = count * pixel_bytes
        if count > remaining or cursor + size > len(data):
            raise ValueError("image literal run out of range")
        output[position:position + size] = data[cursor:cursor + size]
        cursor += size
        position += size
        remaining -= count
        while remaining:
            if cursor >= len(data):
                raise ValueError("truncated image backreference")
            code = data[cursor]
            cursor += 1
            if not code & 0x80:
                literal_code = code + (word() if code == 0x7F else 0)
                break
            shift = shifts[(code >> count_bits) & (len(shifts) - 1)]
            # len(RGB_SHIFTS)==32 and len(INDEX_SHIFTS)==16.
            count = code & count_mask
            if count == count_mask:
                count += word()
            count += 1 if pixel_bytes == 3 else 3
            displacement = ((shift >> 4) - (shift & 15) * width) * pixel_bytes
            if count > remaining or displacement >= 0 or position + displacement < 0:
                raise ValueError("image backreference out of range")
            size = count * pixel_bytes
            # Expand overlapping repeats by doubling rather than byte-by-byte.
            copied = 0
            while copied < size:
                block = min(-displacement, size - copied)
                start = position + copied
                output[start:start + block] = output[start + displacement:start + displacement + block]
                copied += block
            position += size
            remaining -= count
    if cursor != len(data) and not (trailer is not None and data[cursor:] == trailer):
        raise ValueError(f"unconsumed image bytes: {len(data) - cursor}")
    return bytes(output)


def metadata(data: bytes):
    if len(data) < 20 or data[:4] != MAGIC:
        raise ValueError("invalid Majiro image header")
    width, height = struct.unpack_from("<II", data, 8)
    if not (0 < width <= 32768 and 0 < height <= 32768 and width * height <= 64 * 1024 * 1024):
        raise ValueError("image dimensions exceed budget")
    return width, height


def image_key(password: bytes):
    """Majiro TS00/TS01 key, derived from the exact script string bytes."""
    if not isinstance(password, bytes):
        raise TypeError("image password must contain original byte-string data")
    crc = zlib.crc32(password)
    table = []
    for i in range(256):
        value = i
        for _ in range(8):
            value = (value >> 1) ^ (0xEDB88320 if value & 1 else 0)
        table.append(value)
    return b"".join(struct.pack("<I", crc ^ table[(i + crc) & 255]) for i in range(256))


def find_image_password(catalogue):
    """Recognize a constant setter in start.mjo; never execute game code."""
    from majiro_mjo import Script
    script = Script("start.mjo", catalogue.get("start.mjo").read())
    candidates = []
    for index, instruction in enumerate(script.instructions):
        if (instruction.name not in ("syscall", "syscallp")
                or instruction.operands != (0x7A7B6ED4, 1) or index == 0):
            continue
        previous = script.instructions[index - 1]
        if previous.name != "ldstr":
            raise ValueError("dynamic image password requires execution before image decoding")
        length, = struct.unpack_from("<H", script.code, previous.offset + 2)
        raw = script.code[previous.offset + 4:previous.offset + 4 + length]
        if not raw.endswith(b"\0"):
            raise ValueError("unterminated image password")
        candidates.append(raw[:-1])
    unique = set(candidates)
    if len(unique) != 1:
        raise ValueError("one constant image password was not found in start.mjo")
    return candidates[0]


class Decoder:
    def __init__(self, catalogue=None, image_password=None):
        self.catalogue = catalogue or Catalogue(ROOT)
        self.cache = OrderedDict()
        self.encryption_key = image_key(image_password) if image_password is not None else None

    def decrypt_pixels(self, data):
        if self.encryption_key is None:
            try:
                self.encryption_key = image_key(find_image_password(self.catalogue))
            except (KeyError, ValueError, UnicodeError) as error:
                raise ValueError(f"encrypted image key unavailable: {error}") from error
        key = self.encryption_key
        return bytes(value ^ key[index & 1023] for index, value in enumerate(data))

    def pixels(self, name: str, ancestors=()):
        member = self.catalogue.resolve(name, (".rct", ".rc8"))
        name = member.name
        if name in ancestors or len(ancestors) >= 12:
            raise ValueError(f"image base cycle/depth: {name}")
        if name in self.cache:
            image, dependencies = self.cache.pop(name)
            self.cache[name] = image, dependencies
            return image.copy(), list(dependencies)
        data = member.read()
        width, height = metadata(data)
        dependencies = [member]
        variant = data[4:8]
        if variant == b"8_00":
            if len(data) < 20 + 768:
                raise ValueError(f"{name}: truncated RC8 palette")
            palette = data[20:20 + 768]
            indices = unpack(data[20 + 768:], width, height, 1)
            image = Image.frombytes("P", (width, height), indices)
            image.putpalette(palette)
        elif variant in (b"TC00", b"TC01", b"TS00", b"TS01"):
            data_size, = struct.unpack_from("<I", data, 16)
            offset = 20
            base_name = None
            if variant in (b"TC01", b"TS01"):
                if offset + 2 > len(data):
                    raise ValueError(f"{name}: truncated base filename length")
                name_size, = struct.unpack_from("<H", data, offset)
                offset += 2
                raw_name = data[offset:offset + name_size]
                if len(raw_name) != name_size or not raw_name.endswith(b"\0"):
                    raise ValueError(f"{name}: invalid base filename")
                base_name = raw_name[:-1].decode("cp932")
                offset += name_size
            if offset + data_size != len(data):
                raise ValueError(f"{name}: payload size mismatch")
            payload = data[offset:]
            if variant.startswith(b"TS"):
                payload = self.decrypt_pixels(payload)
            # Encrypted files in SWITCH/White/Amber Quartz append "TS"
            # inside the encrypted payload. Only that exact suffix is allowed
            # after the full decoded image; TC files stay strictly bounded.
            pixels = unpack(payload, width, height, 3,
                            trailer=b"TS" if variant.startswith(b"TS") else None)
            image = Image.frombytes("RGB", (width, height), pixels, "raw", "BGR")
            if base_name:
                base, sources = self.pixels(base_name, (*ancestors, name))
                if base.size != image.size:
                    raise ValueError(f"{name}: base dimensions mismatch")
                import numpy as np
                overlay = np.asarray(image)
                transparent = np.all(overlay == (255, 0, 0), axis=2)
                image = Image.composite(base.convert("RGB"), image,
                                        Image.fromarray(transparent.astype("uint8") * 255))
                dependencies.extend(sources)
        else:
            raise ValueError(f"{name}: unsupported image variant {variant!r}")
        self.cache[name] = image.copy(), list(dependencies)
        while len(self.cache) > 2:
            self.cache.popitem(last=False)
        return image, dependencies

    def rgba(self, name: str):
        member = self.catalogue.resolve(name, (".rct", ".rc8"))
        image, dependencies = self.pixels(member.name)
        if member.extension == ".rct":
            mask_name = str(PurePosixPath(member.name).with_suffix("")) + "_.rc8"
            if self.catalogue.key(mask_name) in self.catalogue.lookup:
                mask, sources = self.pixels(mask_name)
                if mask.size != image.size:
                    raise ValueError(f"{name}: alpha mask dimensions mismatch")
                import numpy as np
                values = np.asarray(mask.convert("RGB"), dtype="uint16")
                alpha = 255 - values.sum(axis=2) // 3
                image = image.convert("RGBA")
                image.putalpha(Image.fromarray(alpha.astype("uint8")))
                dependencies.extend(sources)
        return image, list({m.identity: m for m in dependencies}.values())

    def convert(self, name: str):
        member = self.catalogue.resolve(name, (".rct", ".rc8"))
        image, dependencies = self.rgba(member.name)
        from ui_image_localization import apply,write_shared,companion_name
        parent=companion_name(member.name)
        if parent:
            from PIL import ImageOps
            color,parents=self.rgba(parent)
            color,localization=apply(color,parent)
            alpha=ImageOps.invert(color.getchannel("A"))
            image=Image.frombytes("P",alpha.size,alpha.tobytes())
            image.putpalette([v for n in range(256) for v in (n,n,n)])
            # Keep the queried member first for source-integrity verification.
            dependencies=list({m.identity:m for m in [*dependencies,*parents]}.values())
            localization={**localization,"labels":0,"alpha_for":parent}
        else:
            image,localization=apply(image,member.name)
        key = hashlib.sha256(member.name.encode("utf-8")).hexdigest()[:20]
        path = ROOT / "resources/image" / (key + ".png")
        output = io.BytesIO()
        image.save(output, format="PNG", compress_level=6)
        if localization:
            write_shared(image,path)
        else:
            atomic_bytes(path, output.getvalue())
        record={
            "name": member.name, "file": "image/" + path.name,
            "width": image.width, "height": image.height, "mode": image.mode,
            "source_depth": 8 if member.extension == ".rc8" else 32,
            "sha256": sha256(path), "bytes": path.stat().st_size,
            "sources": [{"member": m.identity, "name": m.name, "sha256": m.digest()} for m in dependencies],
            "validation": "decoded, original-engine pixel comparison pending",
        }
        if localization:record["localization"]=localization
        return record
