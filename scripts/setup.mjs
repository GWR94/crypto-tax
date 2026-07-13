import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const npm = process.platform === "win32" ? "npm.cmd" : "npm";

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: root,
    stdio: "inherit",
    shell: false,
    ...options,
  });

  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

const venvDir = path.join(root, ".venv");
const pythonLauncher = process.platform === "win32" ? "python" : "python3";

if (!existsSync(venvDir)) {
  console.log("Creating Python virtual environment (.venv)...");
  run(pythonLauncher, ["-m", "venv", ".venv"]);
}

const venvPython =
  process.platform === "win32"
    ? path.join(venvDir, "Scripts", "python.exe")
    : path.join(venvDir, "bin", "python");

console.log("Installing Python dependencies...");
run(venvPython, ["-m", "pip", "install", "-r", "apps/api/requirements.txt"]);

console.log("Installing root Node dependencies...");
run(npm, ["install"]);

console.log("Installing web app dependencies...");
run(npm, ["install"], { cwd: path.join(root, "apps", "web") });

console.log("\nSetup complete. Start the dashboard with:\n\n  npm run dev\n");
