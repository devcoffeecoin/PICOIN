import { useEffect, useMemo, useState } from "react";
import { Cpu, LogIn, Settings, Shield, Sparkles, Wifi } from "lucide-react";
import picoinLogo from "./assets/picoin-logo.png";
import type { MinerConfig, MinerIdentity, MinerStatus, MinerTask } from "./types";
import {
  buildCommitSignaturePayload,
  buildRevealSignaturePayload,
  calculatePiSegment,
  formatTimestamp,
  generateKeypair,
  hashResult,
  merkleProof,
  merkleRoot,
  signPayload,
} from "./miner";

const DEFAULT_CONFIG: MinerConfig = {
  apiUrl: "https://api.picoin.science",
  minerName: "picoin-miner",
  rewardAddress: "",
  speed: 7,
};

const CONFIG_STORAGE_KEY = "picoin-miner-config";
const IDENTITY_STORAGE_KEY = "picoin-miner-identity";

function normalizeUrl(rawUrl: string) {
  return rawUrl.replace(/\/$/, "");
}

function loadConfig(): MinerConfig {
  try {
    const stored = localStorage.getItem(CONFIG_STORAGE_KEY);
    if (!stored) return DEFAULT_CONFIG;
    return { ...DEFAULT_CONFIG, ...JSON.parse(stored) };
  } catch {
    return DEFAULT_CONFIG;
  }
}

function loadIdentity(): MinerIdentity | null {
  try {
    const stored = localStorage.getItem(IDENTITY_STORAGE_KEY);
    return stored ? JSON.parse(stored) : null;
  } catch {
    return null;
  }
}

function saveConfig(config: MinerConfig) {
  localStorage.setItem(CONFIG_STORAGE_KEY, JSON.stringify(config));
}

function saveIdentity(identity: MinerIdentity) {
  localStorage.setItem(IDENTITY_STORAGE_KEY, JSON.stringify(identity));
}

function addLog(previous: string[], message: string) {
  return [
    `${new Date().toLocaleTimeString()}: ${message}`,
    ...previous.slice(0, 49),
  ];
}

function formatValue(value: string | number | null | undefined) {
  return value === null || value === undefined ? "n/a" : String(value);
}

