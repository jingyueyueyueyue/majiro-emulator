import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.program.model.listing.Function;
import java.io.PrintWriter;

public class DecompileTileAI extends GhidraScript {
    @Override public void run() throws Exception {
        DecompInterface decompiler = new DecompInterface();
        decompiler.openProgram(currentProgram);
        try (PrintWriter output = new PrintWriter(getScriptArgs()[0], "UTF-8")) {
            long[] targets = {0x4326c0L,0x437970L,0x467910L,0x455cb0L};
            for (long at : targets) {
                Function function = getFunctionAt(toAddr(at));
                if (function == null) function = createFunction(toAddr(at), null);
                output.println("// " + Long.toHexString(at));
                output.println(decompiler.decompileFunction(function, 30, monitor)
                    .getDecompiledFunction().getC());
            }
        } finally { decompiler.dispose(); }
        try (PrintWriter output = new PrintWriter(getScriptArgs()[0] + ".functions", "UTF-8")) {
            for (Function function : currentProgram.getFunctionManager().getFunctions(true)) {
                long begin = function.getEntryPoint().getOffset();
                if (begin >= 0x431000L && begin < 0x438000L)
                    output.println(Long.toHexString(begin) + " " +
                        Long.toHexString(function.getBody().getMaxAddress().getOffset() + 1));
            }
        }
    }
}
