"""Check the production Android PE icon reader against malformed and real data.

The Android API is compiled against android.jar. Behavioral JVM checks substitute
only SAF and Bitmap/PNG decoding; the PE parser and all DIB pixel code are the
production Java implementation. Original EXEs are never executed or copied.
"""
from __future__ import annotations
import hashlib
import io
import json
from pathlib import Path
import struct
import subprocess
import sys

import pefile
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, read_json, sha256, timestamp, write_json


STUBS = {
"android/net/Uri.java": """
package android.net;
public final class Uri {
 private final String value; private Uri(String value){this.value=value;}
 public static Uri parse(String value){return new Uri(value);}
 public String getScheme(){int at=value.indexOf(':');return at<0?null:value.substring(0,at);}
}
""",
"android/content/res/AssetFileDescriptor.java": """
package android.content.res;
import java.io.*;
public final class AssetFileDescriptor implements AutoCloseable {
 private final File file; private final long start,length;
 public AssetFileDescriptor(File file,long start,long length){this.file=file;this.start=start;this.length=length;}
 public FileInputStream createInputStream() throws IOException{return new FileInputStream(file);}
 public long getStartOffset(){return start;} public long getDeclaredLength(){return length;}
 public void close() throws IOException{}
}
""",
"android/content/ContentResolver.java": """
package android.content;
import android.content.res.AssetFileDescriptor; import android.net.Uri; import java.io.*;
public final class ContentResolver {
 private final File file; private final long start,length;private final boolean pipe;
 public int opens,streamOpens,bytesRead,generatedBytes;public boolean denied;
 public ContentResolver(File file,long start,long length,boolean pipe){
  this.file=file;this.start=start;this.length=length;this.pipe=pipe;
 }
 public AssetFileDescriptor openAssetFileDescriptor(Uri uri,String mode) throws IOException{
  opens++;if(denied)throw new SecurityException("not authorized");
  if(pipe)throw new IOException("provider pipe");return new AssetFileDescriptor(file,start,length);
 }
 public InputStream openInputStream(Uri uri) throws IOException{
  streamOpens++;if(denied)throw new SecurityException("not authorized");
  if(generatedBytes>0)return new InputStream(){
   public int read(){if(bytesRead>=generatedBytes)return -1;bytesRead++;return 0;}
   public int read(byte[] b,int off,int len){int n=Math.min(len,generatedBytes-bytesRead);
    if(n==0)return -1;java.util.Arrays.fill(b,off,off+n,(byte)0);bytesRead+=n;return n;}
  };
  return new FileInputStream(file);
 }
}
""",
"android/graphics/Bitmap.java": """
package android.graphics;
public final class Bitmap {
 public enum Config {ARGB_8888}
 public final int[] pixels;private final int width,height;
 private Bitmap(int[] pixels,int width,int height){this.pixels=pixels;this.width=width;this.height=height;}
 public static Bitmap createBitmap(int[] colors,int w,int h,Config config){return new Bitmap(colors.clone(),w,h);}
 public int getWidth(){return width;}public int getHeight(){return height;}public void recycle(){}
}
""",
"android/graphics/BitmapFactory.java": """
package android.graphics;
import java.io.*;import java.awt.image.BufferedImage;import javax.imageio.*;
import javax.imageio.stream.ImageInputStream;
public final class BitmapFactory {
 public static final class Options {
  public boolean inJustDecodeBounds,inScaled;public int outWidth=-1,outHeight=-1;public Bitmap.Config inPreferredConfig;
 }
 public static Bitmap decodeByteArray(byte[] bytes,int offset,int length,Options options){
  try(ImageInputStream input=ImageIO.createImageInputStream(new ByteArrayInputStream(bytes,offset,length))){
   java.util.Iterator<ImageReader> readers=ImageIO.getImageReaders(input);if(!readers.hasNext())return null;
   ImageReader reader=readers.next();
   try{reader.setInput(input);options.outWidth=reader.getWidth(0);options.outHeight=reader.getHeight(0);
    if(options.inJustDecodeBounds)return null;BufferedImage image=reader.read(0);
    int[] pixels=image.getRGB(0,0,image.getWidth(),image.getHeight(),null,0,image.getWidth());
    return Bitmap.createBitmap(pixels,image.getWidth(),image.getHeight(),Bitmap.Config.ARGB_8888);
   }finally{reader.dispose();}
  }catch(IOException|RuntimeException invalid){return null;}
 }
}
""",
}


