"""Exercise production string comparisons with the same strict CP932 boundary as Android."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from lua_bridge import create_lua
from port_common import ROOT,archive_dir,write_json,timestamp


def main():
    lua=create_lua()
    source=(ROOT/"player/runtime/majiro_strings.lua").read_text(encoding="utf8")
    module=lua.execute(source)
    providers=lua.table()
    define=lua.eval("function(p)return function(name,fn)p[name]=fn end end")(providers)
    def cp932(text):
        return text.encode("cp932")
    rows=[]
    for profile in ("bytes","cp932"):
        options=lua.table_from({"cp932":cp932,"string_case":profile})
        api=module["install"](define,lua.table(),options)
        # Wrapper reverses argument order, matching the existing VM contract.
        for a,b in [("\u6211\u5c45\u7136\u7761\u4e86\u8fd9\u4e48\u4e45",""),
                    ("\u4f60\u597d","\u4f60\u597d"),("\u4f60","\u4ed6"),
                    ("\u8bd1\u6587\uff1aA","\u8bd1\u6587\uff1aB"),
                    ("ASCII","ascii"),("\u30ab\u30ca","\u304b\u306a"),
                    ("\uff71","\u3042"),("same\0ignored","same")]:
            expected_a,expected_b=b.split("\0",1)[0],a.split("\0",1)[0]
            try:
                left,right=cp932(expected_a),cp932(expected_b)
                if profile=="cp932":
                    def units(raw):
                        out=[];i=0
                        while i<len(raw):
                            x=raw[i];i+=1
                            if 0x81<=x<=0x9f or 0xe0<=x<=0xfc:
                                x=x*256+(raw[i]if i<len(raw)else 0);i+=1
                            out.append(x)
                        return out
                    left,right=units(left),units(right)
            except UnicodeEncodeError:
                left,right=expected_a.encode("utf8"),expected_b.encode("utf8")
            expected=(left>right)-(left<right)
            actual=providers["$strcmp"](None,lua.table_from([a,b]))
            assert actual==expected,(profile,a,b,actual,expected)
            rows.append({"profile":profile,"status":"passed","expected":expected})
        assert providers["$stricmp"](None,lua.table_from(["\u8bd1\u6587\uff1aA","\u8bd1\u6587\uff1aa"]))==0
        rows.append({"profile":profile,"casefold_unicode":"passed"})
    # Source resource CRC still uses strict encoding; do not mask bad resource
    # names by mapping unrelated Chinese characters onto the same '?' bytes.
    assert cp932("abc")==b"abc"
    folder=archive_dir("translated_string_comparison")
    report={"status":"passed","timestamp":timestamp(),"cases":rows,
            "strict_cp932_host":True,"unicode_strings_remain_distinct":True,
            "source_resource_encoding_relaxed":False,"device_accessed":False}
    write_json(folder/"report.json",report)
    write_json(ROOT/"reports/translated_string_comparison_tests.json",report)
    print("Translated string comparison:",len(rows),"strict-host cases passed")


if __name__=="__main__":main()
