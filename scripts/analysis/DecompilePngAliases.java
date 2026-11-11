import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.program.model.listing.Function;
import java.io.PrintWriter;

public class DecompilePngAliases extends GhidraScript {
    @Override public void run() throws Exception {
        DecompInterface decompiler = new DecompInterface();
        decompiler.openProgram(currentProgram);
        long[] roots = {0x48a400L,0x48a4f0L,0x48a990L,0x48ae00L,
                        0x48b000L,0x48b0f0L,0x48b160L,0x44a340L,0x44a4c0L};
        try (PrintWriter output = new PrintWriter(getScriptArgs()[0], "UTF-8")) {
            for (long at : roots) {
                Function function = getFunctionAt(toAddr(at));
                if (function == null) function = createFunction(toAddr(at), null);
                if (function == null) continue;
                output.println("// " + Long.toHexString(at));
                var result = decompiler.decompileFunction(function, 30, monitor);
                if (result.decompileCompleted()) output.println(result.getDecompiledFunction().getC());
                else output.println("// " + result.getErrorMessage());
            }
        } finally { decompiler.dispose(); }
    }
}
