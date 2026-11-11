"""Compile the real Android checker adapter and exercise unavailable dispatch."""
from __future__ import annotations
import hashlib
from pathlib import Path
import subprocess
import sys
from audit_player_semantics import registered_providers
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,read_json,timestamp,write_json


def run():
    folder=archive_dir("external_checker")
    tools=read_json(ROOT/"reports/android_toolchain.json")
    source=ROOT/"player/android/src/org/majiro/player/ExternalChecker.java"
    java=Path(tools["java"])/"bin/javac.exe"
    android=Path(tools["sdk"])/"platforms/android-36/android.jar"
    process=subprocess.run([str(java),"-encoding","UTF-8","-source","8","-target","8",
        "-bootclasspath",str(android),"-d",str(folder),str(source)],capture_output=True)
    (folder/(folder.name[:8]+"_javac.txt")).write_bytes(process.stdout+process.stderr)
    if process.returncode:raise RuntimeError("ExternalChecker Android compilation failed")
    attempts=[]
    def service(action,value):
        attempts.append((action,value))
        return"missing external disc authenticator: org.majiro.discchecker"
    lua,_,_,system=registered_providers(overrides={"service":service})
    fixture=lua.eval("""
    function(system)
      local vm={thread_id=1,status='running'}
      function vm:fail(message)self.status='blocked';self.problem=message;error(message)end
      local ok,why=pcall(system.providers[0xf430015b],vm,{},true)
      assert(not ok and vm.status=='blocked')
      assert(vm.problem:find('missing external disc authenticator',1,true))
      assert(system.state.external_checker_authenticated==nil)
      assert(vm.external_checker==nil)
      return vm.problem
    end
    """)
    reason=fixture(system)
    assert len(attempts)==1 and attempts[0][0]=="checker_begin"
    report={"status":"passed","timestamp":timestamp(),"entry_hash":"f430015b",
        "dispatch_registered":True,"android_adapter_compiled":True,
        "java_sha256":hashlib.sha256(source.read_bytes()).hexdigest(),
        "host_attempts":attempts,"missing_capability_error":reason,
        "authentication_capability_available":False,
        "scope":"actual Android adapter compilation and real provider's attempted-host/unavailable-capability path",
        "device_execution":False,"authentication_success_claimed":False}
    write_json(folder/(folder.name[:8]+"_external_checker.json"),report)
    write_json(ROOT/"reports/external_checker_tests.json",report)
    print("external checker: Android adapter compiled; attempted-host missing-capability path passed")


if __name__=="__main__":run()
