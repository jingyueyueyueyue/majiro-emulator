package org.majiro.player;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.IntBuffer;

/** Immutable top-down RGBA bytes; JNI copies the payload without a color conversion. */
final class MovieFramePixels {
    static int[] pack(ByteBuffer rgba,int width,int height,int sequence) {
        long count=(long)width*height;
        if(width<=0||height<=0||count>16*1024*1024||rgba.capacity()<count*4)
            throw new IllegalArgumentException("movie pixel geometry");
        int[] frame=new int[3+(int)count];
        frame[0]=width;frame[1]=height;frame[2]=sequence;
        ByteBuffer bytes=rgba.duplicate().order(ByteOrder.LITTLE_ENDIAN);
        bytes.clear();
        IntBuffer words=bytes.asIntBuffer();
        for(int y=0;y<height;++y){
            words.position((height-1-y)*width);
            words.get(frame,3+y*width,width);
        }
        return frame;
    }
    private MovieFramePixels() {}
}
