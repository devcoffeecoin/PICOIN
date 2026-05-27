import { exec } from "child_process";
import path from "path";
import { app } from "electron"; // 1. Importamos 'app' de electron

function run(command: string): Promise<string> {
  return new Promise((resolve, reject) => {
    exec(command, (error, stdout, stderr) => {
      if (error) {
        reject(stderr || stdout || error.message);
      } else {
        resolve(stdout.trim());
      }
    });
  });
}

export async function ensurePythonRequirements() {
  // Detect Python
  let pythonCmd = "python";

  try {
    const version = await run("python --version");

    if (!version.includes("Python")) {
      throw new Error("Python not found");
    }

    console.log("Python detected:", version);

    // Validate version
    const match = version.match(/Python (\d+)\.(\d+)/);

    if (!match) {
      throw new Error("Unable to detect Python version");
    }

    const major = parseInt(match[1]);
    const minor = parseInt(match[2]);

    if (major < 3 || (major === 3 && minor < 10)) {
      throw new Error("Python 3.10+ required");
    }
  } catch {
    // fallback Windows launcher
    try {
      const version = await run("py --version");

      pythonCmd = "py";

      console.log("Python detected via py:", version);
    } catch {
      throw new Error(
        "Python not installed. Please install Python 3.10+"
      );
    }
  }

  // LÓGICA INTELIGENTE DE DETECCIÓN DE RUTAS
  // 2. Si la app está empaquetada usa 'process.resourcesPath', si estás desarrollando usa 'process.cwd()'
  const isDev = !app.isPackaged;
  const rootPath = isDev ? process.cwd() : process.resourcesPath;

  // 3. Modificamos la ruta para que apunte dinámicamente al lugar correcto
  const requirementsPath = path.join(
    rootPath,
    "backend",
    "requirements.txt"
  );

  console.log("Installing requirements from:", requirementsPath);

  await run(
    `${pythonCmd} -m pip install -r "${requirementsPath}"`
  );

  return pythonCmd;
}