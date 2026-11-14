"""Publish a validated APK to an existing release and verify downloaded bytes."""
from pathlib import Path
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys

sys.path[:0]=[str(Path(__file__).resolve().parents[1]/part)for part in ("analysis","convert")]
from port_common import ROOT,archive_dir,read_json,sha256,timestamp,write_json
from audit_player_semantics import source_identity


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo",required=True)
    parser.add_argument("--tag",required=True)
    parser.add_argument("--expected-tag",required=True)
    parser.add_argument("--previous-report",type=Path,required=True)
    parser.add_argument("--notes",type=Path,required=True)
    parser.add_argument("--report",type=Path,required=True)
    args=parser.parse_args()
    folder=archive_dir("android_release")
    report={"timestamp":timestamp(),"status":"preparing","evidence":str(folder),
            "repo":args.repo,"tag":args.tag,"device_accessed":False}
    def run(label,command):
        result=subprocess.run(list(map(str,command)),cwd=ROOT,capture_output=True,timeout=180)
        (folder/(label+".txt")).write_bytes(result.stdout+result.stderr)
        if result.returncode:raise RuntimeError(label+" failed: "+str(folder/(label+".txt")))
        return result.stdout.decode("utf8").strip()
    try:
        audit=read_json(ROOT/"reports/emulator_audit_validation.json")
        package=read_json(ROOT/"reports/android_probe_build.json")
        previous=read_json(args.previous_report)
        assert audit["status"]=="passed"and audit["source_identity"]==source_identity()
        assert not audit["source_changed_during_validation"]and not audit["test_sources_changed"]
        assert audit.get("apk",{}).get("sha256")==package["sha256"],"APK was not packaged by the validated audit"
        assert package["application_id"]==previous["application_id"]=="org.majiro.player.recovery"
        assert package["version_name"]=="0.2"and package["version_code"]>previous["version_code"]
        apk=Path(package["apk"]);assert sha256(apk)==package["sha256"]
        assert run("tracked_diff",["git","status","--porcelain","--untracked-files=no"])==""
        commit=run("commit",["git","rev-parse","HEAD"])
        remote=run("remote_refs",["git","ls-remote","origin","refs/heads/main","refs/tags/"+args.tag])
        refs=dict((line.split("\t")[1],line.split("\t")[0])for line in remote.splitlines())
        assert refs["refs/heads/main"]==commit,"push the validated source commit first"
        assert refs["refs/tags/"+args.tag]==args.expected_tag,"release tag changed since review"
        release=json.loads(run("previous_release",["gh","api","repos/"+args.repo+"/releases/tags/"+args.tag]))
        asset=next(value for value in release["assets"]if value["name"]=="Majiro-0.2-arm64-v8a.apk")
        assert asset["digest"]=="sha256:"+previous["apk_sha256"],"published APK changed since review"
        tools=read_json(ROOT/"reports/android_toolchain.json")
        signing=run("signer",[
            Path(tools["java"])/"bin/java.exe","-jar",
            Path(tools["sdk"])/"build-tools/36.0.0/lib/apksigner.jar",
            "verify","--print-certs",apk])
        certificate=re.search(r"Signer #1 certificate SHA-256 digest: ([0-9a-f]+)",signing).group(1)
        assert certificate==previous["signer_sha256"],"update signer changed"
        notes=args.notes.read_text(encoding="utf8")
        assert notes.startswith("Majiro 0.2\n")and "\n- "in notes
        notes_file=folder/"release_notes.md";notes_file.write_text(notes,encoding="utf8")
        public_apk=folder/"Majiro-0.2-arm64-v8a.apk";shutil.copyfile(apk,public_apk)
        checksum=folder/"SHA256SUMS.txt"
        checksum.write_text(package["sha256"]+"  "+public_apk.name+"\n",encoding="ascii")
        run("tag_local",["git","tag","-f","-a",args.tag,"-m","Majiro 0.2",commit])
        run("tag_push",["git","push",
            "--force-with-lease=refs/tags/"+args.tag+":"+args.expected_tag,
            "origin","refs/tags/"+args.tag])
        run("upload",["gh","release","upload",args.tag,str(public_apk),str(checksum),
            "--repo",args.repo,"--clobber"])
        run("notes",["gh","release","edit",args.tag,"--repo",args.repo,
            "--title","Majiro 0.2","--notes-file",notes_file,"--latest"])
        published=json.loads(run("published_release",["gh","api","repos/"+args.repo+"/releases/tags/"+args.tag]))
        assert published["body"].strip()==notes.strip()
        live_assets={value["name"]:value for value in published["assets"]}
        for path in (public_apk,checksum):
            assert live_assets[path.name]["digest"]=="sha256:"+sha256(path)
            assert live_assets[path.name]["size"]==path.stat().st_size
        downloads=folder/"verified-downloads";downloads.mkdir()
        run("download",["gh","release","download",args.tag,"--repo",args.repo,
            "--pattern",public_apk.name,"--pattern",checksum.name,"--dir",downloads])
        for path in (public_apk,checksum):assert sha256(downloads/path.name)==sha256(path)
        for path in (public_apk,checksum):(downloads/path.name).unlink()
        downloads.rmdir()
        remote=run("published_refs",["git","ls-remote","origin","refs/heads/main","refs/tags/"+args.tag+"^{}"])
        assert all(line.split("\t")[0]==commit for line in remote.splitlines())
        report.update(status="published_and_download_verified",completed_at=timestamp(),
            version_name=package["version_name"],version_code=package["version_code"],
            application_id=package["application_id"],commit=commit,apk=str(public_apk),
            apk_sha256=package["sha256"],signer_sha256=certificate,
            signature_matches_previous=True,release_url=published["html_url"],
            notes_file=str(notes_file),suite_count=len(audit["tests"]),
            download_verified=True,temporary_downloads_removed=True)
    except Exception as error:
        report.update(status="failed",error=str(error))
        write_json(args.report,report);write_json(folder/"report.json",report)
        raise
    write_json(args.report,report);write_json(folder/"report.json",report)
    print(json.dumps(report,ensure_ascii=True))
    return 0


if __name__=="__main__":raise SystemExit(main())
