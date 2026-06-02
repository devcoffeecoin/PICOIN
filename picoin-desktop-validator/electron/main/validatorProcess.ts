import { app, dialog } from "electron";
import { ChildProcessWithoutNullStreams, spawn } from "child_process";
import crypto from "crypto";
import fs from "fs";
import os from "os";
import path from "path";

const DEFAULT_NETWORK_ID = "picoin-mainnet-v1";
const DEFAULT_CHAIN_ID = "314159";
const DEFAULT_PROTOCOL_VERSION = "1.0";
const MAINNET_GENESIS_HASH = "da286143167d14044c053fbb23fcf4673bb11bcd34fb1a11e5510ee8f8edb6e7";
const MAINNET_TREASURY_WALLET = "PIE1EE818AA165EECC3F0CCF058F4FF7BC04517F8CD07385";
const MAINNET_GOVERNANCE_WALLET = "PI251078EE911B17EDC747DB5BDF505649ECAF60F787AA23";
const MIN_VALIDATOR_STAKE = "31.416";
const MAX_LOG_LINES = 20;
const NODE_HTTP_TIMEOUT_SECONDS = "60";

type NodeStatus = "stopped" | "starting" | "running" | "error";
type ValidatorStatus = "stopped" | "starting" | "validating" | "error";

type ValidatorConfig = {
  pythonCmd?: string;
  validatorName: string;
  rewardWallet: string;
  apiUrl: string;
  networkId: string;
  chainId: string;
  nodePort: number;
  nodeAddress: string;
  validationSleep: number;
};

type StakeConfig = ValidatorConfig & {
  walletPath: string;
  walletPassword?: string;
  amount: string;
  fee: string;
};

let nodeProcess: ChildProcessWithoutNullStreams | null = null;
let validatorProcess: ChildProcessWithoutNullStreams | null = null;
let nodeStatus: NodeStatus = "stopped";
let validatorStatus: ValidatorStatus = "stopped";
let currentTask = "Idle";
let lastLogs: string[] = [];
let nodeStopRequested = false;
let validatorStopRequested = false;
let autoRecoveryInProgress = false;

function addLog(line: string) {
  const clean = line.trim();
  if (!clean) return;
  lastLogs.unshift(`[${new Date().toLocaleTimeString()}] ${clean}`);
  lastLogs = lastLogs.slice(0, MAX_LOG_LINES);
  if (/validated|heartbeat|no validation jobs|not eligible|sync|replay/i.test(clean)) {
    currentTask = clean;
  }
}

function getCorePath(): string {
  const rootPath = app.isPackaged ? process.resourcesPath : process.cwd();
  return path.join(rootPath, "backend");
}

function getRuntimeDir(): string {
  const runtimeDir = path.join(app.getPath("userData"), "mainnet");
  fs.mkdirSync(runtimeDir, { recursive: true });
  return runtimeDir;
}

export function getIdentityPath(): string {
  const identityDir = path.join(getRuntimeDir(), "identities");
  fs.mkdirSync(identityDir, { recursive: true });
  return path.join(identityDir, "validator_identity.json");
}

function getDbPath(): string {
  return path.join(getRuntimeDir(), "picoin.sqlite3");
}

function getLocalNodeUrl(config: ValidatorConfig): string {
  return `http://127.0.0.1:${normalizePort(config.nodePort)}`;
}

function configValue(value: string | undefined, fallback: string): string {
  return String(value || "").trim() || fallback;
}

function normalizePort(port: number | undefined): number {
  const numeric = Number(port);
  if (!Number.isFinite(numeric)) return 8131;
  return Math.max(1024, Math.min(65535, Math.floor(numeric)));
}

function normalizeSleep(value: number | undefined): number {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return 1;
  return Math.max(0.2, Math.min(30, numeric));
}

function normalizeApiUrl(value: string): string {
  return configValue(value, "https://api.picoin.science").replace(/\/$/, "");
}

