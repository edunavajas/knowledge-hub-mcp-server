#!/usr/bin/env node
// Thin npm shim: spawn the bundled Python stdio server and pass stdio through.
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const script = join(dirname(fileURLToPath(import.meta.url)), "..", "knowledge_hub_mcp.py");
const candidates = [process.env.KNOWLEDGE_HUB_PYTHON, "python3", "python"].filter(Boolean);
const args = [script, ...process.argv.slice(2)];

let child;
function launch(index) {
  child = spawn(candidates[index], args, { stdio: "inherit" });
  child.once("error", (err) => {
    if (err.code === "ENOENT" && index + 1 < candidates.length) {
      launch(index + 1);
      return;
    }
    console.error(`knowledge-hub-mcp: ${err.message}`);
    process.exit(127);
  });
  child.once("exit", (code, signal) => {
    if (signal) process.kill(process.pid, signal);
    else process.exit(code ?? 0);
  });
}

// ponytail: best-effort forwarding; the terminal already signals the whole
// foreground process group, this only covers kill-by-node-pid.
for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => {
    if (child && !child.killed) child.kill(signal);
  });
}

launch(0);
