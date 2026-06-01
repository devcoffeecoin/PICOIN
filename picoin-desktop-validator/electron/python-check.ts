import { execFile } from "child_process";
import path from "path";
import { app } from "electron";

function run(command: string, args: string[]): Promise<string> {
  return new Promise((resolve, reject) => {
    execFile(command, args, { windowsHide: true }, (error, stdout, stderr) => {
      if (error) {
        reject(new Error(stderr || stdout || error.message));
        return;
      }
      resolve(stdout.trim() || stderr.trim());
    });
  });
}

async function detectPython(): Promise<string> {
  for (const candidate of ["python", "py"]) {
    try {
      const version = await run(candidate, ["--version"]);
      const match = version.match(/Python\s+(\d+)\.(\d+)/i);
      if (!match) continue;
      const major = Number(match[1]);
      const minor = Number(match[2]);
      if (major > 3 || (major === 3 && minor >= 10)) {
        return candidate;
      }
    } catch {
      continue;
    }
  }
  throw new Error("Python 3.10+ is required.");
}

export async function ensurePythonRequirements(): Promise<string> {
  const pythonCmd = await detectPython();
  const rootPath = app.isPackaged ? process.resourcesPath : process.cwd();
  const requirementsPath = path.join(rootPath, "backend", "requirements.txt");
  await run(pythonCmd, ["-m", "pip", "install", "-r", requirementsPath]);
  return pythonCmd;
}