function buildEnv(config: ValidatorConfig): NodeJS.ProcessEnv {
  const port = normalizePort(config.nodePort);
  const localNodeUrl = `http://127.0.0.1:${port}`;
  const apiUrl = normalizeApiUrl(config.apiUrl);
  const nodeAddress = configValue(config.nodeAddress, localNodeUrl).replace(/\/$/, "");
  const validatorName = configValue(config.validatorName, "picoin-desktop-validator");

  return {
    ...process.env,
    PICOIN_NETWORK: configValue(config.networkId, DEFAULT_NETWORK_ID),
    PICOIN_CHAIN_ID: configValue(config.chainId, DEFAULT_CHAIN_ID),
    PICOIN_PROTOCOL_VERSION: DEFAULT_PROTOCOL_VERSION,
    PICOIN_GENESIS_HASH: MAINNET_GENESIS_HASH,
    PICOIN_GENESIS_ALLOCATIONS_FILE: "",
    PICOIN_TREASURY_WALLET: MAINNET_TREASURY_WALLET,
    PICOIN_GOVERNANCE_WALLET: MAINNET_GOVERNANCE_WALLET,
    PICOIN_SCIENCE_RESERVE_AUTHORIZED_SIGNERS: `${MAINNET_TREASURY_WALLET},${MAINNET_GOVERNANCE_WALLET}`,
    PICOIN_FAUCET_ALLOWED_NETWORKS: "",
    PICOIN_DATA_DIR: getRuntimeDir(),
    PICOIN_DB_PATH: getDbPath(),
    PICOIN_PORT: String(port),
    PICOIN_NODE_ID: validatorName,
    PICOIN_NODE_TYPE: "validator",
    PICOIN_NODE_ADDRESS: nodeAddress,
    PICOIN_BOOTSTRAP_PEERS: apiUrl,
    PICOIN_VALIDATOR_SERVER: apiUrl,
    PICOIN_VALIDATOR_NODE_SERVER: localNodeUrl,
    PICOIN_VALIDATOR_NODE_ADDRESS: nodeAddress,
    PICOIN_VALIDATOR_REWARD_ADDRESS: config.rewardWallet,
    PICOIN_AUTO_REGISTER_IDENTITY: "1",
    PICOIN_HTTP_TIMEOUT_SECONDS: NODE_HTTP_TIMEOUT_SECONDS,
    PYTHONUNBUFFERED: "1",
  };
}

function assertCoreReady() {
  const corePath = getCorePath();
  if (!fs.existsSync(corePath)) {
    throw new Error(`Picoin backend not found: ${corePath}`);
  }
  if (!fs.existsSync(path.join(corePath, "picoin", "__main__.py"))) {
    throw new Error(`Picoin CLI not found in backend: ${corePath}`);
  }
}

async function waitForRpc(url: string, timeoutMs = 45000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    try {
      const response = await fetch(`${url}/protocol`);
      if (response.ok) return;
    } catch {
      // wait and retry
    }
    await new Promise((resolve) => setTimeout(resolve, 750));
  }
  throw new Error(`Local node RPC did not become available at ${url}`);
}

