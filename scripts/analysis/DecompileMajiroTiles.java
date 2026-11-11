import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.listing.Function;
import ghidra.program.model.address.Address;
import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;

/** Read-only headless evidence; output stays in the caller's archive folder. */
public class DecompileMajiroTiles extends GhidraScript {
    @Override public void run() throws Exception {
        String[] args=getScriptArgs();
        if(args.length!=1)throw new IllegalArgumentException("output path required");
        DecompInterface decompiler=new DecompInterface();
        decompiler.openProgram(currentProgram);
        try(PrintWriter output=new PrintWriter(args[0],StandardCharsets.UTF_8.name())){
            for(Function function:currentProgram.getFunctionManager().getFunctions(true)){
                long at=function.getEntryPoint().getOffset();
                if(!((at>=0x461e00L&&at<0x467c50L)||at==0x45b510L||at==0x45c8e0L))continue;
                DecompileResults result=decompiler.decompileFunction(function,30,monitor);
                output.println("// "+function.getEntryPoint()+" "+function.getName());
                if(result.decompileCompleted())output.println(result.getDecompiledFunction().getC());
                else output.println("// ERROR "+result.getErrorMessage());
            }
        }finally{decompiler.dispose();}
    }
}
