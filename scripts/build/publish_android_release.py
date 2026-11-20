"""Publish a validated APK and verify downloaded bytes against the local build."""
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
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--expected-tag",help="replace an existing release at this tag object")
    mode.add_argument("--new-release",action="store_true",help="publish a new version and keep previous releases")
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
        version_name=package["version_name"]
        previous_tag=previous["release_url"].rstrip("/").rsplit("/",1)[-1]
        assert audit["status"]=="passed"and audit["source_identity"]==source_identity()
        assert not audit["source_changed_during_validation"]and not audit["test_sources_changed"]
        assert audit.get("apk",{}).get("sha256")==package["sha256"],"APK was not packaged by the validated audit"
        assert package["application_id"]==previous["application_id"]
        assert package["application_id"] in (
            "com.tyranor.runtime.majiro","com.tyranor.runtime.majiro.recovery")
        assert package["version_name"]==version_name and package["version_code"]>previous["version_code"]
        apk=Path(package["apk"]);assert sha256(apk)==package["sha256"]
        assert run("tracked_diff",["git","status","--porcelain","--untracked-files=no"])==""
        commit=run("commit",["git","rev-parse","HEAD"])
        remote=run("remote_refs",["git","ls-remote","origin","refs/heads/main","refs/tags/"+args.tag])
        refs=dict((line.split("\t")[1],line.split("\t")[0])for line in remote.splitlines())
        assert refs["refs/heads/main"]==commit,"push the validated source commit first"
        if args.new_release:
            assert args.tag=="v"+version_name and args.tag!=previous_tag,"new version/tag mismatch"
            assert "refs/tags/"+args.tag not in refs,"new release tag already exists"
        else:
            assert refs["refs/tags/"+args.tag]==args.expected_tag,"release tag changed since review"
        release=json.loads(run("previous_release",["gh","api","repos/"+args.repo+"/releases/tags/"+previous_tag]))
        previous_asset_name="Majiro-"+previous["version_name"]+"-arm64-v8a.apk"
        asset=next(value for value in release["assets"]if value["name"]==previous_asset_name)
        assert asset["digest"]=="sha256:"+previous["apk_sha256"],"published APK changed since review"
        tools=read_json(ROOT/"reports/android_toolchain.json")
        signing=run("signer",[
            Path(tools["java"])/"bin/java.exe","-jar",
            Path(tools["sdk"])/"build-tools/36.0.0/lib/apksigner.jar",
            "verify","--print-certs",apk])
        certificate=re.search(r"Signer #1 certificate SHA-256 digest: ([0-9a-f]+)",signing).group(1)
        assert certificate==previous["signer_sha256"],"update signer changed"
        notes=args.notes.read_text(encoding="utf8")
        assert notes.startswith("Majiro "+version_name+"\n")and "\n- "in notes
        notes_file=folder/"release_notes.md";notes_file.write_text(notes,encoding="utf8",newline="\n")
        public_apk=folder/("Majiro-"+version_name+"-arm64-v8a.apk");shutil.copyfile(apk,public_apk)
        checksum=folder/"SHA256SUMS.txt"
        checksum.write_text(package["sha256"]+"  "+public_apk.name+"\n",encoding="ascii",newline="\n")
        if args.new_release:
            run("publish",["gh","release","create",args.tag,str(public_apk),str(checksum),
                "--target",commit,"--repo",args.repo,"--title","Majiro "+version_name,
                "--notes-file",notes_file,"--latest"])
        else:
            run("tag_local",["git","tag","-f","-a",args.tag,"-m","Majiro "+version_name,commit])
            run("tag_push",["git","push",
                "--force-with-lease=refs/tags/"+args.tag+":"+args.expected_tag,
                "origin","refs/tags/"+args.tag])
            run("upload",["gh","release","upload",args.tag,str(public_apk),str(checksum),
                "--repo",args.repo,"--clobber"])
            run("notes",["gh","release","edit",args.tag,"--repo",args.repo,
                "--title","Majiro "+version_name,"--notes-file",notes_file,"--latest"])
        published=json.loads(run("published_release",["gh","api","repos/"+args.repo+"/releases/tags/"+args.tag]))
        assert published["body"].replace("\r\n","\n").strip()==notes.replace("\r\n","\n").strip()
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
        tag_ref="refs/tags/"+args.tag
        remote=run("published_refs",["git","ls-remote","origin","refs/heads/main",tag_ref,tag_ref+"^{}"])
        refs={ref:digest for digest,ref in (line.split("\t") for line in remote.splitlines())}
        assert refs["refs/heads/main"]==commit and refs.get(tag_ref+"^{}",refs[tag_ref])==commit
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
