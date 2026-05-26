import { app } from "electron";
import { spawn, ChildProcessWithoutNullStreams } from "child_process";
import path from "path";
import os from "os";
import fs from "fs";

let minerProcess: ChildProcessWithoutNullStreams | null = null;
let lastLogs: string[] = [];
let currentTask = "Idle";
let status: "stopped" | "starting" | "mining" | "error" = "stopped";
let stoppedByUser = false;
let hashrate = "0 H/s";
let activeWorkers = 0;
let activeIntensity = 0;
let idleDelaySeconds = 1;
let latestSegmentLength: number | null = null;
let latestComputeMs: number | null = null;
const DEFAULT_NETWORK_ID = "public-testnet";
const DEFAULT_CHAIN_ID = "picoin-public-testnet-v018";
const MAX_LOG_LINES = 15;

function addLog(line: string) {
  const clean = line.trim();
  if (!clean) return;

  lastLogs.unshift(`[${new Date().toLocaleTimeString()}] ${clean}`);
  lastLogs = lastLogs.slice(0, MAX_LOG_LINES);

  if (clean.includes("Task assigned")) currentTask = clean;
  if (clean.includes("Calculated segment")) currentTask = clean;
  if (clean.includes("Commit accepted")) currentTask = clean;
  if (clean.includes("Reveal accepted")) currentTask = clean;
  if (clean.includes("Accepted block")) currentTask = clean;
  if (clean.includes("Reward")) currentTask = clean;

  updateMiningMetrics(clean);
}

function normalizeIntensity(intensity: number | undefined) {
  const numeric = Number(intensity);
  if (!Number.isFinite(numeric)) return 75;
  return Math.max(1, Math.min(100, numeric));
}

function workersFromIntensity(intensity: number) {
  const cpus = Math.max(1, os.cpus().length);
  return Math.max(1, Math.floor((cpus * intensity) / 100));
}

function idleDelayFromIntensity(intensity: number) {
  return Number((0.25 + ((100 - intensity) / 100) * 2.75).toFixed(2));
}

function updateMiningMetrics(line: string) {
  const segmentMatch = line.match(/^Calculated segment length:\s*(\d+)/i);
  if (segmentMatch) {
    latestSegmentLength = Number(segmentMatch[1]);
    refreshHashrate();
    return;
  }

  const timeMatch = line.match(/^Compute time:\s*(\d+)\s*ms/i);
  if (timeMatch) {
    latestComputeMs = Number(timeMatch[1]);
    refreshHashrate();
    return;
  }

  const workersMatch = line.match(/^Workers:\s*(\d+)/i);
  if (workersMatch) {
    activeWorkers = Number(workersMatch[1]);
  }
}

function refreshHashrate() {
  if (!latestSegmentLength || latestComputeMs === null) return;
  const seconds = Math.max(0.001, latestComputeMs / 1000);
  hashrate = formatHashrate(latestSegmentLength / seconds);
}

function formatHashrate(rate: number) {
  if (!Number.isFinite(rate) || rate <= 0) return "0 H/s";
  if (rate >= 1_000_000) return `${(rate / 1_000_000).toFixed(2)} MH/s`;
  if (rate >= 1_000) return `${(rate / 1_000).toFixed(2)} kH/s`;
  if (rate >= 100) return `${Math.round(rate)} H/s`;
  return `${rate.toFixed(2)} H/s`;
}

function configValue(value: string | undefined, fallback: string) {
  return String(value || "").trim() || fallback;
}

