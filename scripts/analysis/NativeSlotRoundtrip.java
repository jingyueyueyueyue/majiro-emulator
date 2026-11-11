package org.majiro.player;

/** Execute the same picker state and async replies used by the Android view. */
final class NativeSlotRoundtrip {
    public static void main(String[] args){
        boolean loading=Boolean.parseBoolean(args[0]);
        String[] names=args[1].isEmpty()?new String[]{}:args[1].split(",");
        int[] slots=new int[names.length];
        for(int i=0;i<slots.length;i++)slots[i]=Integer.parseInt(names[i]);
        ModalDialogState state=new ModalDialogState();state.foreground(true);
        ModalDialogState.Request request=state.begin("1",327681,args[2],"Fixture");
        if(!request.draft.isEmpty())throw new AssertionError("slot picker must start unselected");
        NativeSlotSelection model=new NativeSlotSelection(loading,slots);
        if(!args[3].isEmpty())model.restoreDraft(args[3]);
        if(args[4].equals("choose")){
            if(model.choose(Integer.parseInt(args[5])))request.draft=model.confirm();
        }else if(args[4].equals("restore")){
            state.foreground(false);
            if(state.visible()!=null)throw new AssertionError("background dialog still visible");
            state.foreground(true);
            model=new NativeSlotSelection(loading,slots);
            model.restoreDraft(args[3]);
        }else if(!args[4].equals("default")&&!args[4].equals("cancel"))
            throw new IllegalArgumentException("test action");
        if(args[4].equals("cancel"))state.completeData(request,model.cancel());
        else if(model.canConfirm()){
            state.completeData(request,model.confirm());
            // Late duplicate/stale replies cannot change the accepted result.
            state.completeData(request,"slot=0");
        }
        System.out.print(state.poll("1"));
    }
}
