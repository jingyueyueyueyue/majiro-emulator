package org.majiro.player;

import java.util.HashSet;
import java.util.Set;

/** Valid native save/load selection with no implicit confirmation. */
final class NativeSlotSelection {
    private final boolean loading;
    private final Set<Integer> existing=new HashSet<Integer>();
    private Integer selected;

    NativeSlotSelection(boolean loading,int[] available){
        this.loading=loading;
        for(int slot:available){
            if(!readId(slot)||!existing.add(slot))
                throw new IllegalArgumentException("invalid or duplicate native slot metadata");
        }
    }
    private static boolean readId(int slot){
        return(slot>=1&&slot<=1100)||slot==10001||(slot>=10003&&slot<=10010);
    }
    boolean choose(int slot){
        selected=null;
        if(loading){
            if(!readId(slot)||!existing.contains(slot))return false;
        }else if(slot<101||slot>1100)return false;
        selected=slot;return true;
    }
    boolean restoreDraft(String draft){
        selected=null;
        if(!draft.matches("slot=[0-9]{1,5}"))return false;
        return choose(Integer.parseInt(draft.substring(5)));
    }
    boolean canConfirm(){return selected!=null;}
    Integer selected(){return selected;}
    String confirm(){
        if(!canConfirm())throw new IllegalStateException("no valid native slot selected");
        return"slot="+selected;
    }
    String cancel(){selected=null;return"";}
}
