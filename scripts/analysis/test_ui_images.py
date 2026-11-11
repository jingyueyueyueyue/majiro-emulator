"""Check localized assets keep source artwork, full label bounds and one shared file."""
from __future__ import annotations
import os
from pathlib import Path
import sys
import numpy as np
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,read_json,write_json
from majiro_image import Decoder


def run():
    config=read_json(ROOT/"localization/ui_images.json")
    catalogue=read_json(ROOT/"resources/image_catalogue.json")
    decoder=Decoder();cases=[]
    for group in config["groups"]:
        for name in group["images"]:
            source,_=decoder.rgba(name)
            path=ROOT/"resources"/catalogue[name]["file"]
            with Image.open(path)as image:
                actual=np.asarray(image.convert("RGBA"))
            original=np.asarray(source.convert("RGBA"))
            assert actual.shape==original.shape,name
            allowed=np.zeros(actual.shape[:2],dtype=bool)
            for item in group["labels"]:
                x0,y0,x1,y1=item["box"]if isinstance(item,dict)else item[0]
                allowed[y0:y1,x0:x1]=True
            assert np.array_equal(actual[~allowed],original[~allowed]),"artwork outside text changed: "+name
            assert os.path.samefile(path,ROOT/"localization/images"/path.name),name
            assert np.any(actual[allowed]!=original[allowed]),name
            mask_name=name[:-4]+"_.rc8"
            if mask_name in catalogue:
                mask_path=ROOT/"resources"/catalogue[mask_name]["file"]
                with Image.open(mask_path) as mask:
                    assert np.array_equal(np.asarray(mask.convert("L")),255-actual[:,:,3]),(
                        "explicit RC8 mask does not match localized RGBA",name)
                assert os.path.samefile(mask_path,ROOT/"localization/images"/mask_path.name)
            if name.startswith("config_"):
                # The description erase box previously cut six rows from
                # the reset button's upper-left border (x766..794, y40..46).
                assert np.array_equal(actual[40:47,760:930],original[40:47,760:930]),(
                    "settings reset button border clipped",name)
            if name=="askyesno_base.rct":
                for item in group["labels"]:
                    x0,y0,x1,y1=item[0]
                    crop=actual[y0:y1,x0:x1]
                    # These are interiors of the three button states.
                    # A lettering erase must not borrow the outer shadow
                    # or change the existing fully opaque button surface.
                    assert np.all(original[y0:y1,[x0-1,x1],3]==255)
                    assert np.all(crop[:,:,3]==255),"confirmation button has an alpha hole"
                    center=(x1-x0)/2
                    side=np.abs(np.arange(x1-x0)-center)>20
                    assert not np.any(crop[:,side,:3].min(axis=2)>180),(
                        "Japanese lettering survives around Chinese confirmation label",item)
            if name.startswith(("sl_load_","sl_save_")):
                # The user's reported caption was left above the source line.
                # Old Japanese occupied x905..1109, y153..168. Its left margin
                # is now background, while centered Chinese occupies the middle.
                margin=actual[150:171,901:946,:3]
                assert not np.any(margin.min(axis=2)>180),"Japanese caption remains: "+name
                text=actual[150:171,960:1050,:3]
                assert np.any(text.min(axis=2)>200),"Chinese caption missing: "+name
            cases.append(name)
    report={"status":"passed","images":len(cases),"cases":cases,
        "checks":["source dimensions unchanged","artwork outside lettering unchanged",
                  "formal and runtime PNG share the same file","preview caption erased at original baseline"]}
    write_json(ROOT/"reports/ui_image_tests.json",report)
    print("localized UI image checks:",len(cases),"passed")
    return report


if __name__=="__main__":
    run()
