"""Check centered name cells, grayscale coverage and bounded console ink."""
from PIL import Image
import numpy as np
from native_pixels import Pixels
from port_common import ROOT, archive_dir, write_json, sha256,player_pixels_library as native_pixels_library


def unclipped_reference(text,height,scale=3):
    """Draw on a padded GDI canvas; choose the cell from the font's OS/2 metrics."""
    import ctypes as c
    from ctypes import wintypes as w
    from fontTools.ttLib import TTFont
    class Header(c.Structure):
        _fields_=[("size",w.DWORD),("width",w.LONG),("height",w.LONG),("planes",w.WORD),
                  ("bits",w.WORD),("compression",w.DWORD),("image",w.DWORD),
                  ("x",w.LONG),("y",w.LONG),("used",w.DWORD),("important",w.DWORD)]
    class Metrics(c.Structure):
        _fields_=[(n,w.LONG)for n in("height","ascent","descent","internal","external",
                  "average","maximum","weight","overhang","aspectx","aspecty")]+[
                  (n,w.WCHAR)for n in("first","last","default","breakchar")]+[
                  (n,w.BYTE)for n in("italic","underline","strike","pitch","charset")]
    g=c.WinDLL("gdi32")
    g.CreateCompatibleDC.argtypes=[w.HDC];g.CreateCompatibleDC.restype=w.HDC
    g.CreateFontW.argtypes=[c.c_int]*5+[w.DWORD]*8+[w.LPCWSTR];g.CreateFontW.restype=w.HFONT
    g.SelectObject.argtypes=[w.HDC,w.HANDLE];g.SelectObject.restype=w.HANDLE
    g.GetTextMetricsW.argtypes=[w.HDC,c.POINTER(Metrics)]
    g.CreateDIBSection.argtypes=[w.HDC,c.POINTER(Header),w.UINT,c.POINTER(c.c_void_p),w.HANDLE,w.DWORD]
    g.CreateDIBSection.restype=w.HBITMAP
    g.ExtTextOutW.argtypes=[w.HDC,c.c_int,c.c_int,w.UINT,c.c_void_p,w.LPCWSTR,w.UINT,c.POINTER(c.c_int)]
    g.SetTextColor.argtypes=[w.HDC,w.DWORD];g.SetBkMode.argtypes=[w.HDC,c.c_int]
    g.DeleteObject.argtypes=[w.HANDLE];g.DeleteDC.argtypes=[w.HDC]
    dc=g.CreateCompatibleDC(None)
    font=g.CreateFontW(-height*scale,0,0,0,400,0,0,0,1,0,0,4,0,"Source Han Sans SC")
    oldfont=g.SelectObject(dc,font)
    metrics=Metrics();assert g.GetTextMetricsW(dc,c.byref(metrics))
    guard=height*scale*2
    width,height_pixels=len(text)*height*scale,(height*scale)*6
    header=Header(c.sizeof(Header),width,-height_pixels,1,32,0,0,0,0,0,0)
    bits=c.c_void_p()
    bitmap=g.CreateDIBSection(dc,c.byref(header),0,c.byref(bits),None,0)
    oldbitmap=g.SelectObject(dc,bitmap)
    try:
        c.memset(bits.value,0,width*height_pixels*4)
        g.SetTextColor(dc,0xffffff);g.SetBkMode(dc,1)
        dx=(c.c_int*len(text))(*([height*scale]*len(text)))
        assert g.ExtTextOutW(dc,0,guard,0,None,text,len(text),dx)
        raster=np.frombuffer(c.string_at(bits.value,width*height_pixels*4),np.uint8).reshape(
            height_pixels,width,4)[:,:,0].copy()
        with TTFont(ROOT/"resources/font/SourceHanSansSC-Regular.otf")as face:
            baseline=round(height*scale*face["OS/2"].sTypoAscender/face["head"].unitsPerEm)
        top=guard+metrics.ascent-baseline
        # This assertion establishes that the reference itself contains
        # the whole glyph, before comparing with the production cell.
        assert not raster[:top].any() and not raster[top+height*scale:].any(),text
        full=raster[top:top+height*scale].astype(np.uint16)
        return ((full.reshape(height,scale,len(text)*height,scale).sum(axis=(1,3))+4)//9).astype(np.uint8)
    finally:
        g.SelectObject(dc,oldbitmap);g.SelectObject(dc,oldfont)
        g.DeleteObject(bitmap);g.DeleteObject(font);g.DeleteDC(dc)


def run():
    evidence = archive_dir("text_raster")
    api = Pixels()
    assert api("create 1 180 50 0") == api("alpha 1 2 0") == "OK"
    assert api("fill 2 0 0 180 50 255") == "OK"
    assert api("text 1 0 0 24 0 16777215 180 50 0\nSource Han Sans SC\n健　志").startswith("OK")
    path = evidence/"20261109_name.png"
    assert api("png 1 "+str(path)) == "OK"
    image = np.asarray(Image.open(path).convert("RGBA"))
    ys, xs = np.where(image[:,:,3] > 20)
    assert abs((int(ys.min())+int(ys.max()))/2-24.5) <= 1.5, (ys.min(), ys.max())
    assert abs((int(xs.min())+int(xs.max()))/2-89.5) <= 2
    name_box = [int(xs.min()),int(ys.min()),int(xs.max())+1,int(ys.max())+1]
    assert len(np.unique(image[:,:,3])) > 30, "insufficient grayscale smoothing"
    assert np.all(image[:,:,:3][image[:,:,3]>0] == 255), "colored edge in white name"
    assert api("create 3 180 70 0") == api("alpha 3 4 0") == "OK"
    assert api("fill 4 0 0 180 70 255") == "OK"
    assert api("glyphs 3 20 10 24 12 16777215 33554432\nSource Han Sans SC\n24 24 24 24\n中文边缘").startswith("OK")
    path = evidence/"20261109_glyphs.png"
    assert api("png 3 "+str(path)) == "OK"
    image = np.asarray(Image.open(path).convert("RGBA"))
    outside = image[:,:,3].copy()
    outside[10:34,20:116] = 0
    assert not outside.any(), "ink outside CONSOLE_WROTE cell would survive source clearing"
    assert np.all(image[:,:,0] == image[:,:,1]) and np.all(image[:,:,1] == image[:,:,2])
    assert api("fill 4 20 10 96 24 255") == "OK"
    path = evidence/"20261109_cleared.png"
    assert api("png 3 "+str(path)) == "OK"
    assert not np.asarray(Image.open(path).convert("RGBA"))[:,:,3].any(), "glyph fragments survive clear"
    complete=[]
    text="握着方向盘的是我。旁边是那家伙。"
    for size in(16,20,24,30,36):
        page=100+size*2;mask=page+1;width=len(text)*size
        assert api(f"create {page} {width} {size} 0")==api(f"alpha {page} {mask} 0")=="OK"
        assert api(f"fill {mask} 0 0 {width} {size} 255")=="OK"
        advances=" ".join([str(size)]*len(text))
        assert api(f"glyphs {page} 0 0 {size} {size//2} 16777215 0\nSource Han Sans SC\n{advances}\n{text}").startswith("OK")
        path=evidence/f"20261109_complete_{size}.png"
        assert api(f"png {page} {path}")=="OK"
        actual=np.asarray(Image.open(path).convert("RGBA"))[:,:,3]
        expected=unclipped_reference(text,size)
        Image.fromarray(expected).save(evidence/f"20261109_reference_{size}.png")
        assert np.array_equal(actual,expected),("glyph top/bottom cropped or shifted",size,
                                               int(np.abs(actual.astype(int)-expected).max()))
        complete.append(size)
    write_json(ROOT/"reports/text_raster_tests.json", {
        "status":"passed", "name_box":name_box, "cell":[180,50],
        "glyph_cell":[20,10,96,24], "clear_has_residual_pixels":False,
        "complete_glyph_reference_sizes":complete,
        "dll_sha256":sha256(native_pixels_library()),
        "evidence":str(evidence)})
    print("text raster: centered name, grayscale coverage, bounded ink and clean erase passed")


if __name__ == "__main__":
    run()
