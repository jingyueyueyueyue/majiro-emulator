package org.majiro.player;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Typeface;
import android.os.Bundle;
import java.io.File;
import java.io.FileOutputStream;
import java.lang.reflect.Field;
import org.json.JSONArray;
import org.json.JSONObject;

/** Real Android glyph raster checks on padded canvases, without loading a game. */
public final class RenderInstrumentation extends Instrumentation {
    @Override public void onCreate(Bundle arguments){super.onCreate(arguments);start();}
    private static Field field(String name)throws Exception{
        Field field=GameBridge.class.getDeclaredField(name);field.setAccessible(true);return field;
    }
    private static void image(File file,Bitmap bitmap)throws Exception{
        try(FileOutputStream output=new FileOutputStream(file)){
            bitmap.compress(Bitmap.CompressFormat.PNG,100,output);
        }
    }
    @Override public void onStart(){
        Bundle result=new Bundle();JSONArray cases=new JSONArray();
        try{
            Context context=getTargetContext();
            field("context").set(null,context);
            String[] samples={"中文顶端完整，保持笔画。","「日本語の高い文字」",
                              "ÉÅgjpq国高雪冒瞥","“翻译后的对白”：测试"};
            int clippedLegacy=0;
            for(String family:new String[]{"sans-serif","serif"})
            for(int style:new int[]{Typeface.NORMAL,Typeface.BOLD})
            for(int height:new int[]{16,20,24,30,36})
            for(int quality:new int[]{1,4})
            for(String text:samples){
                Typeface typeface=Typeface.create(family,style);
                field("customTypeface").set(null,typeface);
                field("fontQualityMode").setInt(null,quality==1?0:1);
                int[] codepoints=text.codePoints().toArray(),steps=new int[codepoints.length];
                java.util.Arrays.fill(steps,height);
                int[] production=GameBridge.glyphs("",height,steps,text,false);
                Paint paint=new Paint(Paint.ANTI_ALIAS_FLAG|Paint.SUBPIXEL_TEXT_FLAG);
                paint.setTypeface(typeface);paint.setTextSize(height*quality);paint.setColor(Color.WHITE);
                GlyphCell cell=GlyphCell.measure(paint,codepoints,height*quality,quality);
                int width=production[0],guard=height*quality*2;
                Bitmap padded=Bitmap.createBitmap(width*quality+2*guard,height*quality+2*guard,Bitmap.Config.ARGB_8888);
                Bitmap legacy=Bitmap.createBitmap(width*quality+2*guard,height*quality+2*guard,Bitmap.Config.ARGB_8888);
                Canvas canvas=new Canvas(padded),oldCanvas=new Canvas(legacy);
                Paint.FontMetrics fm=paint.getFontMetrics();
                float oldBaseline=-fm.ascent,measured=fm.descent-fm.ascent;
                if(measured>height*quality)oldBaseline*=height*quality/measured;
                int x=0;
                for(int cp:codepoints){
                    String glyph=new String(Character.toChars(cp));
                    float glyphWidth=paint.measureText(glyph);
                    float sx=glyphWidth>0?height*quality/glyphWidth:1;
                    canvas.save();canvas.translate(guard+x*quality,guard+cell.baseline);
                    canvas.scale(sx,cell.verticalScale);canvas.drawText(glyph,0,0,paint);canvas.restore();
                    oldCanvas.save();oldCanvas.translate(guard+x*quality,guard);
                    oldCanvas.scale(sx,1);oldCanvas.drawText(glyph,0,oldBaseline,paint);oldCanvas.restore();
                    x+=height;
                }
                int[] pixels=new int[padded.getWidth()*padded.getHeight()],oldPixels=new int[pixels.length];
                padded.getPixels(pixels,0,padded.getWidth(),0,0,padded.getWidth(),padded.getHeight());
                legacy.getPixels(oldPixels,0,legacy.getWidth(),0,0,legacy.getWidth(),legacy.getHeight());
                long lost=0,oldLost=0;
                for(int y=0;y<padded.getHeight();++y)for(int xx=0;xx<padded.getWidth();++xx){
                    if(y<guard||y>=guard+height*quality){
                        lost+=pixels[y*padded.getWidth()+xx]>>>24;
                        oldLost+=oldPixels[y*padded.getWidth()+xx]>>>24;
                    }
                }
                if(lost!=0)throw new AssertionError("glyph ink escaped fixed cell: "+family+"/"+height+"/"+quality);
                for(int y=0;y<height;++y)for(int xx=0;xx<width;++xx){
                    int sum=0;
                    for(int yy=0;yy<quality;++yy)for(int sx=0;sx<quality;++sx)
                        sum+=pixels[(guard+y*quality+yy)*padded.getWidth()+guard+xx*quality+sx]>>>24;
                    int expected=(sum+quality*quality/2)/(quality*quality);
                    if(production[2+y*width+xx]!=expected)
                        throw new AssertionError("production glyph differs from complete padded reference");
                }
                if(oldLost>0)++clippedLegacy;
                JSONObject row=new JSONObject();
                row.put("family",family);row.put("style",style);row.put("height",height);
                row.put("quality",quality);row.put("sample",cases.length()%samples.length);
                row.put("legacy_lost_alpha",oldLost);row.put("new_lost_alpha",lost);
                cases.put(row);
                if(family.equals("sans-serif")&&style==0&&height==24&&quality==4&&text==samples[0]){
                    File folder=new File(context.getFilesDir(),"render-checks");folder.mkdirs();
                    image(new File(folder,"glyph-padded-fixed.png"),padded);
                    image(new File(folder,"glyph-padded-old.png"),legacy);
                }
                padded.recycle();legacy.recycle();
            }
            result.putString("stream","Glyph cells: "+cases.length()+" passed; legacy clipping reproduced in "+clippedLegacy+" cases.\n");
            result.putInt("checks",cases.length());result.putInt("legacy_clipped_cases",clippedLegacy);
            result.putString("cases",cases.toString());
            finish(Activity.RESULT_OK,result);
        }catch(Throwable error){
            result.putString("stream","Glyph rendering FAILED: "+error.toString()+"\n");
            result.putString("cases",cases.toString());finish(Activity.RESULT_CANCELED,result);
        }
    }
}
