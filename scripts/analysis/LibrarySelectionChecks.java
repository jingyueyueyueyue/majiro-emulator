package org.majiro.player;
import java.util.*;
public final class LibrarySelectionChecks {
    public static void main(String[] args){
        GameLibrary.Entry stale=new GameLibrary.Entry();stale.id="same";stale.tree="old-grant";
        GameLibrary.Entry live=new GameLibrary.Entry();live.id="same";live.tree="new-grant";live.root="live-root";
        ArrayList<GameLibrary.Entry> entries=new ArrayList<>();entries.add(live);
        if(GameLibrary.currentEntry(entries,stale.id)!=live)throw new AssertionError("stale library selection");
        entries.clear();boolean rejected=false;
        try{GameLibrary.currentEntry(entries,stale.id);}catch(IllegalStateException expected){rejected=true;}
        if(!rejected)throw new AssertionError("removed entry relaunched");
        System.out.println("{\"status\":\"passed\",\"checks\":2}");
    }
}
