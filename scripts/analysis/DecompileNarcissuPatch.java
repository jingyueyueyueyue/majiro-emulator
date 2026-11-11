import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.program.model.listing.Function;
import java.io.PrintWriter;

public class DecompileNarcissuPatch extends GhidraScript {
    @Override public void run() throws Exception {
        DecompInterface decompiler = new DecompInterface();
        decompiler.openProgram(currentProgram);
        long image = currentProgram.getImageBase().getOffset();
        try (PrintWriter output = new PrintWriter(getScriptArgs()[0], "UTF-8")) {
            for (Function function : currentProgram.getFunctionManager().getFunctions(true)) {
                long at = function.getEntryPoint().getOffset();
                if (at < image + 0x1000L || at >= image + 0xc000L) continue;
                output.println("// " + Long.toHexString(at));
                var result = decompiler.decompileFunction(function, 20, monitor);
                if (result.decompileCompleted())
                    output.println(result.getDecompiledFunction().getC());
                else output.println("// " + result.getErrorMessage());
            }
        } finally { decompiler.dispose(); }
    }
}