def pixel_hash(pixels):
    return hashlib.sha256(b"".join(struct.pack(">I", value) for value in pixels)).hexdigest()


def dib(width, height, bits, pixels, *, mask=None, header=40, compression=0,
        masks=None, palette=None, colors=0, top_down=False, omit_mask=False):
    stride = (width * bits + 31) // 32 * 4
    mask_stride = (width + 31) // 32 * 4
    if header == 12:
        data = bytearray(struct.pack("<IHHHH", 12, width, height * 2, 1, bits))
    else:
        data = bytearray(header)
        struct.pack_into("<IiiHHIIiiII", data, 0, header, width,
                         height * (-2 if top_down else 2), 1, bits, compression, 0, 0, 0, colors, 0)
    if masks:
        packed = struct.pack("<" + "I" * len(masks), *masks)
        if header == 40:
            data.extend(packed)
        else:
            data[40:40 + len(packed)] = packed
    if palette:
        data.extend(b"".join(bytes((c & 255, c >> 8 & 255, c >> 16 & 255)) +
                             (b"" if header == 12 else b"\0") for c in palette))
    for y in range(height) if top_down else reversed(range(height)):
        row = bytearray(stride)
        for x in range(width):
            value = pixels[y * width + x]
            if bits <= 8:
                row[x * bits // 8] |= value << (8 - bits - x * bits % 8)
            else:
                row[x * (bits // 8):(x + 1) * (bits // 8)] = value.to_bytes(bits // 8, "little")
        data.extend(row)
    if not omit_mask:
        for y in range(height) if top_down else reversed(range(height)):
            row = bytearray(mask_stride)
            for x in range(width):
                if mask and mask[y * width + x]:
                    row[x // 8] |= 0x80 >> (x % 8)
            data.extend(row)
    return bytes(data)


def png(width, height, color=(31, 87, 191, 123)):
    output = io.BytesIO()
    Image.new("RGBA", (width, height), color).save(output, format="PNG")
    return output.getvalue()


def group(entries):
    return struct.pack("<HHH", 0, 1, len(entries)) + b"".join(
        struct.pack("<BBBBHHIH", w if w < 256 else 0, h if h < 256 else 0, 0, 0, 1, bits, len(data), ident)
        for ident, w, h, bits, data in entries)


def pe(images, groups=None, *, plus=False, prefix=0, language=0, icon_languages=None):
    """Independently assemble a PE resource tree with RVA != disk offset."""
    if groups is None:
        groups = [(1, group([(ident, w, h, bits, data) for ident, w, h, bits, data in images]))]
    leaves = {3: {}, 14: {}}
    for ident, w, h, bits, data in images:
        leaves[3].setdefault(ident, {})[language] = data
    if icon_languages:
        for ident, lang, data in icon_languages:
            leaves[3].setdefault(ident, {})[lang] = data
    for ident, data in groups:
        leaves[14].setdefault(ident, {})[language] = data
    resource = bytearray()
    def directory(items):
        at = len(resource)
        resource.extend(bytes(16 + len(items) * 8))
        struct.pack_into("<H", resource, at + 14, len(items))
        return at
    root = directory(leaves)
    patches = []
    for type_index, (kind, names) in enumerate(sorted(leaves.items())):
        type_at = directory(names)
        struct.pack_into("<II", resource, root + 16 + type_index * 8, kind, type_at | 0x80000000)
        for name_index, (ident, languages) in enumerate(sorted(names.items())):
            name_at = directory(languages)
            struct.pack_into("<II", resource, type_at + 16 + name_index * 8, ident, name_at | 0x80000000)
            for lang_index, (lang, data) in enumerate(sorted(languages.items())):
                data_at = len(resource)
                resource.extend(bytes(16))
                struct.pack_into("<II", resource, name_at + 16 + lang_index * 8, lang, data_at)
                patches.append((data_at, data))
    for data_at, data in patches:
        while len(resource) & 3:
            resource.append(0)
        location = len(resource)
        resource.extend(data)
        struct.pack_into("<II", resource, data_at, 0x3000 + prefix + location, len(data))
    optional_size = 240 if plus else 224
    data = bytearray(0x600 + prefix) + resource
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", data, 0x84, 0x8664 if plus else 0x14c, 1, 0, 0, 0, optional_size, 0)
    optional = 0x98
    struct.pack_into("<H", data, optional, 0x20b if plus else 0x10b)
    struct.pack_into("<I", data, optional + 60, 0x600)
    directory_at = optional + (112 if plus else 96)
    struct.pack_into("<I", data, directory_at - 4, 16)
    struct.pack_into("<II", data, directory_at + 16, 0x3000 + prefix, len(resource))
    section_at = optional + optional_size
    data[section_at:section_at + 8] = b".rsrc\0\0\0"
    struct.pack_into("<IIII", data, section_at + 8, prefix + len(resource), 0x3000, prefix + len(resource), 0x600)
    return bytes(data)


def real_executables():
    for sample in read_json(ROOT / "config/player_samples.json")["games"]:
        base = Path(sample["directory"])
        for parent in (base, base / "bin"):
            for path in sorted(parent.glob("*.exe")):
                if path.stem.lower() in ("autorun", "install", "uninst"):
                    continue
                yield sample["id"], path


def pillow_icon(path):
    executable = pefile.PE(str(path), fast_load=True)
    try:
        executable.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]])
        resources = {entry.id: entry for entry in executable.DIRECTORY_ENTRY_RESOURCE.entries}
        app = sorted(resources[14].directory.entries, key=lambda e: (e.id is None, e.id or str(e.name)))[0]
        language = sorted(app.directory.entries, key=lambda e: e.id)[0]
        info = language.data.struct
        data = executable.get_data(info.OffsetToData, info.Size)
        icons = {entry.id: entry for entry in resources[3].directory.entries}
        choices = []
        for i in range(struct.unpack_from("<H", data, 4)[0]):
            w, h, colors, reserved, planes, bits, size, ident = struct.unpack_from("<BBBBHHIH", data, 6 + i * 14)
            lang = next((e for e in icons[ident].directory.entries if e.id == language.id),
                        sorted(icons[ident].directory.entries, key=lambda e: e.id)[0])
            meta = lang.data.struct
            image = executable.get_data(meta.OffsetToData, meta.Size)
            if len(image) != size:
                continue
            choices.append((w or 256, h or 256, bits, i, image, colors, planes))
        width, height, bits, _, image, colors, planes = sorted(
            choices, key=lambda value: (-value[0] * value[1], -value[2], value[3]))[0]
        ico = struct.pack("<HHH", 0, 1, 1) + struct.pack(
            "<BBBBHHII", width if width < 256 else 0, height if height < 256 else 0,
            colors, 0, planes, bits, len(image), 22) + image
        decoded = Image.open(io.BytesIO(ico)).convert("RGBA")
        values = [a << 24 | r << 16 | g << 8 | b
                  for r, g, b, a in struct.iter_unpack("4B", decoded.tobytes())]
        return width, height, pixel_hash(values), image.startswith(b"\x89PNG"), image
    finally:
        executable.close()


def main():
    tools = read_json(ROOT / "reports/android_toolchain.json")
    java = Path(tools["java"]) / "bin"
    folder = archive_dir("game_icon_tests")
    classes, android_classes = folder / "classes", folder / "android-classes"
    classes.mkdir(); android_classes.mkdir()
    source = ROOT / "player/android/src/org/majiro/player/GameIcon.java"
    fixture = ROOT / "scripts/analysis/GameIconChecks.java"
    cases = []
    def case(kind, name, raw, width=None, height=None, values=None):
        filename = name + ".bin"
        (folder / filename).write_bytes(raw)
        row = [kind, name, filename]
        if width is not None:
            row.extend((str(width), str(height)))
            if values is not None: row.append(pixel_hash(values))
        cases.append("\t".join(row))
    red, green, blue, white = 0xffff0000, 0xff00ff00, 0xff0000ff, 0xffffffff
    colors = [red, green, blue, white]
    for bits in (1, 4, 8):
        indices = [0, 1, 1, 0] if bits == 1 else [0, 1, 2, 3]
        palette = colors[:2] if bits == 1 else colors + [0] * ((1 << bits) - 4)
        values = [palette[i] for i in indices]
        for header in (12, 40):
            case("dib", f"palette{bits}-{header}", dib(2, 2, bits, indices, palette=palette, header=header),
                 2, 2, values)
    for top_down in (False, True):
        case("dib", "rgb24-" + str(top_down), dib(2, 2, 24, [c & 0xffffff for c in colors], top_down=top_down),
             2, 2, colors)
    case("dib", "mask24", dib(2, 2, 24, [c & 0xffffff for c in colors], mask=[0, 1, 0, 1]),
         2, 2, [red, green & 0xffffff, blue, white & 0xffffff])
    for header in (40, 52, 56, 108, 124):
        case("dib", "alpha32-" + str(header), dib(2, 2, 32, [0x00ff0000, 0x8000ff00, blue, white],
             mask=[1] * 4, header=header), 2, 2, [0x00ff0000, 0x8000ff00, blue, white])
    case("dib", "legacy32", dib(2, 2, 32, [c & 0xffffff for c in colors], mask=[1, 0, 0, 1]),
         2, 2, [red & 0xffffff, green, blue, white & 0xffffff])
    case("dib", "alpha32-no-mask", dib(2, 2, 32, colors, omit_mask=True), 2, 2, colors)
    case("bad-dib", "legacy32-no-mask", dib(2, 2, 32, [c & 0xffffff for c in colors], omit_mask=True))
    case("bad-dib", "truncated24-mask", dib(2, 2, 24, [c & 0xffffff for c in colors], omit_mask=True))
    case("dib", "rgb555", dib(2, 2, 16, [0x7c00, 0x3e0, 0x1f, 0x7fff]), 2, 2, colors)
    for header in (40, 52, 108):
        case("dib", "rgb565-" + str(header), dib(2, 2, 16, [0xf800, 0x7e0, 0x1f, 0xffff],
             compression=3, masks=(0xf800, 0x7e0, 0x1f), header=header), 2, 2, colors)
    case("dib", "rgba4444", dib(2, 2, 16, [0xff00, 0xf0f0, 0xf00f, 0xffff], compression=6,
         masks=(0xf00, 0xf0, 0xf, 0xf000)), 2, 2, colors)
    case("dib", "rgba32-fields", dib(2, 2, 32, colors, compression=6, header=56,
         masks=(0xff0000, 0xff00, 0xff, 0xff000000)), 2, 2, colors)
    for name, change in [
        ("width-zero", (4, "<i", 0)), ("width-negative", (4, "<i", -1)),
        ("width-huge", (4, "<i", 0x7fffffff)), ("height-odd", (8, "<i", 3)),
        ("height-min", (8, "<i", -2147483648)), ("planes", (12, "<H", 2)),
        ("bits", (14, "<H", 48)), ("compression", (16, "<I", 1)),
        ("colors-huge", (32, "<I", 0xffffffff)), ("header-huge", (0, "<I", 0xffffffff)),
    ]:
        raw = bytearray(dib(2, 2, 32, colors))
        offset, encoding, value = change
        struct.pack_into(encoding, raw, offset, value)
        case("bad-dib", name, raw)
    case("bad-dib", "overlapping-masks", dib(2, 2, 16, [0] * 4, compression=3, masks=(0xff, 0xff, 0xf)))
    case("bad-dib", "noncontiguous-mask", dib(2, 2, 16, [0] * 4, compression=3, masks=(0xf0f, 0xf0, 0xf000)))
    case("bad-dib", "bad-palette-index", dib(2, 2, 8, [0, 1, 2, 3], palette=colors[:2], colors=2))
    case("png", "png-valid", png(2, 3), 2, 3)
    oversized = bytearray(png(2, 3))
    struct.pack_into(">I", oversized, 16, 65535)
    case("bad-png", "png-oversized", oversized)
    case("bad-png", "png-truncated", png(2, 3)[:20])
    small = dib(2, 2, 32, colors)
    blue_icon = dib(2, 2, 32, [blue] * 4)
    base = pe([(1, 2, 2, 32, small)])
    for name, plus, prefix in (("pe32", False, 0), ("pe64", True, 0), ("rva-root-offset", False, 32)):
        case("pe", name, pe([(1, 2, 2, 32, small)], plus=plus, prefix=prefix), 2, 2, colors)
    png_image = png(32, 32)
    case("pe", "png-preferred", pe([(1, 2, 2, 32, small), (2, 32, 32, 32, png_image)]),
         32, 32, [0x7b1f57bf] * 1024)
    case("pe", "bad-large-fallback", pe([(1, 2, 2, 32, small), (2, 32, 32, 32, b"bad png" * 10)]),
         2, 2, colors)
    case("pe", "first-group-app", pe([(1, 2, 2, 32, small), (2, 32, 32, 32, png_image)],
         groups=[(1, group([(1, 2, 2, 32, small)])), (2, group([(2, 32, 32, 32, png_image)]))]), 2, 2, colors)
    case("pe", "matching-language", pe([(1, 2, 2, 32, small)], language=1041,
         icon_languages=[(1, 0, blue_icon)]), 2, 2, colors)
    for name, at, encoding, value in [
        ("bad-mz", 0, "<H", 0), ("bad-pe", 0x80, "<I", 0),
        ("e-lfanew-overflow", 60, "<I", 0xffffffff),
        ("section-count", 0x86, "<H", 65535), ("optional-size", 0x94, "<H", 65535),
        ("root-outside", 0x98 + 96 + 16, "<I", 0xfffffff0),
        ("resource-size-overflow", 0x98 + 96 + 20, "<I", 0xffffffff),
        ("raw-offset-outside", 0x98 + 224 + 20, "<I", 0xfffffff0),
        ("root-cycle", 0x600 + 20, "<I", 0x80000000),
        ("directory-count", 0x600 + 14, "<H", 65535),
        ("named-overflow", 0x600 + 16, "<I", 0xffffffff),
    ]:
        raw = bytearray(base); struct.pack_into(encoding, raw, at, value)
        case("bad-pe", name, raw)
    real_rows, reference_failures = [], []
    for game, path in real_executables():
        try:
            w, h, digest, is_png, extracted = pillow_icon(path)
            cases.append("\t".join(("real", game + "/" + path.name, str(path), str(w), str(h), digest)))
            real_rows.append({"game": game, "path": str(path), "sha256": sha256(path),
                              "width": w, "height": h, "encoding": "PNG" if is_png else "DIB",
                              "argb_sha256": digest, "reference": "pefile + Pillow ICO"})
        except (KeyError, AttributeError, IndexError, OSError, pefile.PEFormatError) as error:
            reference_failures.append({"game": game, "path": str(path), "reason": str(error)})
    (folder / "cases.tsv").write_text("\n".join(cases) + "\n", encoding="utf8")
    sources = [source, fixture]
    for name, text in STUBS.items():
        path = folder / "stubs" / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf8"); sources.append(path)
    def run(name, arguments, timeout=120):
        result = subprocess.run(arguments, capture_output=True, timeout=timeout)
        (folder / (folder.name[:8] + "_" + name + ".txt")).write_bytes(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError(name + " failed; see " + str(folder))
        return result.stdout
    jar = Path(tools["sdk"]) / "platforms/android-36/android.jar"
    run("android_javac", [str(java / "javac.exe"), "-encoding", "UTF-8", "-source", "8", "-target", "8",
                         "-bootclasspath", str(jar), "-d", str(android_classes), str(source)])
    run("fixture_javac", [str(java / "javac.exe"), "-encoding", "UTF-8", "-source", "8", "-target", "8",
                         "-d", str(classes), *map(str, sources)])
    result = json.loads(run("checks", [str(java / "java.exe"), "-Xmx192m", "-Djava.awt.headless=true",
                                      "-cp", str(classes), "org.majiro.player.GameIconChecks", str(folder)]))
    result.update(schema=1, timestamp=timestamp(), source_sha256=sha256(source),
                  fixture_sha256=sha256(fixture), script_sha256=sha256(Path(__file__)),
                  evidence=str(folder.relative_to(ROOT)), android_api_compile=True, device_execution=False,
                  real_samples=real_rows, reference_unavailable=reference_failures,
                  scope="production PE32/PE32+ parser and DIB pixels; SAF/Bitmap PNG codec use JVM substitutes",
                  api="GameIcon.load(ContentResolver, Uri) -> Bitmap or null",
                  limits={"side": 512, "icon_bytes": 4 * 1024 * 1024, "read_bytes": 16 * 1024 * 1024,
                          "stream_bytes": 16 * 1024 * 1024, "table_entries": 8192, "decode_attempts": 64})
    write_json(ROOT / "reports/game_icon_tests.json", result)
    print("Game icons:", result["checks"], "checks,", result["real_executables"],
          "real EXEs,", result["truncation_and_mutation_cases"], "malformed cases; Android API compiled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
