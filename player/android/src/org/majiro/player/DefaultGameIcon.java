package org.majiro.player;

import android.graphics.Canvas;
import android.graphics.ColorFilter;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.PixelFormat;
import android.graphics.Rect;
import android.graphics.drawable.Drawable;

/** One consistent library icon when the game contains no readable artwork. */
public final class DefaultGameIcon extends Drawable {
    private final Paint paint=new Paint(Paint.ANTI_ALIAS_FLAG);
    @Override public void draw(Canvas canvas) {
        Rect bounds=getBounds();
        int save=canvas.save();
        canvas.translate(bounds.left,bounds.top);
        canvas.scale(bounds.width()/96f,bounds.height()/96f);
        paint.setStyle(Paint.Style.FILL);paint.setColor(0xff226b54);
        canvas.drawRoundRect(0,0,96,96,24,24,paint);
        Path book=new Path();
        book.moveTo(48,32);book.cubicTo(39,25,27,24,19,28);
        book.lineTo(19,67);book.cubicTo(29,63,39,65,48,72);
        book.cubicTo(57,65,67,63,77,67);book.lineTo(77,28);
        book.cubicTo(69,24,57,25,48,32);book.close();
        paint.setColor(0xfff3f6ec);canvas.drawPath(book,paint);
        paint.setColor(0xffb8d2ba);paint.setStrokeWidth(2.5f);
        paint.setStrokeCap(Paint.Cap.ROUND);canvas.drawLine(48,34,48,65,paint);
        canvas.restoreToCount(save);
    }
    @Override public void setAlpha(int alpha){paint.setAlpha(alpha);invalidateSelf();}
    @Override public void setColorFilter(ColorFilter filter){paint.setColorFilter(filter);invalidateSelf();}
    @Override public int getOpacity(){return PixelFormat.TRANSLUCENT;}
    @Override public int getIntrinsicWidth(){return 96;}
    @Override public int getIntrinsicHeight(){return 96;}
}
