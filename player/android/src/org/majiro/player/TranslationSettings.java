package org.majiro.player;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/** User-selected provider settings; API credentials stay encrypted on this device. */
public final class TranslationSettings {
    private static final String ALIAS="majiro.translation.api-key";
    private static volatile TranslationSettings cached;
    private static volatile long cachedRevision=-1;
    public final String mode,url,key,model;
    public final int context;
    private TranslationSettings(String mode,String url,String key,String model,int context) {
        this.mode=mode;this.url=url;this.key=key;this.model=model;this.context=context;
    }
    private static SecretKey secret() throws Exception {
        KeyStore store=KeyStore.getInstance("AndroidKeyStore");store.load(null);
        if(store.containsAlias(ALIAS))return(SecretKey)store.getKey(ALIAS,null);
        KeyGenerator generator=KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES,"AndroidKeyStore");
        generator.init(new KeyGenParameterSpec.Builder(ALIAS,KeyProperties.PURPOSE_ENCRYPT|KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build());
        return generator.generateKey();
    }
    public static TranslationSettings load(Context context) {
        // The library and each fresh :game process share this preference file.
        // Recheck its revision so in-game edits are visible on return.
        java.io.File file=new java.io.File(context.getApplicationInfo().dataDir,"shared_prefs/translation.xml");
        long revision=file.lastModified();
        TranslationSettings ready=cached;if(ready!=null&&revision==cachedRevision)return ready;
        SharedPreferences data=context.getSharedPreferences("translation",Context.MODE_MULTI_PROCESS);
        String key="";
        try {
            String value=data.getString("key","");
            if(!value.isEmpty()) {
                String[] pieces=value.split(":",-1);
                if(pieces.length!=2)throw new IllegalStateException("credential format");
                Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");
                cipher.init(Cipher.DECRYPT_MODE,secret(),new GCMParameterSpec(128,Base64.decode(pieces[0],Base64.NO_WRAP)));
                key=new String(cipher.doFinal(Base64.decode(pieces[1],Base64.NO_WRAP)),StandardCharsets.UTF_8);
            }
        }catch(Exception error){DiagnosticLog.write("translation","stored credential unavailable");}
        String mode=data.getString("mode","auto");
        if(!TextMode.valid(mode))mode="auto";
        ready=new TranslationSettings(mode,data.getString("url",""),key,data.getString("model",""),
            Math.max(2,Math.min(200,data.getInt("context",20))));
        cachedRevision=revision;cached=ready;return ready;
    }
    public static String normalizeUrl(String value) throws Exception {
        String url=value.trim();
        while(url.endsWith("/"))url=url.substring(0,url.length()-1);
        URI uri=new URI(url);
        if(url.length()>4096)throw new IllegalArgumentException("API 地址过长");
        if((!"https".equalsIgnoreCase(uri.getScheme())&&!"http".equalsIgnoreCase(uri.getScheme()))
            ||uri.getHost()==null||uri.getUserInfo()!=null||uri.getFragment()!=null||uri.getQuery()!=null
            ||!uri.getPath().endsWith("/v1"))
            throw new IllegalArgumentException("请输入以 /v1 结尾的完整 API 地址");
        return url;
    }
    public static void save(Context context,String mode,String url,String key,String model,int lines) throws Exception {
        if(!TextMode.valid(mode))throw new IllegalArgumentException("文本模式");
        url=url.trim().isEmpty()?"":normalizeUrl(url);
        model=model.trim();key=key.trim();
        if(model.length()>256||key.length()>8192)throw new IllegalArgumentException("模型或 Key 过长");
        if(key.indexOf('\r')>=0||key.indexOf('\n')>=0)throw new IllegalArgumentException("Key 格式有误");
        if(lines<2||lines>200)throw new IllegalArgumentException("上下文句数需在 2 到 200 之间");
        if(mode.equals("online")&&(url.isEmpty()||model.isEmpty()))throw new IllegalArgumentException("请填写 API 地址并选择模型");
        String encrypted="";
        if(!key.isEmpty()) {
            Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.ENCRYPT_MODE,secret());
            encrypted=Base64.encodeToString(cipher.getIV(),Base64.NO_WRAP)+":"+
                Base64.encodeToString(cipher.doFinal(key.getBytes(StandardCharsets.UTF_8)),Base64.NO_WRAP);
        }
        if(!context.getSharedPreferences("translation",0).edit().putString("mode",mode).putString("url",url)
            .putString("key",encrypted).putString("model",model).putInt("context",lines).commit())
            throw new IllegalStateException("设置保存失败");
        cached=new TranslationSettings(mode,url,key,model,lines);
        cachedRevision=new java.io.File(context.getApplicationInfo().dataDir,
            "shared_prefs/translation.xml").lastModified();
    }
}
