package org.majiro.player;

import android.content.Context;
import android.graphics.SurfaceTexture;
import android.net.Uri;
import android.opengl.EGL14;
import android.opengl.EGLConfig;
import android.opengl.EGLContext;
import android.opengl.EGLDisplay;
import android.opengl.EGLSurface;
import android.opengl.GLES11Ext;
import android.opengl.GLES20;
import android.os.Handler;
import android.os.HandlerThread;
import android.view.Surface;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.FloatBuffer;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import tv.danmaku.ijk.media.player.IjkMediaPlayer;
import tv.danmaku.ijk.media.player.IMediaPlayer;

/** IJK -> external texture -> EGL RGBA readback on one private GL thread.
 * The immutable published pixels enter the same Majiro pages as ordinary
 * images, allowing grp_copy/sprites to sample a real decoded movie frame.
 */
public final class MovieTextures {
    private static final MediaSessionOwner<Session> sessions=new MediaSessionOwner<Session>();
    private static volatile boolean background;
    private MovieTextures() {}

    public static int play(Context context, Uri uri, boolean loop,
                                         long from, long to, int volume) throws Exception {
        if (from < 0 || (to >= 0 && to <= from))
            throw new IllegalArgumentException("invalid movie range");
        Session next = new Session(context, uri, loop, from, to, volume);
        return sessions.play(next)?1:0;
    }
    public static int status() throws Exception {
        Session current=sessions.current();
        if (current == null) return 0;
        current.check();
        return current.finished ? 0 : 1;
    }
    public static int width() { Session current=sessions.current();return current == null ? 0 : current.width; }
    public static int height() { Session current=sessions.current();return current == null ? 0 : current.height; }
    public static int[] frame() throws Exception {
        Session current=sessions.current();
        if (current == null) return null;
        current.check();
        return current.pixels;
    }
    public static void volume(int value) {
        Session current=sessions.current();
        if (current != null) current.volume(value);
    }
    public static void pause() { background=true;Session current=sessions.current();if(current!=null)current.pause(true); }
    public static void resume() { background=false;Session current=sessions.current();if(current!=null)current.pause(false); }
    public static void stop() {sessions.stop();}
    public static void stopAndWait() throws Exception {
        if(!sessions.stopAndAwait(5000))throw new IOException("movie cleanup timed out");
    }

    private static final class Session implements MediaSessionOwner.Resource {
        final Context context;
        final Uri uri;
        final boolean repeat;
        final long from, requestedEnd;
        final HandlerThread thread = new HandlerThread("majiro-movie-rgba");
        final CountDownLatch firstFrame = new CountDownLatch(1);
        final CountDownLatch released = new CountDownLatch(1);
        Handler handler;
        IjkMediaPlayer player;
        SurfaceTexture texture;
        Surface surface;
        EGLDisplay display = EGL14.EGL_NO_DISPLAY;
        EGLContext glContext = EGL14.EGL_NO_CONTEXT;
        EGLSurface pbuffer = EGL14.EGL_NO_SURFACE;
        EGLConfig config;
        int textureId, program, sequence, gain;
        volatile int width, height;
        volatile int[] pixels;
        volatile boolean closed, finished, suspended;
        volatile String error;
        long end;
        boolean seeking, prepared;
        ByteBuffer rgba;
        final float[] transform = new float[16];
        final FloatBuffer quad = ByteBuffer.allocateDirect(16*4).order(ByteOrder.nativeOrder())
            .asFloatBuffer();

