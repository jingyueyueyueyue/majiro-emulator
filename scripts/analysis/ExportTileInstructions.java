import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import java.io.PrintWriter;
import java.util.ArrayDeque;
import java.util.HashSet;

public class ExportTileInstructions extends GhidraScript {
    @Override public void run() throws Exception {
        ArrayDeque<Long> pending = new ArrayDeque<Long>();
        pending.add(0x4326c0L);
        HashSet<Long> seen = new HashSet<Long>();
        try (PrintWriter output = new PrintWriter(getScriptArgs()[0], "UTF-8")) {
            while (!pending.isEmpty()) {
                long at = pending.removeFirst();
                if (!seen.add(at)) continue;
                Function function = getFunctionAt(toAddr(at));
                if (function == null) { output.println("MISSING " + Long.toHexString(at)); continue; }
                output.println("FUNCTION " + Long.toHexString(at));
                for (Instruction row : currentProgram.getListing().getInstructions(function.getBody(), true)) {
                    output.println(Long.toHexString(row.getAddress().getOffset()));
                    if (row.getFlowType().isCall()) {
                        for (ghidra.program.model.address.Address target : row.getFlows()) {
                            long value = target.getOffset();
                            if (value >= 0x431000L && value < 0x438000L) pending.add(value);
                        }
                    }
                }
            }
        }
    }
}
