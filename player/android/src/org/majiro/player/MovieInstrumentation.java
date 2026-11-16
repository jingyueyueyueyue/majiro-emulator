package org.majiro.player;

import android.app.Activity;
import android.app.Instrumentation;
import android.net.Uri;
import android.os.Bundle;
import android.os.SystemClock;

/** Local decoder/readback measurement using the target app's existing document grant. */
public final class MovieInstrumentation extends Instrumentation {
    private Bundle arguments;
    @Override public void onCreate(Bundle arguments) {
        this.arguments=arguments;
        super.onCreate(arguments);start();
    }
    @Override public void onStart() {
        Bundle result=new Bundle();
        try {
            Uri uri=Uri.parse(arguments.getString("movie_uri",""));
            if(!"content".equals(uri.getScheme())&&!"file".equals(uri.getScheme()))
                throw new IllegalArgumentException("local movie URI required");
            long started=SystemClock.elapsedRealtime();
            MovieTextures.play(getTargetContext(),uri,false,0,-1,0);
            result.putLong("first_frame_ms",SystemClock.elapsedRealtime()-started);
            long began=SystemClock.elapsedRealtime();
            int first=-1,last=-1,observed=0,width=0,height=0;
            while(SystemClock.elapsedRealtime()-began<5000&&MovieTextures.status()!=0){
                int[] frame=MovieTextures.frame();
                if(frame!=null&&frame[2]!=last){
                    if(first<0)first=frame[2];
                    last=frame[2];width=frame[0];height=frame[1];++observed;
                }
                SystemClock.sleep(10);
            }
            long elapsed=SystemClock.elapsedRealtime()-began;
            if(first<0||last<=first)throw new AssertionError("movie frame stream did not advance");
            result.putInt("decoded_frames",last-first);
            result.putInt("observed_frames",observed);
            result.putInt("width",width);result.putInt("height",height);
            result.putLong("sample_ms",elapsed);
            result.putString("stream","Movie decoder/readback: "+
                ((last-first)*1000.0/Math.max(1,elapsed))+" fps\n");
            MovieTextures.stopAndWait();
            finish(Activity.RESULT_OK,result);
        }catch(Throwable error){
            try{MovieTextures.stopAndWait();}catch(Throwable cleanup){error.addSuppressed(cleanup);}
            result.putString("stream","Movie performance FAILED: "+error+"\n");
            finish(Activity.RESULT_CANCELED,result);
        }
    }
}
