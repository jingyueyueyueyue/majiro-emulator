"""Document real checker launch/handshake and inspect executable resources."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys

import pefile
from original_calls import CallOracle
from native_function import NativeFunctions
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,write_json


def resource_entries(pe,entry,path=()):
    result=[]
    for child in entry.entries:
        name=str(child.name)if child.name else str(child.id)
        key=(*path,name)
        if hasattr(child,"directory"):result+=resource_entries(pe,child.directory,key)
        elif hasattr(child,"data"):
            value=child.data.struct;data=pe.get_data(value.OffsetToData,value.Size)
            result.append({"path":"/".join(key),"bytes":len(data),
                "sha256":hashlib.sha256(data).hexdigest(),"starts_mz":data.startswith(b"MZ"),
                "checker_ascii_offsets":[offset for offset in range(len(data))
                    if data[offset:offset+7].lower()==b"checker"]})
    return result


def run():
    folder=archive_dir("checker_contract")
    samples=json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf8"))["samples"]
    scan=json.loads((ROOT/"reports/checker_sources.json").read_text(encoding="utf8"))
    packages=json.loads((ROOT/"reports/checker_packages.json").read_text(encoding="utf8"))
    paths={Path(sample["path"])for sample in samples}
    for row in scan["samples"]:
        for directory in row["checked_dirs"]:
            if Path(directory).exists():paths.update(Path(directory).glob("*.exe"))
    extracted=[]
    seven=Path("C:/Program Files/7-Zip/7z.exe")
    for package in packages["packages"]:
        if "update"not in Path(package["path"]).name.lower():continue
        for entry in package["executable_or_payload_candidates"]:
            if not entry["Path"].lower().endswith(".exe"):continue
            data=subprocess.check_output([str(seven),"x","-so",package["path"],entry["Path"]],timeout=30)
            path=folder/(folder.name[:8]+"_update_reference.exe")
            path.write_bytes(data);paths.add(path);extracted.append(str(path.relative_to(ROOT)))
    binaries=[]
    for path in sorted(paths):
        try:
            raw=path.read_bytes();pe=pefile.PE(data=raw)
            resources=resource_entries(pe,pe.DIRECTORY_ENTRY_RESOURCE)if hasattr(pe,"DIRECTORY_ENTRY_RESOURCE")else[]
            strings=[text.decode("cp932","replace")for text in raw.split(b"\0")
                if 0<len(text)<500 and b"checker"in text.lower()]
            binaries.append({"path":str(path),"bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest(),
                "checker_strings":strings,"embedded_executable_resources":[r for r in resources if r["starts_mz"]],
                "checker_resources":[r for r in resources if r["checker_ascii_offsets"]],
                "resource_count":len(resources)})
        except Exception as error:binaries.append({"path":str(path),"error":str(error)})
    sample=next(s for s in samples if s["sample"]=="RURINOKASANE")
    native=CallOracle(sample);functions=NativeFunctions(native)
    bodies={}
    for address in(0x467ce0,0x467c50,0x467b50,0x467b20):
        bodies[f"{address:08x}"]=[f"{row.address:08x} {row.mnemonic} {row.op_str}"
            for row in functions.body(address,maximum_bytes=2048)]
    (folder/(folder.name[:8]+"_checker_assembly.txt")).write_text("\n\n".join(
        key+"\n"+"\n".join(body)for key,body in bodies.items()),encoding="utf8")
    report={"schema":1,"timestamp":timestamp(),"reference_exe_sha256":sample["exe_sha256"],
        "binaries":binaries,"extracted_single_file_references":extracted,
        "launch_contract":{"application_name":"checker.exe","command_line":"checker.exe",
            "arguments":[],"current_directory":"inherit game process","creation_flags":0,
            "expected_callback_message":"0x9004","callback_sets":"005b45c4=1",
            "accepted_sender_validation":"none visible in engine callback; checker must perform the actual disc authentication",
            "status_poll":"GetExitCodeProcess until handshake or exit; Sleep(2) and message servicing",
            "failed_launch_or_exit_without_handshake":"show disc authentication error then retry",
            "error_text_cp932_hex":native._input(0x4acf70,-1).value.hex(),
            "error_text":"ディスク認証エラー","error_dialog_flags":"0x40015",
            "check_once_per_process":"005b45c0 set before launch","script_result":0},
        "missing_dependency":{"name":"checker.exe","purpose":"original removable-disc authentication",
            "compatible_android_implementation":"requires the original checker verification algorithm and its disc/license evidence",
            "not_equivalent":["resource parser self-test","synthetic WM_APP reply","unconditional success"],
            "status":"binary/authentication evidence absent from checked sources"},
        "player_entry_implemented":True,"authentication_capability_available":False,
        "android_contract":{"package":"org.majiro.discchecker",
            "action":"org.majiro.player.action.AUTHENTICATE_DISC",
            "request_token":"request_token extra echoed by response",
            "source":"selected game document-tree URI with read grant",
            "required_result":{"result":"Activity.RESULT_OK","native_protocol_message":36868,
                               "disc_authenticated":True},
            "missing_behavior":"query explicit companion; missing application gives a capability error",
            "limits":"real authentication requires an independently supplied compatible checker companion"},
        "evidence":str(folder.relative_to(ROOT))}
    write_json(folder/(folder.name[:8]+"_checker_contract.json"),report)
    write_json(ROOT/"reports/checker_contract.json",report)
    print(len(binaries),"PE files examined;",sum(bool(r.get("embedded_executable_resources"))for r in binaries),
          "files with embedded PE resources")
    print("checker is a disc authenticator; unavailable dependency retained")
    return report


if __name__=="__main__":run()