export function startMiner(config: {
  minerName: string;
  rewardWallet: string;
  apiNodeUrl: string;
  networkId: string;
  chainId: string;
  miningIntensity: number;
}) {
  if (minerProcess) {
    return { ok: true, message: "Miner already running" };
  }

  status = "starting";
  currentTask = "Starting miner...";
  lastLogs = [];

  const projectRoot = path.resolve(__dirname, "..", "..", "..");
  const corePath = app.isPackaged
    ? path.join(process.resourcesPath, "picoin-core")
    : path.resolve(projectRoot, "resources", "picoin-core");


  const identityPath = getIdentityPath();
  const normalizedIntensity = normalizeIntensity(config.miningIntensity);
  const workers = workersFromIntensity(normalizedIntensity);
  const sleepSeconds = idleDelayFromIntensity(normalizedIntensity);
  const networkId = configValue(config.networkId, DEFAULT_NETWORK_ID);
  const chainId = configValue(config.chainId, DEFAULT_CHAIN_ID);
  activeIntensity = normalizedIntensity;
  activeWorkers = workers;
  idleDelaySeconds = sleepSeconds;
  hashrate = "0 H/s";
  latestSegmentLength = null;
  latestComputeMs = null;

  const env = {
    ...process.env,
    PICOIN_MINER_REWARD_ADDRESS: config.rewardWallet,
    PICOIN_NETWORK: networkId,
    PICOIN_CHAIN_ID: chainId,
    PICOIN_AUTO_REGISTER_IDENTITY: "1",
    PYTHONUNBUFFERED: "1",
  };

  
    const args = [
    "-u",
    "-m",
    "miner.client",
    "--server",
    config.apiNodeUrl,
    "--identity",
    identityPath,
    "mine",
    "--loops",
    "999999",
    "--sleep",
    String(sleepSeconds),
    "--workers",
    String(workers),
    ];

  addLog(`Starting Picoin miner with ${workers} workers`);
  addLog(`Mining intensity: ${normalizedIntensity}%`);
  addLog(`Idle delay: ${sleepSeconds}s`);
  addLog(`API node: ${config.apiNodeUrl}`);
  addLog(`Network: ${env.PICOIN_NETWORK}`);
  addLog(`Chain ID: ${env.PICOIN_CHAIN_ID}`);
  addLog(`Identity: ${identityPath}`);


    if (!fs.existsSync(corePath)) {
    throw new Error(`Picoin core path not found: ${corePath}`);
    }

    if (!fs.existsSync(path.join(corePath, "miner", "client.py"))) {
    throw new Error(`miner/client.py not found in: ${corePath}`);
    }

   const pythonExecutable = path.join(
  corePath,
  ".venv",
  "Scripts",
  "python.exe"
    );

    addLog(`Python: ${pythonExecutable}`);

    minerProcess = spawn(pythonExecutable, args, {
        cwd: corePath,
        env,
        shell: false,
    });

  addLog(`PID: ${minerProcess.pid}`);
  addLog(`Core path: ${corePath}`);
  addLog(`Command: python ${args.join(" ")}`);

  status = "mining";

  minerProcess.stdout.on("data", (data) => {
    String(data)
      .split(/\r?\n/)
      .forEach(addLog);
  });

  minerProcess.stderr.on("data", (data) => {
    String(data)
      .split(/\r?\n/)
      .forEach((line) => addLog(`ERROR: ${line}`));
  });

  minerProcess.on("close", (code) => {
  addLog(`Miner process exited with code ${code}`);

    minerProcess = null;
    activeWorkers = 0;

    if (stoppedByUser) {
        status = "stopped";
        currentTask = "Miner stopped by user";
        hashrate = "0 H/s";
        stoppedByUser = false;
        return;
    }

    status = code === 0 ? "stopped" : "error";
    currentTask = "Miner stopped";
    hashrate = "0 H/s";
    });

  minerProcess.on("error", (error) => {
    addLog(`Miner process error: ${error.message}`);
    minerProcess = null;
    activeWorkers = 0;
    hashrate = "0 H/s";
    status = "error";
    currentTask = error.message;
  });

  return {
    ok: true,
    message: "Miner started",
    workers,
    miningIntensity: normalizedIntensity,
    idleDelaySeconds: sleepSeconds,
    identityPath,
    corePath,
  };
}

export function stopMiner() {
  if (!minerProcess) {
    status = "stopped";
    return { ok: true, message: "Miner already stopped" };
  }
  stoppedByUser = true;
  minerProcess.kill();
  minerProcess = null;
  status = "stopped";
  currentTask = "Miner stopped";
  hashrate = "0 H/s";
  activeWorkers = 0;
  addLog("Miner stopped by user");

  return { ok: true, message: "Miner stopped" };
}

