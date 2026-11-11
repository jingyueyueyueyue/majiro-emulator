package org.majiro.player;

import java.nio.file.Files;
import java.nio.file.Paths;
import java.nio.charset.StandardCharsets;

/** JVM comparison entry: one identity or explicit reject per supplied file. */
public final class EngineCodeIdentityChecks {
    public static void main(String[] args) throws Exception {
        String[] paths = args;
        if (args.length == 2 && args[0].equals("--utf8-list"))
            paths = Files.readAllLines(Paths.get(args[1]), StandardCharsets.UTF_8).toArray(new String[0]);
        for (String path : paths) {
            try {
                System.out.println(EngineCodeIdentity.from(Files.readAllBytes(Paths.get(path))));
            } catch (IllegalArgumentException error) {
                System.err.println(path + ": " + error.getMessage());
                System.out.println("REJECT");
            }
        }
    }
}
