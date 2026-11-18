"""Check the public TyranorNext runtime module contract without starting an app."""
from __future__ import annotations

import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ANDROID_NS = "http://schemas.android.com/apk/res/android"
ACTION = "com.tyranor.runtime.action.LAUNCH"
PACKAGE = "com.tyranor.runtime.majiro"
sys.path.insert(0,str(ROOT/"scripts/convert"))
from port_common import archive_dir,read_json
from test_audio_preparation_cleanup import method


def main() -> int:
    manifest = ET.parse(ROOT / "player/android/AndroidManifest.xml").getroot()
    main_activity = next(
        activity
        for activity in manifest.findall("./application/activity")
        if activity.get(f"{{{ANDROID_NS}}}name") == "org.majiro.player.MainActivity"
    )
    launch_data = {
        tuple(data.get(f"{{{ANDROID_NS}}}scheme") for data in intent_filter.findall("data"))
        for intent_filter in main_activity.findall("intent-filter")
        if any(action.get(f"{{{ANDROID_NS}}}name") == ACTION for action in intent_filter.findall("action"))
        and any(category.get(f"{{{ANDROID_NS}}}name") == "android.intent.category.DEFAULT"
                for category in intent_filter.findall("category"))
    }
    if () not in launch_data or ("content",) not in launch_data:
        raise AssertionError("MainActivity needs separate filters for discovery/path launches and SAF launches")
    if main_activity.get(f"{{{ANDROID_NS}}}exported") != "true":
        raise AssertionError("MainActivity must be exported for external launch")

    descriptor = json.loads(
        (ROOT / "player/android/res/raw/tyranor_runtime.json").read_text(
            encoding="utf-8",
        ),
    )
    scan = descriptor.get("scan", {})
    if set(scan.get("extensions", ())) != {".mjo", ".arc"}:
        raise AssertionError("runtime scan descriptor must expose Majiro archive extensions")
    if "majiro.env" not in scan.get("markers", ()):
        raise AssertionError("runtime scan descriptor must keep the majiro.env marker")

    source = (ROOT / "player/android/src/org/majiro/player/MainActivity.java").read_text(
        encoding="utf-8",
    )
    for marker in (
        "ExternalLaunchContract.ACTION_LAUNCH",
        "EXTRA_PROTOCOL_VERSION",
        "EXTRA_FOLDER_PATH",
        "EXTRA_GAME_ROOT",
        "getData",
        "FLAG_GRANT_READ_URI_PERMISSION",
        "setData",
        "game_tree",
        "game_root",
        "game_path",
        "getCanonicalFile",
        "ExternalLaunchTree.find(this, directory)",
        "setGameTree(game, existing.uri, existing.documentId)",
        "GameActivity.class",
    ):
        if marker not in source:
            raise AssertionError(f"external launch source contract is missing {marker}")

    contract = (
        ROOT / "player/android/src/org/majiro/player/ExternalLaunchContract.java"
    ).read_text(encoding="utf-8")
    for marker in (
        f'"{ACTION}"',
        '"com.tyranor.runtime.extra.FOLDER_PATH"',
        '"com.tyranor.runtime.extra.GAME_ROOT"',
        '"com.tyranor.runtime.extra.GAME_TITLE"',
        '"com.tyranor.runtime.extra.PROTOCOL_VERSION"',
        "PROTOCOL_VERSION = 1",
    ):
        if marker not in contract:
            raise AssertionError(f"external launch protocol is missing {marker}")

    folder=archive_dir("diagnostic_launch")
    body=method(source,"private void forwardDiagnostics(")
    tree_source=(ROOT/"player/android/src/org/majiro/player/ExternalLaunchTree.java").read_text(encoding="utf8")
    tree_methods="\n".join(method(tree_source,signature) for signature in (
        "static ExternalLaunchTree find(", "private static File volumeRoot(", "private static String documentFor("))
    fixture=folder/"LaunchChecks.java"
    fixture.write_text("""
import java.io.*;
import java.util.*;
public class LaunchChecks {
 static class Intent {
  final Map<String,String> extras=new HashMap<>();
  String getStringExtra(String name){return extras.get(name);}
  void putExtra(String name,String value){extras.put(name,value);}
 }
 static class Info{int flags=2;}
 Info info=new Info();Intent incoming=new Intent();
 Info getApplicationInfo(){return info;}Intent getIntent(){return incoming;}
 // METHOD
 public static void main(String[] args) throws IOException {
  LaunchChecks owner=new LaunchChecks();Intent game=new Intent();
  owner.incoming.putExtra("diagnostic_save_namespace","test-isolated");
  owner.incoming.putExtra("diagnostic_mode","original");owner.forwardDiagnostics(game);
  if(!game.extras.equals(owner.incoming.extras))throw new AssertionError("diagnostic handoff lost");
  owner.info.flags=0;game=new Intent();owner.forwardDiagnostics(game);
  if(!game.extras.isEmpty())throw new AssertionError("release accepted diagnostics");
  owner.info.flags=2;owner.incoming.putExtra("diagnostic_save_namespace","../games");
  try{owner.forwardDiagnostics(new Intent());throw new AssertionError("invalid namespace");}
  catch(IllegalArgumentException expected){}
  owner.incoming.extras.clear();owner.incoming.putExtra("diagnostic_mode","invalid");
  game=new Intent();owner.forwardDiagnostics(game);
  if(!game.extras.isEmpty())throw new AssertionError("invalid mode forwarded");
  System.out.println("Diagnostic launch: 4 behavior checks passed");
  TreeChecks.run();
 }
}

final class TreeChecks {
 static class Build { static class VERSION { static int SDK_INT=36; } }
 static class Environment {
  static File primary;
  static File getExternalStorageDirectory(){return primary;}
 }
 record StorageVolume(String uuid,File directory) {
  String getUuid(){return uuid;}
  File getDirectory(){return directory;}
 }
 static class StorageManager {
  List<StorageVolume> volumes=new ArrayList<>();
  List<StorageVolume> getStorageVolumes(){return volumes;}
 }
 record Uri(String authority,String tree,String document) {
  String getScheme(){return "content";}
  String getAuthority(){return authority;}
 }
 record UriPermission(Uri uri,boolean read) {
  Uri getUri(){return uri;}
  boolean isReadPermission(){return read;}
 }
 record Cursor(String mime) implements AutoCloseable {
  boolean moveToFirst(){return mime!=null;}
  String getString(int column){return mime;}
  public void close(){}
 }
 static class DocumentsContract {
  static class Document {
   static final String COLUMN_MIME_TYPE="mime_type",MIME_TYPE_DIR="directory";
  }
  static boolean isTreeUri(Uri uri){return uri.tree()!=null;}
  static String getTreeDocumentId(Uri uri){return uri.tree();}
  static Uri buildDocumentUriUsingTree(Uri uri,String document){return new Uri(uri.authority(),uri.tree(),document);}
 }
 static class ContentResolver {
  List<UriPermission> grants=new ArrayList<>();
  List<Uri> checked=new ArrayList<>();
  String denied,mime=DocumentsContract.Document.MIME_TYPE_DIR;
  List<UriPermission> getPersistedUriPermissions(){return grants;}
  Cursor query(Uri uri,String[] projection,Object selection,Object arguments,Object sort){
   checked.add(uri);
   if(uri.tree().equals(denied))throw new SecurityException("expired grant");
   return new Cursor(mime);
  }
 }
 static class Context {
  ContentResolver resolver=new ContentResolver();
  StorageManager storage=new StorageManager();
  ContentResolver getContentResolver(){return resolver;}
  <T> T getSystemService(Class<T> type){return type.cast(storage);}
 }
 record ExternalLaunchTree(Uri uri,String documentId) {
  private static final String AUTHORITY="com.android.externalstorage.documents";
  // TREE_METHODS
 }
 static UriPermission grant(String id,boolean read){
  return new UriPermission(new Uri(ExternalLaunchTree.AUTHORITY,id,null),read);
 }
 static void expect(boolean ok,String message){if(!ok)throw new AssertionError(message);}
 static void run() throws IOException {
  File workspace=new File(".").getCanonicalFile();
  Environment.primary=new File(workspace,"primary");
  String folder="Download/MajiroGames/[131129] \\u4e59\\u5973";
  String document="primary:"+folder;
  File game=new File(Environment.primary,folder).getCanonicalFile();
  Context context=new Context();
  ContentResolver resolver=context.resolver;
  String parent="primary:Download/MajiroGames";
  resolver.grants.add(grant(document,false));
  resolver.grants.add(new UriPermission(new Uri("other.provider",parent,null),true));
  resolver.grants.add(grant(parent,true));
  ExternalLaunchTree match=ExternalLaunchTree.find(context,game);
  expect(match!=null && match.documentId.equals(document),"wrong game SAF document ID");
  expect(match.uri.tree().equals(parent) && resolver.checked.size()==1,"used non-readable or foreign grant");
  expect(ExternalLaunchTree.find(context,new File(Environment.primary,"Download/MajiroGamesBackup/game"))==null
         && resolver.checked.size()==1,"matched an unrelated sibling directory");
  expect(ExternalLaunchTree.find(context,new File(workspace,"primary2/"+folder))==null
         && resolver.checked.size()==1,"matched an unrelated volume");
  resolver.grants.add(grant(document,true));resolver.denied=parent;
  match=ExternalLaunchTree.find(context,game);
  expect(match!=null && match.uri.tree().equals(document),"stale parent hid valid exact grant");
  resolver.mime="application/octet-stream";
  expect(ExternalLaunchTree.find(context,game)==null,"accepted a non-directory document");
  resolver.grants.clear();
  expect(ExternalLaunchTree.find(context,game)==null,"invented a grant for an unauthorized directory");
  File sd=new File(workspace,"sd");
  context.storage.volumes.add(new StorageVolume("1234-5678",sd));
  resolver.grants.add(grant("1234-5678:Games",true));
  resolver.mime=DocumentsContract.Document.MIME_TYPE_DIR;
  match=ExternalLaunchTree.find(context,new File(sd,"Games/Title"));
  expect(match!=null && match.documentId.equals("1234-5678:Games/Title"),"removable volume not resolved");
  System.out.println("Persisted launch access: 8 behavior checks passed");
 }
}""".replace("// METHOD",body).replace("// TREE_METHODS",tree_methods),encoding="utf8")
    java=Path(read_json(ROOT/"reports/android_toolchain.json")["java"])/"bin"
    for label,command in (
        ("compile",[str(java/"javac.exe"),"-encoding","UTF-8","-d",str(folder),str(fixture)]),
        ("run",[str(java/"java.exe"),"-cp",str(folder),"LaunchChecks"])):
        result=subprocess.run(command,capture_output=True,cwd=folder,timeout=30)
        (folder/(label+".txt")).write_bytes(result.stdout+result.stderr)
        if result.returncode:raise RuntimeError(label+" diagnostic launch failed: "+str(folder))
    print(f"Android external launch contract passed: {PACKAGE} / {ACTION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