async function fetchLocalNodeJson(config: ValidatorConfig, pathName: string, timeoutMs = 15000): Promise<any | null> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${getLocalNodeUrl(config)}${pathName}`, { signal: controller.signal });
    if (!response.ok) return null;
    return await response.json();
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

function nodeNeedsSnapshotRestore(syncStatus: any): boolean {
  const replay = syncStatus?.replay || {};
  const replayStatus = String(replay.sync_status || syncStatus?.sync_status || "").toLowerCase();
  return Boolean(replay.divergence_detected) || replayStatus.includes("divergent") || replayStatus.includes("out_of_sync");
}

function nodeNeedsCatchUp(syncStatus: any): boolean {
  const replay = syncStatus?.replay || {};
  const localHeight = Number(syncStatus?.local_block_height ?? syncStatus?.latest_block_height ?? 0);
  const effectiveHeight = Number(
    syncStatus?.effective_latest_block_height ?? syncStatus?.target_height ?? syncStatus?.latest_block_height ?? localHeight,
  );
  const pendingReplay = Number(syncStatus?.pending_replay_blocks ?? replay.queue_size ?? 0);
  return localHeight <= 0 || pendingReplay > 0 || effectiveHeight - localHeight > 1;
}

async function ensureNodeReadyForValidation(config: ValidatorConfig) {
  addLog("Checking local node sync before validation.");
  let syncStatus = await fetchLocalNodeJson(config, "/node/sync-status");
  if (!syncStatus) {
    addLog("Local sync status unavailable; continuing after RPC startup.");
    return;
  }

  if (nodeNeedsSnapshotRestore(syncStatus) || Number(syncStatus.local_block_height ?? syncStatus.latest_block_height ?? 0) <= 0) {
    addLog("Local node requires canonical snapshot restore before validation.");
    await restoreSnapshot(config);
    syncStatus = await fetchLocalNodeJson(config, "/node/sync-status");
  }

  if (syncStatus && nodeNeedsCatchUp(syncStatus)) {
    addLog("Catching up local node before validation.");
    await catchUpNode(config);
    syncStatus = await fetchLocalNodeJson(config, "/node/sync-status");
  }

  if (syncStatus && nodeNeedsSnapshotRestore(syncStatus)) {
    throw new Error("Local node replay is divergent after snapshot restore. Stop the validator and run Fast Sync again.");
  }
}

async function autoRecoverValidator(config: ValidatorConfig, reason: string) {
  if (autoRecoveryInProgress || validatorStopRequested) return;
  autoRecoveryInProgress = true;
  validatorStatus = "starting";
  currentTask = "Recovering validator sync.";
  addLog(`Validator auto-recovery started: ${reason}`);
  try {
    await ensureNodeReadyForValidation(config);
    await seedLocalValidatorIdentity(config);
    autoRecoveryInProgress = false;
    await startValidator(config);
  } catch (error) {
    validatorStatus = "error";
    currentTask = "Validator recovery failed.";
    addLog(`Validator auto-recovery failed: ${errorMessage(error)}`);
    autoRecoveryInProgress = false;
  }
}

function spawnPython(
  pythonCmd: string,
  args: string[],
  env: NodeJS.ProcessEnv,
  onClose: (code: number | null) => void,
) {
  const corePath = getCorePath();
  const child = spawn(pythonCmd || "python", args, {
    cwd: corePath,
    env,
    shell: false,
  });
  child.stdout.on("data", (data) => String(data).split(/\r?\n/).forEach(addLog));
  child.stderr.on("data", (data) => String(data).split(/\r?\n/).forEach((line) => addLog(`ERROR: ${line}`)));
  child.on("close", onClose);
  child.on("error", (error) => {
    addLog(`Process error: ${error.message}`);
  });
  return child;
}

function runPythonOnce(pythonCmd: string, args: string[], env: NodeJS.ProcessEnv, timeoutMs = 120000): Promise<string> {
  return new Promise((resolve, reject) => {
    const corePath = getCorePath();
    const child = spawn(pythonCmd || "python", args, {
      cwd: corePath,
      env,
      shell: false,
    });
    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => {
      child.kill();
      reject(new Error(`Command timed out: python ${args.join(" ")}`));
    }, timeoutMs);

    child.stdout.on("data", (data) => {
      const text = String(data);
      stdout += text;
      text.split(/\r?\n/).forEach(addLog);
    });
    child.stderr.on("data", (data) => {
      const text = String(data);
      stderr += text;
      text.split(/\r?\n/).forEach((line) => addLog(`ERROR: ${line}`));
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      if (code === 0) {
        resolve(stdout.trim());
      } else {
        reject(new Error(stderr.trim() || stdout.trim() || `Command exited with code ${code}`));
      }
    });
    child.on("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
  });
}

function parseJsonOutput(text: string): unknown {
  const trimmed = text.trim();
  if (!trimmed) return {};
  const firstBrace = trimmed.indexOf("{");
  const firstBracket = trimmed.indexOf("[");
  const startCandidates = [firstBrace, firstBracket].filter((index) => index >= 0);
  if (startCandidates.length === 0) return { output: trimmed };
  const start = Math.min(...startCandidates);
  try {
    return JSON.parse(trimmed.slice(start));
  } catch {
    return { output: trimmed };
  }
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return String(error || "Unknown error");
}

export async function startNode(config: ValidatorConfig) {
  assertCoreReady();
  if (nodeProcess) {
    return { ok: true, message: "Node already running", localNodeUrl: getLocalNodeUrl(config) };
  }

  const pythonCmd = config.pythonCmd || "python";
  const port = normalizePort(config.nodePort);
  const env = buildEnv(config);
  const localNodeUrl = getLocalNodeUrl(config);

  nodeStopRequested = false;
  nodeStatus = "starting";
  currentTask = "Starting local node...";
  addLog(`Starting local Picoin node on ${localNodeUrl}`);
  addLog(`Data dir: ${getRuntimeDir()}`);
  addLog(`DB path: ${getDbPath()}`);

  nodeProcess = spawnPython(
    pythonCmd,
    ["-u", "-m", "picoin", "node", "start", "--host", "127.0.0.1", "--port", String(port)],
    env,
    (code) => {
      addLog(`Node process exited with code ${code}`);
      nodeProcess = null;
      const requestedStop = nodeStopRequested;
      nodeStopRequested = false;
      nodeStatus = requestedStop ? "stopped" : code === 0 ? "stopped" : "error";
    },
  );

  await waitForRpc(localNodeUrl);
  nodeStatus = "running";
  currentTask = "Local node running.";
  return { ok: true, message: "Node started", localNodeUrl, dataDir: getRuntimeDir(), dbPath: getDbPath() };
}

export function stopNode() {
  if (!nodeProcess) {
    nodeStatus = "stopped";
    return { ok: true, message: "Node already stopped" };
  }
  nodeStopRequested = true;
  nodeProcess.kill();
  nodeProcess = null;
  nodeStatus = "stopped";
  addLog("Node stopped by user");
  return { ok: true, message: "Node stopped" };
}

export async function registerValidator(config: ValidatorConfig) {
  assertCoreReady();
  const identityPath = getIdentityPath();
  if (fs.existsSync(identityPath)) {
    return { ok: true, message: "Validator identity already exists", identity: getSavedValidatorIdentity() };
  }
  const env = buildEnv(config);
  const output = await runPythonOnce(
    config.pythonCmd || "python",
    [
      "-u",
      "-m",
      "picoin",
      "validator",
      "--server",
      normalizeApiUrl(config.apiUrl),
      "--identity",
      identityPath,
      "register",
      "--name",
      configValue(config.validatorName, "picoin-desktop-validator"),
    ],
    env,
  );
  await seedLocalValidatorIdentity(config);
  return { ok: true, message: "Validator registered", output, identity: getSavedValidatorIdentity() };
}

export async function startValidator(config: ValidatorConfig) {
  assertCoreReady();
  if (validatorProcess) {
    return { ok: true, message: "Validator already running" };
  }
  if (!nodeProcess) {
    await startNode(config);
  }

  const identityPath = getIdentityPath();
  if (!fs.existsSync(identityPath)) {
    await registerValidator(config);
  }
  await seedLocalValidatorIdentity(config);
  await ensureNodeReadyForValidation(config);
  await seedLocalValidatorIdentity(config);

  const env = buildEnv(config);
  const localNodeUrl = getLocalNodeUrl(config);
  const sleep = normalizeSleep(config.validationSleep);

  validatorStopRequested = false;
  validatorStatus = "starting";
  currentTask = "Starting validator loop...";
  addLog(`Starting validator against ${normalizeApiUrl(config.apiUrl)}`);
  addLog(`Local node: ${localNodeUrl}`);
  addLog(`Identity: ${identityPath}`);

  validatorProcess = spawnPython(
    config.pythonCmd || "python",
    [
      "-u",
      "-m",
      "picoin",
      "validator",
      "--server",
      normalizeApiUrl(config.apiUrl),
      "--identity",
      identityPath,
      "validate",
      "--loops",
      "999999",
      "--sleep",
      String(sleep),
      "--node-server",
      localNodeUrl,
      "--node-timeout",
      NODE_HTTP_TIMEOUT_SECONDS,
    ],
    env,
    (code) => {
      addLog(`Validator process exited with code ${code}`);
      validatorProcess = null;
      const requestedStop = validatorStopRequested;
      validatorStopRequested = false;
      validatorStatus = requestedStop ? "stopped" : code === 0 ? "stopped" : "error";
      if (!requestedStop && code !== 0) {
        void autoRecoverValidator(config, `process exited with code ${code}`);
      }
    },
  );

  validatorStatus = "validating";
  currentTask = "Validator running.";
  return { ok: true, message: "Validator started", identityPath, localNodeUrl };
}

export function stopValidator() {
  if (!validatorProcess) {
    validatorStatus = "stopped";
    return { ok: true, message: "Validator already stopped" };
  }
  validatorStopRequested = true;
  validatorProcess.kill();
  validatorProcess = null;
  validatorStatus = "stopped";
  currentTask = "Validator stopped.";
  addLog("Validator stopped by user");
  return { ok: true, message: "Validator stopped" };
}

export async function restoreSnapshot(config: ValidatorConfig) {
  if (!nodeProcess) {
    await startNode(config);
  }
  const env = buildEnv(config);
  const output = await runPythonOnce(
    config.pythonCmd || "python",
    [
      "-u",
      "-m",
      "picoin",
      "node",
      "checkpoint",
      "--server",
      getLocalNodeUrl(config),
      "restore-peer",
      "--peer",
      normalizeApiUrl(config.apiUrl),
      "--source",
      "desktop-validator",
    ],
    env,
    180000,
  );
  return { ok: true, message: "Snapshot restored", result: parseJsonOutput(output) };
}

export async function catchUpNode(config: ValidatorConfig) {
  if (!nodeProcess) {
    await startNode(config);
  }
  const env = buildEnv(config);
  const output = await runPythonOnce(
    config.pythonCmd || "python",
    [
      "-u",
      "-m",
      "picoin",
      "node",
      "catch-up",
      "--server",
      getLocalNodeUrl(config),
      "--peer",
      normalizeApiUrl(config.apiUrl),
      "--max-rounds",
      "10",
      "--replay-limit",
      "50",
    ],
    env,
    180000,
  );
  return { ok: true, message: "Catch-up completed", result: parseJsonOutput(output) };
}

export async function stakeValidator(config: StakeConfig) {
  assertCoreReady();
  const identity = getSavedValidatorIdentity();
  if (!identity.exists || !identity.validatorId) {
    throw new Error("Register or load a validator identity before staking.");
  }
  if (!config.walletPath || !fs.existsSync(config.walletPath)) {
    throw new Error("Wallet JSON file not found.");
  }
  await seedLocalValidatorIdentity(config);
  const env = buildEnv(config);
  const output = await withCliWallet(config, (walletPath) =>
    runPythonOnce(
      config.pythonCmd || "python",
      [
        "-u",
        "-m",
        "picoin",
        "tx",
        "--server",
        normalizeApiUrl(config.apiUrl),
        "send",
        "--wallet",
        walletPath,
        "--type",
        "stake",
        "--stake-type",
        "validator",
        "--validator-id",
        identity.validatorId,
        "--amount",
        configValue(config.amount, MIN_VALIDATOR_STAKE),
        "--fee",
        configValue(config.fee, "0.001"),
      ],
      env,
    ),
  );
  return { ok: true, message: "Stake transaction submitted", result: parseJsonOutput(output) };
}

export async function unstakeValidator(config: StakeConfig) {
  assertCoreReady();
  const identity = getSavedValidatorIdentity();
  if (!identity.exists || !identity.validatorId) {
    throw new Error("Register or load a validator identity before unstaking.");
  }
  if (!config.walletPath || !fs.existsSync(config.walletPath)) {
    throw new Error("Wallet JSON file not found.");
  }
  await seedLocalValidatorIdentity(config);
  const env = buildEnv(config);
  const output = await withCliWallet(config, (walletPath) =>
    runPythonOnce(
      config.pythonCmd || "python",
      [
        "-u",
        "-m",
        "picoin",
        "tx",
        "--server",
        normalizeApiUrl(config.apiUrl),
        "send",
        "--wallet",
        walletPath,
        "--type",
        "unstake",
        "--stake-type",
        "validator",
        "--validator-id",
        identity.validatorId,
        "--amount",
        configValue(config.amount, MIN_VALIDATOR_STAKE),
        "--fee",
        configValue(config.fee, "0.001"),
      ],
      env,
    ),
  );
  return { ok: true, message: "Unstake transaction submitted", result: parseJsonOutput(output) };
}

async function withCliWallet(config: StakeConfig, action: (walletPath: string) => Promise<string>): Promise<string> {
  const wallet = readWalletJson(config.walletPath);
  const legacyWallet = normalizeLegacyWallet(wallet);
  if (legacyWallet) {
    if (legacyWallet === wallet) return action(config.walletPath);
    return withTemporaryWalletFile(legacyWallet, action);
  }

  const encryptedWallet = decryptPicoinKeystore(wallet, config.walletPassword || "");
  return withTemporaryWalletFile(encryptedWallet, action);
}

function readWalletJson(walletPath: string): Record<string, any> {
  try {
    const parsed = JSON.parse(fs.readFileSync(walletPath, "utf-8"));
    if (!parsed || typeof parsed !== "object") {
      throw new Error("wallet JSON must be an object");
    }
    return parsed as Record<string, any>;
  } catch (error) {
    throw new Error(`Unable to read wallet JSON: ${errorMessage(error)}`);
  }
}

function normalizeLegacyWallet(wallet: Record<string, any>): Record<string, any> | null {
  const privateKey = wallet.private_key || wallet.privateKey;
  const publicKey = wallet.public_key || wallet.publicKey;
  const address = wallet.address;
  if (!privateKey || !publicKey || !address) return null;
  if (wallet.private_key && wallet.public_key) return wallet;
  return {
    ...wallet,
    address,
    private_key: privateKey,
    public_key: publicKey,
  };
}

function decryptPicoinKeystore(wallet: Record<string, any>, password: string): Record<string, any> {
  if (!looksLikePicoinKeystore(wallet)) {
    throw new Error(
      "Selected wallet is not a CLI wallet and not an encrypted Picoin Wallet keystore. Select a wallet with private_key or an exported Picoin keystore.",
    );
  }
  if (!password) {
    throw new Error("Wallet password is required for encrypted Picoin Wallet keystores.");
  }
  try {
    const key = crypto.pbkdf2Sync(
      password,
      Buffer.from(String(wallet.salt), "base64url"),
      Number(wallet.iterations),
      32,
      "sha256",
    );
    const decipher = crypto.createDecipheriv("aes-256-gcm", key, Buffer.from(String(wallet.iv), "base64url"));
    decipher.setAuthTag(Buffer.from(String(wallet.tag), "base64url"));
    const plaintext = Buffer.concat([
      decipher.update(Buffer.from(String(wallet.ciphertext), "base64url")),
      decipher.final(),
    ]).toString("utf-8");
    const secrets = JSON.parse(plaintext);
    if (!secrets.privateKey) {
      throw new Error("keystore does not contain a private key");
    }
    return {
      address: wallet.address,
      public_key: wallet.publicKey,
      private_key: secrets.privateKey,
      network_id: wallet.network,
      chain_id: wallet.chainId,
    };
  } catch (error) {
    throw new Error(`Unable to unlock wallet keystore: ${errorMessage(error)}`);
  }
}

function looksLikePicoinKeystore(wallet: Record<string, any>): boolean {
  return (
    wallet.version === 1 &&
    wallet.cipher === "aes-256-gcm" &&
    wallet.kdf === "pbkdf2-sha256" &&
    Boolean(wallet.salt) &&
    Boolean(wallet.iv) &&
    Boolean(wallet.tag) &&
    Boolean(wallet.ciphertext) &&
    Boolean(wallet.address) &&
    Boolean(wallet.publicKey)
  );
}

async function withTemporaryWalletFile(wallet: Record<string, any>, action: (walletPath: string) => Promise<string>): Promise<string> {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "picoin-validator-wallet-"));
  const walletPath = path.join(tempDir, "wallet.json");
  try {
    fs.writeFileSync(walletPath, JSON.stringify(wallet, null, 2), { encoding: "utf-8", mode: 0o600 });
    return await action(walletPath);
  } finally {
    try {
      fs.rmSync(tempDir, { recursive: true, force: true });
    } catch {
      // Best-effort cleanup only.
    }
  }
}

export async function updateValidatorRewardWallet(config: ValidatorConfig) {
  const rewardWallet = configValue(config.rewardWallet, "");
  if (!rewardWallet) {
    throw new Error("Reward wallet is required.");
  }
  const identityPath = getIdentityPath();
  if (!fs.existsSync(identityPath)) {
    throw new Error("Validator identity does not exist yet.");
  }

  const identity = JSON.parse(fs.readFileSync(identityPath, "utf-8"));
  identity.reward_address = rewardWallet;
  fs.writeFileSync(identityPath, JSON.stringify(identity, null, 2), "utf-8");
  addLog(`Reward wallet saved locally: ${rewardWallet}`);

  let syncMessage = "Saved locally. It will sync to the coordinator when the validator is eligible and polls for jobs.";
  if (nodeProcess) {
    await seedLocalValidatorIdentity(config);
    try {
      await runPythonOnce(
        config.pythonCmd || "python",
        [
          "-u",
          "-m",
          "picoin",
          "validator",
          "--server",
          normalizeApiUrl(config.apiUrl),
          "--identity",
          identityPath,
          "validate",
          "--once",
          "--node-server",
          getLocalNodeUrl(config),
          "--node-timeout",
          NODE_HTTP_TIMEOUT_SECONDS,
        ],
        buildEnv(config),
        90000,
      );
      syncMessage = "Saved locally and sync attempt completed.";
    } catch (error) {
      syncMessage = `Saved locally. Coordinator sync pending: ${errorMessage(error)}`;
      addLog(syncMessage);
    }
  }

  return {
    ok: true,
    message: syncMessage,
    identity: getSavedValidatorIdentity(),
  };
}

async function seedLocalValidatorIdentity(config: ValidatorConfig) {
  if (!nodeProcess) return;
  const identityPath = getIdentityPath();
  if (!fs.existsSync(identityPath)) return;
  try {
    addLog("Seeding validator identity into local node state.");
    await runPythonOnce(
      config.pythonCmd || "python",
      [
        "-u",
        "-m",
        "picoin",
        "validator",
        "--server",
        getLocalNodeUrl(config),
        "--identity",
        identityPath,
        "validate",
        "--once",
        "--node-server",
        getLocalNodeUrl(config),
        "--node-timeout",
        NODE_HTTP_TIMEOUT_SECONDS,
      ],
      buildEnv(config),
      90000,
    );
  } catch (error) {
    addLog(`Local validator identity seed skipped: ${errorMessage(error)}`);
  }
}

export async function chooseWalletFile() {
  const result = await dialog.showOpenDialog({
    title: "Select Picoin wallet JSON",
    properties: ["openFile"],
    filters: [{ name: "JSON wallet", extensions: ["json"] }],
  });
  if (result.canceled || result.filePaths.length === 0) {
    return { canceled: true, path: null };
  }
  return { canceled: false, path: result.filePaths[0] };
}

export function getSavedValidatorIdentity() {
  const identityPath = getIdentityPath();
  if (!fs.existsSync(identityPath)) {
    return {
      exists: false,
      identityPath,
      validatorId: null,
      validatorName: null,
      rewardWallet: null,
      publicKey: null,
    };
  }
  try {
    const identity = JSON.parse(fs.readFileSync(identityPath, "utf-8"));
    return {
      exists: true,
      identityPath,
      validatorId: identity.validator_id || null,
      validatorName: identity.name || null,
      rewardWallet: identity.reward_address || null,
      publicKey: identity.public_key || null,
    };
  } catch (error) {
    addLog(`ERROR: Could not read validator identity: ${error instanceof Error ? error.message : String(error)}`);
    return {
      exists: false,
      identityPath,
      validatorId: null,
      validatorName: null,
      rewardWallet: null,
      publicKey: null,
    };
  }
}

export function getValidatorProcessStatus() {
  return {
    nodeStatus,
    validatorStatus,
    currentTask,
    logs: lastLogs,
    nodeRunning: nodeProcess !== null,
    validatorRunning: validatorProcess !== null,
    identity: getSavedValidatorIdentity(),
    dataDir: getRuntimeDir(),
    dbPath: getDbPath(),
  };
}

export function stopAll() {
  stopValidator();
  stopNode();
}
