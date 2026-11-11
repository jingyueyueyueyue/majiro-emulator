"""Exercise production translation HTTP/cache logic against a local mock, without keys or a tablet."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,read_json,sha256,timestamp,write_json

SOURCES={
"android/content/Context.java":r"""
package android.content;
import java.io.File;
public final class Context {
 private final File root;public Context(File root){this.root=root;root.mkdirs();}
 public File getFilesDir(){return root;}public Context getApplicationContext(){return this;}
}
""",
"android/os/SystemClock.java":r"""
package android.os;public final class SystemClock {
 public static long elapsedRealtime(){return System.nanoTime()/1000000;}
}
""",
"org/majiro/player/TranslationSettings.java":r"""
package org.majiro.player;
import android.content.Context;
public final class TranslationSettings{
 public static TranslationSettings current=new TranslationSettings();
 public String mode="online",url="",key="fixture-private-key",model="fixture-model";public int context=20;
 public static TranslationSettings load(Context c){return current;}
 public static String normalizeUrl(String u){return u;}
}
""",
"org/majiro/player/DiagnosticLog.java":r"""
package org.majiro.player;public final class DiagnosticLog{
 public static String messages="";public static void write(String a,String b){messages+=a+" "+b+"\n";}
}
""",
"org/majiro/player/TranslationFixture.java":r"""
package org.majiro.player;
import android.content.Context;
import org.json.*;import java.io.*;import java.net.*;import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicInteger;
import com.sun.net.httpserver.HttpServer;
public final class TranslationFixture{
 static int checks;static AtomicInteger calls=new AtomicInteger();static volatile String behavior="ok";
 static void check(boolean value,String why){checks++;if(!value)throw new AssertionError(why);}
 static String read(InputStream in)throws Exception{ByteArrayOutputStream b=new ByteArrayOutputStream();byte[] x=new byte[4096];int n;while((n=in.read(x))>0)b.write(x,0,n);return b.toString("UTF-8");}
 static String query(String id,String text)throws Exception{return new JSONObject().put("game","script-fingerprint")
  .put("lines",new JSONArray().put(new JSONObject().put("id",id).put("text",text))).put("context",new JSONArray().put("reference")).toString();}
 static String lookup(Context c,String id)throws Exception{return TranslationService.call(c,"lookup",new JSONObject().put("game","script-fingerprint").put("id",id).toString());}
 static void await(Context c,String id,boolean hit)throws Exception{
  long end=System.currentTimeMillis()+6000;
  while(System.currentTimeMillis()<end){if(!lookup(c,id).isEmpty()==hit&&(!hit||!TranslationService.call(c,"status","").contains("正在")))return;Thread.sleep(10);}
  throw new AssertionError("cache timeout "+id);
 }
 static void idle()throws Exception{Thread.sleep(80);long end=System.currentTimeMillis()+6000;
  while(System.currentTimeMillis()<end){if(!TranslationService.call(new Context(new File(".")),"status","").contains("正在"))return;Thread.sleep(10);}throw new AssertionError("worker timeout");}
 static void retry()throws Exception{java.lang.reflect.Field f=TranslationService.class.getDeclaredField("retryAfter");f.setAccessible(true);f.setLong(null,0);}
 public static void main(String[] args)throws Exception{
  HttpServer server=HttpServer.create(new InetSocketAddress("127.0.0.1",0),0);
  server.createContext("/v1/",exchange->{try{
   calls.incrementAndGet();String authorization=exchange.getRequestHeaders().getFirst("Authorization");
   check("Bearer fixture-private-key".equals(authorization),"bearer header");
   String response;int status=200;
   if(exchange.getRequestURI().getPath().endsWith("/models")){
    response="{\"data\":[{\"id\":\"z\"},{\"id\":\"a\"},{\"id\":\"a\"}]}";
   }else{
    JSONObject body=new JSONObject(read(exchange.getRequestBody()));
    check(body.getString("model").equals("fixture-model"),"selected model");
    check(!body.getBoolean("stream"),"non-stream transport");
    JSONArray messages=body.getJSONArray("messages");
    check(messages.getJSONObject(0).getString("role").equals("system"),"instruction isolation");
    JSONObject payload=new JSONObject(messages.getJSONObject(1).getString("content"));
    check(payload.getJSONArray("context").getString(0).equals("reference"),"context reference");
    JSONArray lines=payload.getJSONArray("lines"),values=new JSONArray();
    for(int i=0;i<lines.length();++i)values.put(new JSONObject().put("id",lines.getJSONObject(i).getString("id")).put("text","\u4f60\u597d"));
    if(behavior.equals("bad"))values.getJSONObject(0).put("id","unknown");
    if(behavior.equals("http")){status=429;response="fixture-private-key SHOULD NEVER BE LOGGED";}
    else if(behavior.equals("redirect")){status=302;exchange.getResponseHeaders().add("Location","/v1/leak");response="";}
    else response=new JSONObject().put("choices",new JSONArray().put(new JSONObject().put("message",new JSONObject()
      .put("content","```json\n"+new JSONObject().put("translations",values).toString()+"\n```")))).toString();
   }
   byte[] data=response.getBytes(StandardCharsets.UTF_8);exchange.sendResponseHeaders(status,data.length);
   try(OutputStream output=exchange.getResponseBody()){output.write(data);}
  }catch(Throwable error){error.printStackTrace();exchange.close();}});
  server.start();TranslationSettings.current.url="http://127.0.0.1:"+server.getAddress().getPort()+"/v1";
  Context context=new Context(new File(args[0]));
  check(TranslationService.models(TranslationSettings.current.url,TranslationSettings.current.key).toString().equals("[a, z]"),"model dedup and sorting");
  JSONObject config=new JSONObject(TranslationService.call(context,"config",""));check(config.getInt("context")==20,"context option");
  check(TranslationService.call(context,"submit",query("line-1","\u3053\u3093\u306b\u3061\u306f")).equals("queued"),"background queue");
  check(TranslationService.call(context,"submit",query("line-1","\u3053\u3093\u306b\u3061\u306f")).equals("cached"),"duplicate dedup");
  await(context,"line-1",true);check(lookup(context,"line-1").equals("\u4f60\u597d"),"UTF-8 output");
  int before=calls.get();check(TranslationService.call(context,"submit",query("line-1","source")).equals("cached"),"warm cache");check(calls.get()==before,"no cached network");
  java.lang.reflect.Field memory=TranslationService.class.getDeclaredField("memory");memory.setAccessible(true);((java.util.Map)memory.get(null)).clear();
  check(lookup(context,"line-1").equals("\u4f60\u597d"),"persistent cache");
  TranslationSettings.current.model="another-model";check(lookup(context,"line-1").isEmpty(),"provider/model isolation");TranslationSettings.current.model="fixture-model";
  behavior="bad";TranslationService.call(context,"submit",query("line-bad","source"));idle();check(lookup(context,"line-bad").isEmpty(),"unknown output id rejected");retry();
  behavior="http";TranslationService.call(context,"submit",query("line-error","source"));idle();check(lookup(context,"line-error").isEmpty(),"HTTP failure fallback");check(!DiagnosticLog.messages.contains("fixture-private-key"),"secret never logged");retry();
  behavior="redirect";before=calls.get();TranslationService.call(context,"submit",query("line-redirect","source"));idle();check(calls.get()==before+1,"no redirect authorization leak");retry();
  TranslationSettings.current.mode="original";before=calls.get();check(TranslationService.call(context,"submit",query("off","source")).isEmpty(),"original mode disabled");check(calls.get()==before,"original mode sends nothing");
  System.out.println("{\"status\":\"passed\",\"checks\":"+checks+",\"requests\":"+calls.get()+"}");server.stop(0);System.exit(0);
 }
}
""",
}


def main():
    evidence=archive_dir("translation_service_tests");sources=evidence/"src";classes=evidence/"classes";classes.mkdir()
    java=Path(read_json(ROOT/"reports/android_toolchain.json")["java"])/"bin"
    jar=ROOT/"references/player/testing-json/json-20240303.jar"
    if sha256(jar)!="3cf6cd6892e32e2b4c1c39e0f52f5248a2f5b37646fdfbb79a66b46b618414ed":
        raise ValueError("fixture-only JSON library hash changed")
    paths=[]
    for name,text in SOURCES.items():
        path=sources/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding="utf8");paths.append(path)
    production=ROOT/"player/android/src/org/majiro/player/TranslationService.java"
    def run(name,arguments):
        result=subprocess.run(arguments,capture_output=True,timeout=45)
        (evidence/(name+".txt")).write_bytes(result.stdout+result.stderr)
        if result.returncode:raise RuntimeError(name+" failed: "+str(evidence/(name+".txt")))
        return result
    run("compile",[str(java/"javac.exe"),"-encoding","UTF-8","-cp",str(jar),"-d",str(classes),str(production),*map(str,paths)])
    import os
    output=run("run",[str(java/"java.exe"),"-cp",os.pathsep.join(map(str,(classes,jar))),"org.majiro.player.TranslationFixture",str(evidence/"data")])
    report=json.loads(output.stdout.decode("utf8").strip())
    report.update(timestamp=timestamp(),production_sha256=sha256(production),evidence=str(evidence.relative_to(ROOT)),
        external_api_called=False,tablet_accessed=False,real_credentials_used=False,
        boundaries=["Android Keystore and UI compiled separately; HTTP/cache/queue class is unchanged production source"])
    write_json(ROOT/"reports/translation_service_tests.json",report)
    print(json.dumps(report,ensure_ascii=False))
    return 0


if __name__=="__main__":raise SystemExit(main())