        Session(Context context, Uri uri, boolean repeat, long from, long to, int gain) {
            this.context=context; this.uri=uri; this.repeat=repeat;
            this.from=from; this.requestedEnd=to; this.gain=gain;
            quad.put(new float[]{-1,-1,0,0, 1,-1,1,0, -1,1,0,1, 1,1,1,1}).position(0);
        }
        void check() throws IOException { if (error != null) throw new IOException(error); }
        void fail(String why) {
            error=why; finished=true; firstFrame.countDown();
        }
        @Override public void open() throws Exception {
            synchronized(this) {
                if(closed)return;
                suspended=background;
                thread.start();
                handler=new Handler(thread.getLooper());
            }
            handler.post(new Runnable() { @Override public void run() {
                if(closed)return;
                try {
                    initializeGl();
                    player=IjkVideo.open(context,uri);
                    final IjkMediaPlayer owner=player;
                    player.setSurface(surface);
                    player.setVolume(level(gain),level(gain));
                    player.setOnPreparedListener(new IMediaPlayer.OnPreparedListener() {
                        @Override public void onPrepared(IMediaPlayer ready) {
                            if (closed || owner!=player) return;
                            try {
                                long duration=owner.getDuration();
                                end=requestedEnd<0?duration:Math.min(requestedEnd,duration);
                                if(duration<=0 || from>=end)throw new IOException("movie range outside duration");
                                resize(owner.getVideoWidth(),owner.getVideoHeight());
                                prepared=true;
                                if(from>0)seekStart();else if(!suspended)owner.start();
                                handler.post(rangePoll);
                            } catch(Exception failure) { fail("movie prepare: "+failure); }
                        }
                    });
                    player.setOnSeekCompleteListener(new IMediaPlayer.OnSeekCompleteListener() {
                        @Override public void onSeekComplete(IMediaPlayer completed) {
                            if(closed || owner!=player)return;
                            try{
                                seeking=false;
                                if(!suspended && !finished && prepared)owner.start();
                            }catch(RuntimeException failure){fail("movie seek completion: "+failure);}
                        }
                    });
                    player.setOnCompletionListener(new IMediaPlayer.OnCompletionListener() {
                        @Override public void onCompletion(IMediaPlayer completed) {
                            if(closed || owner!=player)return;
                            try{
                                if(pixels==null){fail("movie decoder completed without a frame");return;}
                                if(repeat)seekStart();else finished=true;
                            }catch(RuntimeException failure){fail("movie completion: "+failure);}
                        }
                    });
                    player.setOnErrorListener(new IMediaPlayer.OnErrorListener() {
                        @Override public boolean onError(IMediaPlayer failed,int what,int extra) {
                            if(!closed && owner==player)fail("IJK movie decode "+what+"/"+extra);
                            return true;
                        }
                    });
                    player.prepareAsync();
                } catch(Exception failure) { fail("movie startup: "+failure); }
            }});
            int activeWait=0;
            while(!firstFrame.await(100,TimeUnit.MILLISECONDS)) {
                if(closed)return;
                if(!suspended)activeWait+=100;
                if(activeWait>=30000)throw new IOException("movie first-frame timeout");
            }
            if(closed)return;
            check();
            if(pixels==null)throw new IOException("movie decoder produced no RGBA frame");
        }
        final Runnable rangePoll = new Runnable() { @Override public void run() {
            if(closed || finished || error!=null)return;
            try {
                if(!suspended && !seeking && player!=null && player.getCurrentPosition()>=end) {
                    if(repeat)seekStart();else {player.pause();finished=true;return;}
                }
                handler.postDelayed(this,15);
            } catch(RuntimeException failure) {fail("movie range poll: "+failure);}
        }};
        void seekStart() {
            if(closed || player==null)return;
            seeking=true;
            player.seekTo(from);
        }
        static float level(int value) { return Math.max(0,Math.min(1000,value))/1000f; }
        void volume(final int value) {
            gain=value;
            if(handler!=null)handler.post(new Runnable(){@Override public void run(){
                if(!closed && player!=null)player.setVolume(level(value),level(value));
            }});
        }
        void pause(final boolean value) {
            suspended=value;
            if(handler!=null)handler.post(new Runnable(){@Override public void run(){
                if(closed || player==null || finished || !prepared)return;
                try{
                    if(suspended){if(player.isPlaying())player.pause();}
                    else if(!seeking)player.start();
                }catch(RuntimeException failure){fail("movie lifecycle: "+failure);}
            }});
        }
        int shader(int type,String source) throws IOException {
            int value=GLES20.glCreateShader(type);
            GLES20.glShaderSource(value,source);GLES20.glCompileShader(value);
            int[] ok=new int[1];GLES20.glGetShaderiv(value,GLES20.GL_COMPILE_STATUS,ok,0);
            if(ok[0]==0)throw new IOException("movie shader: "+GLES20.glGetShaderInfoLog(value));
            return value;
        }
        void initializeGl() throws Exception {
            display=EGL14.eglGetDisplay(EGL14.EGL_DEFAULT_DISPLAY);
            int[] version=new int[2];
            if(!EGL14.eglInitialize(display,version,0,version,1))throw new IOException("movie EGL initialize");
            int[] attributes={EGL14.EGL_RED_SIZE,8,EGL14.EGL_GREEN_SIZE,8,EGL14.EGL_BLUE_SIZE,8,
                EGL14.EGL_ALPHA_SIZE,8,EGL14.EGL_RENDERABLE_TYPE,EGL14.EGL_OPENGL_ES2_BIT,
                EGL14.EGL_SURFACE_TYPE,EGL14.EGL_PBUFFER_BIT,EGL14.EGL_NONE};
            EGLConfig[] configs=new EGLConfig[1];int[] count=new int[1];
            if(!EGL14.eglChooseConfig(display,attributes,0,configs,0,1,count,0)||count[0]==0)
                throw new IOException("movie EGL RGBA config");
            config=configs[0];
            glContext=EGL14.eglCreateContext(display,config,EGL14.EGL_NO_CONTEXT,
                new int[]{EGL14.EGL_CONTEXT_CLIENT_VERSION,2,EGL14.EGL_NONE},0);
            createPbuffer(1,1);
            int[] names=new int[1];GLES20.glGenTextures(1,names,0);textureId=names[0];
            GLES20.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES,textureId);
            GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES,GLES20.GL_TEXTURE_MIN_FILTER,GLES20.GL_LINEAR);
            GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES,GLES20.GL_TEXTURE_MAG_FILTER,GLES20.GL_LINEAR);
            GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES,GLES20.GL_TEXTURE_WRAP_S,GLES20.GL_CLAMP_TO_EDGE);
            GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES,GLES20.GL_TEXTURE_WRAP_T,GLES20.GL_CLAMP_TO_EDGE);
            int vertex=shader(GLES20.GL_VERTEX_SHADER,
                "attribute vec2 p;attribute vec2 t;uniform mat4 m;varying vec2 uv;"
                +"void main(){gl_Position=vec4(p,0.,1.);uv=(m*vec4(t,0.,1.)).xy;}");
            int fragment=shader(GLES20.GL_FRAGMENT_SHADER,
                "#extension GL_OES_EGL_image_external : require\nprecision mediump float;"
                +"varying vec2 uv;uniform samplerExternalOES s;void main(){gl_FragColor=texture2D(s,uv);}");
            program=GLES20.glCreateProgram();GLES20.glAttachShader(program,vertex);
            GLES20.glAttachShader(program,fragment);GLES20.glLinkProgram(program);
            GLES20.glDeleteShader(vertex);GLES20.glDeleteShader(fragment);
            int[] linked=new int[1];GLES20.glGetProgramiv(program,GLES20.GL_LINK_STATUS,linked,0);
            if(linked[0]==0)throw new IOException("movie GL program link");
            texture=new SurfaceTexture(textureId);
            texture.setOnFrameAvailableListener(new SurfaceTexture.OnFrameAvailableListener(){
                @Override public void onFrameAvailable(SurfaceTexture owner){
                    if(closed || owner!=texture)return;
                    try { captureFrame(); }catch(Exception failure){fail("movie RGBA frame: "+failure);}
                }
            },handler);
            surface=new Surface(texture);
        }
        void createPbuffer(int width,int height) throws IOException {
            EGLSurface next=EGL14.eglCreatePbufferSurface(display,config,
                new int[]{EGL14.EGL_WIDTH,width,EGL14.EGL_HEIGHT,height,EGL14.EGL_NONE},0);
            if(next==EGL14.EGL_NO_SURFACE ||
               !EGL14.eglMakeCurrent(display,next,next,glContext))throw new IOException("movie EGL pbuffer");
            if(pbuffer!=EGL14.EGL_NO_SURFACE)EGL14.eglDestroySurface(display,pbuffer);
            pbuffer=next;
        }
        void resize(int width,int height) throws IOException {
            if(width<=0 || height<=0 || (long)width*height>16*1024*1024)
                throw new IOException("invalid or oversized decoded movie dimensions");
            if(this.width==width && this.height==height)return;
            createPbuffer(width,height);this.width=width;this.height=height;
            rgba=ByteBuffer.allocateDirect(width*height*4).order(ByteOrder.nativeOrder());
            texture.setDefaultBufferSize(width,height);
        }
        void captureFrame() throws IOException {
            if(width==0 || height==0)resize(player.getVideoWidth(),player.getVideoHeight());
            texture.updateTexImage();texture.getTransformMatrix(transform);
            GLES20.glViewport(0,0,width,height);GLES20.glUseProgram(program);
            GLES20.glActiveTexture(GLES20.GL_TEXTURE0);
            GLES20.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES,textureId);
            int p=GLES20.glGetAttribLocation(program,"p"),t=GLES20.glGetAttribLocation(program,"t");
            quad.position(0);GLES20.glVertexAttribPointer(p,2,GLES20.GL_FLOAT,false,16,quad);
            quad.position(2);GLES20.glVertexAttribPointer(t,2,GLES20.GL_FLOAT,false,16,quad);
            GLES20.glEnableVertexAttribArray(p);GLES20.glEnableVertexAttribArray(t);
            GLES20.glUniformMatrix4fv(GLES20.glGetUniformLocation(program,"m"),1,false,transform,0);
            GLES20.glUniform1i(GLES20.glGetUniformLocation(program,"s"),0);
            GLES20.glDrawArrays(GLES20.GL_TRIANGLE_STRIP,0,4);
            rgba.position(0);GLES20.glReadPixels(0,0,width,height,GLES20.GL_RGBA,GLES20.GL_UNSIGNED_BYTE,rgba);
            if(GLES20.glGetError()!=GLES20.GL_NO_ERROR)throw new IOException("movie GL readback failed");
            int[] frame=new int[3+width*height];frame[0]=width;frame[1]=height;frame[2]=++sequence;
            for(int y=0;y<height;++y)for(int x=0;x<width;++x){
                int at=((height-1-y)*width+x)*4;
                frame[3+y*width+x]=((rgba.get(at+3)&255)<<24)|((rgba.get(at)&255)<<16)
                    |((rgba.get(at+1)&255)<<8)|(rgba.get(at+2)&255);
            }
            pixels=frame;firstFrame.countDown();
        }
        @Override public boolean isClosed(){return closed;}
        @Override public boolean awaitClosed(long timeoutMs) throws InterruptedException {
            return released.await(timeoutMs,TimeUnit.MILLISECONDS);
        }
        @Override public void close() {
            final Handler owner;
            synchronized(this) {
                if(closed)return;
                closed=true;finished=true;firstFrame.countDown();owner=handler;
                if(owner==null){released.countDown();return;}
            }
            owner.post(new Runnable(){@Override public void run(){
                try {
                    try{if(player!=null){player.setSurface(null);player.release();}}
                    catch(RuntimeException failure){error="movie decoder release failed";}
                    finally{player=null;}
                    try{if(surface!=null)surface.release();}catch(RuntimeException failure){error="movie surface release failed";}
                    finally{surface=null;}
                    try{if(texture!=null)texture.release();}catch(RuntimeException failure){error="movie texture release failed";}
                    finally{texture=null;}
                    if(display!=EGL14.EGL_NO_DISPLAY){
                        if(program!=0)GLES20.glDeleteProgram(program);
                        if(textureId!=0)GLES20.glDeleteTextures(1,new int[]{textureId},0);
                        EGL14.eglMakeCurrent(display,EGL14.EGL_NO_SURFACE,EGL14.EGL_NO_SURFACE,EGL14.EGL_NO_CONTEXT);
                        if(pbuffer!=EGL14.EGL_NO_SURFACE)EGL14.eglDestroySurface(display,pbuffer);
                        if(glContext!=EGL14.EGL_NO_CONTEXT)EGL14.eglDestroyContext(display,glContext);
                        // SDL may own contexts on this same EGLDisplay.
                        // Release only this session's surface/context/thread.
                        EGL14.eglReleaseThread();
                        display=EGL14.EGL_NO_DISPLAY;
                        glContext=EGL14.EGL_NO_CONTEXT;pbuffer=EGL14.EGL_NO_SURFACE;
                        program=0;textureId=0;
                    }
                } finally {pixels=null;released.countDown();thread.quitSafely();}
            }});
        }
    }
}