export function getMinerStatus() {
  return {
    status,
    currentTask,
    hashrate,
    workers: activeWorkers,
    miningIntensity: activeIntensity,
    idleDelaySeconds,
    logs: lastLogs,
    running: minerProcess !== null,
  };
}

export function getSavedMinerIdentity() {
  const identityPath = getIdentityPath();
  if (!fs.existsSync(identityPath)) {
    return {
      exists: false,
      identityPath,
      minerName: null,
      rewardWallet: null,
      minerId: null,
    };
  }

  try {
    const identity = JSON.parse(fs.readFileSync(identityPath, "utf-8"));
    return {
      exists: true,
      identityPath,
      minerName: identity.name || null,
      rewardWallet: identity.reward_address || null,
      minerId: identity.miner_id || null,
    };
  } catch (error) {
    addLog(`ERROR: Could not read miner identity: ${error instanceof Error ? error.message : String(error)}`);
    return {
      exists: false,
      identityPath,
      minerName: null,
      rewardWallet: null,
      minerId: null,
    };
  }
}

export function registerMiner(config: {
  minerName: string;
  rewardWallet: string;
  apiNodeUrl: string;
  networkId: string;
  chainId: string;
}) {
  const projectRoot = path.resolve(__dirname, "..", "..", "..");

  const corePath = app.isPackaged
    ? path.join(process.resourcesPath, "picoin-core")
    : path.resolve(projectRoot, "resources", "picoin-core");

  const identityPath = getIdentityPath();
  const networkId = configValue(config.networkId, DEFAULT_NETWORK_ID);
  const chainId = configValue(config.chainId, DEFAULT_CHAIN_ID);

  const env = {
    ...process.env,
    PICOIN_MINER_REWARD_ADDRESS: config.rewardWallet,
    PICOIN_MINER_NAME: config.minerName,
    PICOIN_NETWORK: networkId,
    PICOIN_CHAIN_ID: chainId,
    PICOIN_AUTO_REGISTER_IDENTITY: "1",
    PYTHONUNBUFFERED: "1",
  };

  const pythonExecutable = path.join(
    corePath,
    ".venv",
    "Scripts",
    "python.exe"
  );

  if (!fs.existsSync(pythonExecutable)) {
    throw new Error(`Python executable not found: ${pythonExecutable}`);
  }

  addLog(`Register core path: ${corePath}`);
  addLog(`Register identity path: ${identityPath}`);
  addLog(`Register network: ${env.PICOIN_NETWORK}`);
  addLog(`Register chain ID: ${env.PICOIN_CHAIN_ID}`);
  addLog(`Python: ${pythonExecutable}`);

  const result = spawn(
    pythonExecutable,
    [
      "-u",
      "-m",
      "miner.client",
      "--server",
      config.apiNodeUrl,
      "--identity",
      identityPath,
      "mine",
      "--loops",
      "1",
      "--sleep",
      "1",
      "--workers",
      "1",
    ],
    {
      cwd: corePath,
      env,
      shell: false,
    }
  );

  result.stdout.on("data", (data) => {
    String(data)
      .split(/\r?\n/)
      .forEach(addLog);
  });

  result.stderr.on("data", (data) => {
    String(data)
      .split(/\r?\n/)
      .forEach((line) => addLog(`ERROR: ${line}`));
  });

  result.on("close", (code) => {
    addLog(`Register process exited with code ${code}`);

    if (fs.existsSync(identityPath)) {
      addLog(`Identity file created: ${identityPath}`);
    } else {
      addLog(`ERROR: Identity file was not created: ${identityPath}`);
    }
  });

  result.on("error", (error) => {
    addLog(`Register process error: ${error.message}`);
  });

  return {
    ok: true,
    message: "Miner registration started",
    identityPath,
  };
}

function getIdentityPath() {
  const identityDir = app.getPath("userData");
  fs.mkdirSync(identityDir, { recursive: true });
  return path.join(identityDir, "miner_identity.json");
}