export default function App() {
  const [config, setConfig] = useState<MinerConfig>(loadConfig());
  const [identity, setIdentity] = useState<MinerIdentity | null>(loadIdentity());
  const [status, setStatus] = useState<MinerStatus>({ connected: false, message: "Waiting for API..." });
  const [activeTab, setActiveTab] = useState<"miner" | "settings">("miner");
  const [running, setRunning] = useState(false);
  const [log, setLog] = useState<string[]>([]);
  const [task, setTask] = useState<MinerTask | null>(null);
  const [accepted, setAccepted] = useState(0);
  const [rejected, setRejected] = useState(0);

  const apiBase = useMemo(() => normalizeUrl(config.apiUrl), [config.apiUrl]);

  useEffect(() => {
    saveConfig(config);
    setLog((current) => addLog(current, "Settings updated."));
  }, [config]);

  useEffect(() => {
    if (identity) {
      saveIdentity(identity);
    }
  }, [identity]);

  useEffect(() => {
    refreshStatus();
  }, [apiBase]);

  const updateConfig = (field: keyof MinerConfig, value: string | number) => {
    setConfig((current) => ({ ...current, [field]: value }));
  };

  const appendLog = (message: string) => setLog((current) => addLog(current, message));

  const registerMiner = async () => {
    const keypair = generateKeypair();
    const payload = {
      name: config.minerName,
      public_key: keypair.publicKey,
      reward_address: config.rewardAddress || undefined,
    };
    appendLog("Registering miner identity...");
    const result = await window.minerApi.request("POST", `${apiBase}/miners/register`, payload);
    if (typeof result !== "object" || result === null) {
      throw new Error("Unexpected register response");
    }

    const identityObject = {
      miner_id: (result as any).miner_id,
      name: (result as any).name,
      public_key: keypair.publicKey,
      private_key: keypair.privateKey,
      reward_address: config.rewardAddress || (result as any).reward_address,
      server_url: apiBase,
    } as MinerIdentity;

    setIdentity(identityObject);
    appendLog(`Miner registered: ${(result as any).miner_id}`);
    return identityObject;
  };

  const ensureIdentity = async () => {
    if (identity && identity.server_url === apiBase && identity.miner_id) {
      return identity;
    }
    return await registerMiner();
  };

  const fetchTask = async (minerId: string) => {
    appendLog("Requesting mining task...");
    const url = `${apiBase}/tasks/next?miner_id=${encodeURIComponent(minerId)}`;
    const result = await window.minerApi.request("GET", url);
    return result as MinerTask;
  };

  const createSamples = async (task: MinerTask, segment: string, root: string, challenge: any) => {
    const samples = [] as Array<{ position: number; digit: string; proof: any }>;
    for (const sample of challenge.samples || []) {
      const position = sample.position;
      samples.push({
        position,
        digit: segment[position - task.range_start],
        proof: await merkleProof(segment, task.range_start, position),
      });
    }
    return samples;
  };

  const apiRequest = async (method: string, path: string, body?: unknown) => {
    const url = `${apiBase}${path}`;
    appendLog(`${method} ${url}`);
    return await window.minerApi.request(method, url, body);
  };

  const refreshStatus = async () => {
    try {
      await window.minerApi.request("GET", `${apiBase}/health`);
      setStatus((current) => ({ ...current, connected: true, message: "Connected to Picoin API." }));
      appendLog("API connection established.");
    } catch (firstError) {
      appendLog(`Health check failed: ${(firstError as Error).message}`);
      try {
        await window.minerApi.request("GET", `${apiBase}/status`);
        setStatus((current) => ({ ...current, connected: true, message: "Connected to Picoin API (status endpoint)." }));
        appendLog("API status endpoint established.");
      } catch (secondError) {
        setStatus((current) => ({ ...current, connected: false, message: `Unable to connect to ${apiBase}` }));
        appendLog(`API status error: ${(secondError as Error).message}`);
      }
    }
  };

  const mineOnce = async () => {
    const identityToUse = await ensureIdentity();
    if (!identityToUse.miner_id) {
      throw new Error("Miner identity is not registered.");
    }

    const taskResult = await fetchTask(identityToUse.miner_id);
    setTask(taskResult);
    if (taskResult.status !== "assigned") {
      appendLog(`Task not assigned: ${taskResult.status ?? "unknown"}`);
      return;
    }

    appendLog(`Task received: ${taskResult.task_id}`);
    appendLog(`Computing ${taskResult.range_start}..${taskResult.range_end} ...`);

    const segment = await calculatePiSegment(taskResult.range_start, taskResult.range_end, taskResult.algorithm, config.speed);
    const resultHash = await hashResult(segment, taskResult.range_start, taskResult.range_end, taskResult.algorithm);
    const root = await merkleRoot(segment, taskResult.range_start);

    appendLog(`Segment calculated. Result hash: ${resultHash}`);

    const commitPayload = buildCommitSignaturePayload({
      taskId: taskResult.task_id,
      minerId: identityToUse.miner_id,
      rangeStart: taskResult.range_start,
      rangeEnd: taskResult.range_end,
      algorithm: taskResult.algorithm,
      resultHash,
      merkleRoot: root,
      signedAt: formatTimestamp(),
      txMerkleRoot: taskResult.tx_merkle_root || "",
      mempoolSnapshotId: taskResult.mempool_snapshot_id || "",
      selectedTxHashesHash: taskResult.selected_tx_hashes_hash || "",
      txCount: taskResult.tx_count || 0,
      txFeeTotalUnits: taskResult.tx_fee_total_units || 0,
      chainId: taskResult.chain_id,
      networkId: taskResult.network_id,
    });
    const commitSignature = signPayload(identityToUse.private_key, commitPayload);

    const commitResult = await apiRequest("POST", "/tasks/commit", {
      task_id: taskResult.task_id,
      miner_id: identityToUse.miner_id,
      result_hash: resultHash,
      merkle_root: root,
      tx_merkle_root: taskResult.tx_merkle_root || "",
      mempool_snapshot_id: taskResult.mempool_snapshot_id || "",
      selected_tx_hashes_hash: taskResult.selected_tx_hashes_hash || "",
      tx_count: taskResult.tx_count || 0,
      tx_fee_total_units: taskResult.tx_fee_total_units || 0,
      compute_ms: 0,
      signature: commitSignature,
      signed_at: commitPayload.signed_at,
    });

    if (!(commitResult as any).accepted) {
      setRejected((previous) => previous + 1);
      appendLog(`Commit rejected: ${(commitResult as any).message || "unknown"}`);
      return;
    }

    appendLog("Commit accepted. Preparing reveal...");
    const challenge = commitResult as any;
    const samples = await createSamples(taskResult, segment, root, challenge);
    const revealPayload = buildRevealSignaturePayload({
      taskId: taskResult.task_id,
      minerId: identityToUse.miner_id,
      merkleRoot: root,
      challengeSeed: challenge.challenge_seed || "",
      signedAt: formatTimestamp(),
      txMerkleRoot: taskResult.tx_merkle_root || "",
      mempoolSnapshotId: taskResult.mempool_snapshot_id || "",
      selectedTxHashesHash: taskResult.selected_tx_hashes_hash || "",
    });
    const revealSignature = signPayload(identityToUse.private_key, revealPayload);

    const revealResult = await apiRequest("POST", "/tasks/reveal", {
      task_id: taskResult.task_id,
      miner_id: identityToUse.miner_id,
      samples,
      tx_merkle_root: taskResult.tx_merkle_root || "",
      mempool_snapshot_id: taskResult.mempool_snapshot_id || "",
      selected_tx_hashes_hash: taskResult.selected_tx_hashes_hash || "",
      tx_count: taskResult.tx_count || 0,
      tx_fee_total_units: taskResult.tx_fee_total_units || 0,
      signature: revealSignature,
      signed_at: revealPayload.signed_at,
    });

    if ((revealResult as any).accepted) {
      setAccepted((previous) => previous + 1);
      appendLog(`Block accepted: ${(revealResult as any).block?.height ?? "unknown"}`);
    } else {
      setRejected((previous) => previous + 1);
      appendLog(`Reveal rejected: ${(revealResult as any).message || "unknown"}`);
    }
  };

  const startMining = async () => {
    setRunning(true);
    appendLog("Miner started.");
    try {
      await mineOnce();
    } catch (error) {
      appendLog(`Mining error: ${(error as Error).message}`);
    }
    setRunning(false);
  };

  const stopMining = () => {
    setRunning(false);
    appendLog("Miner stopped.");
  };

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <img className="brand-mark" src={picoinLogo} alt="Picoin Miner" />
          <div>
            <strong>Picoin Miner</strong>
            <span>GUI mining dashboard</span>
          </div>
        </div>
        <nav>
          <button className={activeTab === "miner" ? "nav-item active" : "nav-item"} onClick={() => setActiveTab("miner")}>Miner</button>
          <button className={activeTab === "settings" ? "nav-item active" : "nav-item"} onClick={() => setActiveTab("settings")}>Settings</button>
        </nav>
        <div className="sidebar-footer">
          <Shield size={18} />
          <span>Local settings, remote API</span>
        </div>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">Picoin Desktop Miner</p>
            <h1>{activeTab === "miner" ? "Mining Dashboard" : "Miner Settings"}</h1>
          </div>
          <div className="topbar-actions">
            <span className={`status-pill ${status.connected ? "online" : "offline"}`}>
              {status.connected ? "Online" : "Offline"}
            </span>
            <button className="icon-button" onClick={refreshStatus} title="Refresh status">
              <Wifi size={18} />
            </button>
          </div>
        </header>

        {status.message && <div className="notice"><span>{status.message}</span></div>}

        {activeTab === "miner" ? (
          <section className="page-grid dashboard-grid">
            <div className="panel hero-panel">
              <div>
                <p className="eyebrow">Active Miner</p>
                <div className="balance">{identity?.miner_id ?? "Not registered"}</div>
                <p className="muted">{identity?.name ?? "Set your miner name and register."}</p>
              </div>
              <img className="hero-logo" src={picoinLogo} alt="Picoin" />
            </div>

            <div className="panel">
              <h2>Mining Control</h2>
              <div className="metric-list">
                <div className="metric"><span className="metric-icon"><Cpu size={18} /></span><strong>Speed</strong><span>{config.speed}</span></div>
                <div className="metric"><span className="metric-icon"><Settings size={18} /></span><strong>Tasks</strong><span>{task?.task_id ?? "none"}</span></div>
                <div className="metric"><span className="metric-icon"><Sparkles size={18} /></span><strong>Accepted</strong><span>{accepted}</span></div>
                <div className="metric"><span className="metric-icon"><LogIn size={18} /></span><strong>Rejected</strong><span>{rejected}</span></div>
              </div>
              <div className="button-row">
                <button className="primary-button" onClick={startMining} disabled={running}>
                  {running ? "Mining..." : "Start Mining"}
                </button>
                <button className="secondary-button" onClick={stopMining} disabled={!running}>
                  Stop
                </button>
              </div>
            </div>

            <div className="panel wide-panel">
              <h2>Current task</h2>
              {task ? (
                <div className="table">
                  <div className="table-row"><span>ID</span><span>{task.task_id}</span></div>
                  <div className="table-row"><span>Range</span><span>{task.range_start}..{task.range_end}</span></div>
                  <div className="table-row"><span>Algorithm</span><span>{task.algorithm}</span></div>
                  <div className="table-row"><span>Status</span><span>{task.status ?? "assigned"}</span></div>
                </div>
              ) : (
                <p className="muted">No task acquired yet.</p>
              )}
            </div>

            <div className="panel wide-panel">
              <h2>Activity log</h2>
              <div className="log-box">
                {log.length === 0 ? <p className="muted">Waiting for mining events...</p> : log.map((entry, index) => <div key={index}>{entry}</div>)}
              </div>
            </div>
          </section>
        ) : (
          <section className="page-grid settings-grid">
            <div className="panel single-column">
              <h2>Miner configuration</h2>
              <div className="form-row"><span>Miner name</span><input value={config.minerName} onChange={(event) => updateConfig("minerName", event.target.value)} /></div>
              <div className="form-row"><span>Reward wallet address</span><input value={config.rewardAddress} onChange={(event) => updateConfig("rewardAddress", event.target.value)} /></div>
              <div className="form-row"><span>API node URL</span><input value={config.apiUrl} onChange={(event) => updateConfig("apiUrl", event.target.value)} /></div>
              <div className="form-row"><span>Mining speed</span><input type="range" min="1" max="10" value={config.speed} onChange={(event) => updateConfig("speed", Number(event.target.value))} /></div>
              <div className="form-row"><span>Speed value</span><div>{config.speed}</div></div>
              <button className="primary-button" onClick={async () => {
                try {
                  const id = await registerMiner();
                  setIdentity(id);
                } catch (err) {
                  appendLog(`Registration failed: ${(err as Error).message}`);
                }
              }}>
                Register Miner
              </button>
            </div>

            <div className="panel">
              <h2>API connection</h2>
              <div className="metric-list">
                <div className="metric"><span className="metric-icon"><Wifi size={18} /></span><strong>API</strong><span>{config.apiUrl}</span></div>
                <div className="metric"><span className="metric-icon"><Shield size={18} /></span><strong>Wallet</strong><span>{config.rewardAddress || "not set"}</span></div>
              </div>
              <p className="muted">This miner connects to a Picoin API endpoint and submits mining commits and reveals.</p>
            </div>
          </section>
        )}
      </main>
    </div>
  );
}
