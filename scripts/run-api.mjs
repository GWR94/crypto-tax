import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const venvPython =
  process.platform === "win32"
    ? path.join(root, ".venv", "Scripts", "python.exe")
    : path.join(root, ".venv", "bin", "python");

const python = existsSync(venvPython) ? venvPython : "python";

const child = spawn(
  python,
  [
    "-m",
    "uvicorn",
    "app.main:app",
    "--reload",
    "--host",
    "127.0.0.1",
    "--port",
    "8000",
  ],
  {
    cwd: path.join(root, "apps", "api"),
    stdio: "inherit",
    shell: false,
  }
);

child.on("exit", (code) => {
  process.exit(code ?? 0);
});
