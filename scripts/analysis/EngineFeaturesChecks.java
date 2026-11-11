package org.majiro.player;
import java.nio.file.*;
import java.nio.charset.StandardCharsets;
public final class EngineFeaturesChecks {
    public static void main(String[]args)throws Exception{
        for(String path:Files.readAllLines(Paths.get(args[0]),StandardCharsets.UTF_8)){
            if(path.isEmpty())continue;
            System.out.println(String.join(",",EngineFeatures.from(Files.readAllBytes(Paths.get(path)))));
        }
    }
}
