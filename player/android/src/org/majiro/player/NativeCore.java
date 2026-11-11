package org.majiro.player;

public final class NativeCore {
    static { System.loadLibrary("majiro_core"); }
    private NativeCore() {}
    public static native String inspectArchive(int descriptor, long bytes);
    public static native String inspectImage(byte[] data, byte[] password);
    public static native String inspectNativeSave(int descriptor, long bytes, boolean readmarks);
}
