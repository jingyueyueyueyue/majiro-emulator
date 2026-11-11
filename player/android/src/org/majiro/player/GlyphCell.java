package org.majiro.player;

import android.graphics.Paint;
import android.graphics.Rect;

/** Keep every measured glyph inside the source engine's fixed-height text cell. */
final class GlyphCell {
    final float baseline, verticalScale;

    private GlyphCell(float baseline, float verticalScale) {
        this.baseline=baseline;
        this.verticalScale=verticalScale;
    }

    static GlyphCell measure(Paint paint,int[] codepoints,int cellHeight,int quality) {
        Rect bounds=new Rect();
        int top=0,bottom=0;
        // Stable reference bounds keep punctuation and short chunks aligned
        // with the surrounding CJK/Latin dialogue in the same font.
        int[] reference={0x56fd,'H','g'};
        for(int pass=0;pass<2;++pass){
            int[] values=pass==0?reference:codepoints;
            for(int value:values){
                if(value=='\n'||value=='\r')continue;
                String glyph=new String(Character.toChars(value));
                paint.getTextBounds(glyph,0,glyph.length(),bounds);
                top=Math.min(top,bounds.top);bottom=Math.max(bottom,bounds.bottom);
            }
        }
        int ink=Math.max(1,bottom-top);
        float guard=Math.max(1f,quality*.5f);
        float scale=Math.min(1f,Math.max(1f,cellHeight-2*guard)/ink);
        float baseline=(cellHeight-ink*scale)*.5f-top*scale;
        return new GlyphCell(baseline,scale);
    }
}
