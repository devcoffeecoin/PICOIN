import { useEffect, useState } from "react";
import {
  Activity,
  Cpu,
  Gauge,
  Network,
  Play,
  Power,
  RefreshCw,
  Settings,
  Square,
} from "lucide-react";
import picoinLogo from "./picoin-logo.png";

type Page = "dashboard" | "settings";

type MinerStatus = "stopped" | "starting" | "mining" | "error";
type MinerSettings = {
  minerName: string;
  rewardWallet: string;
  apiUrl: string;
  networkId: string;
  chainId: string;
  miningIntensity: number;
};
type SavedMinerIdentity = {
  exists?: boolean;
  identityPath?: string;
  minerName?: string | null;
  rewardWallet?: string | null;
  minerId?: string | null;
};

const MAX_LOG_LINES = 15;
const SETTINGS_STORAGE_KEY = "picoin-desktop-miner-settings";
const DEFAULT_SETTINGS: MinerSettings = {
  minerName: "Picoin Desktop Miner",
  rewardWallet: "",
  apiUrl: "https://api.picoin.science",
  networkId: "public-testnet",
  chainId: "picoin-public-testnet-v018",
  miningIntensity: 75,
};

export default function App() {
  const [savedSettings] = useState(loadSavedSettings);
  const [page, setPage] = useState<Page>("dashboard");
  const [status, setStatus] = useState<MinerStatus>("stopped");
  const [hashrate, setHashrate] = useState("0 H/s");
  const [activeWorkers, setActiveWorkers] = useState(0);
  const [activeIntensity, setActiveIntensity] = useState(savedSettings.miningIntensity);
  const [idleDelaySeconds, setIdleDelaySeconds] = useState(1);
  const [currentTask, setCurrentTask] = useState("Waiting for task...");
  const [apiStatus, setApiStatus] = useState("offline");
  const [minerName, setMinerName] = useState(savedSettings.minerName);
  const [rewardWallet, setRewardWallet] = useState(savedSettings.rewardWallet);
  const [apiUrl, setApiUrl] = useState(savedSettings.apiUrl);
  const [networkId, setNetworkId] = useState(savedSettings.networkId);
  const [chainId, setChainId] = useState(savedSettings.chainId);
  const [logs, setLogs] = useState<string[]>([]);
  const [miningIntensity, setMiningIntensity] = useState(savedSettings.miningIntensity);

  function addLog(message: string) {
    setLogs((prev) => [`[${new Date().toLocaleTimeString()}] ${message}`, ...prev].slice(0, MAX_LOG_LINES));
  }

  async function apiRequest(method: string, path: string, body?: unknown) {
    const url = path.startsWith("http") ? path : `${apiUrl}${path}`;
    return window.minerApi.request(method, url, body);
  }

  async function startMining() {
  try {
      setStatus("starting");
      setCurrentTask("Starting miner...");
      addLog("Starting mining process.");

     const result = await window.minerApi.start({
      minerName,
      rewardWallet,
      apiNodeUrl: apiUrl,
      networkId,
      chainId,
      miningIntensity,
    });

      setStatus("mining");
      if (typeof result?.workers === "number") setActiveWorkers(result.workers);
      if (typeof result?.miningIntensity === "number") setActiveIntensity(result.miningIntensity);
      if (typeof result?.idleDelaySeconds === "number") setIdleDelaySeconds(result.idleDelaySeconds);
      setCurrentTask("Miner running.");
      addLog(result.message || "Miner started.");
    } catch (error) {
      setStatus("error");
      setCurrentTask("Miner failed to start.");
      addLog(errorMessage(error));
    }
  }

  async function stopMining() {
  try {
      const result = await window.minerApi.stop();

      setStatus("stopped");
      setHashrate("0 H/s");
      setActiveWorkers(0);
      setCurrentTask("Mining stopped.");
      addLog(result.message || "Miner stopped.");
    } catch (error) {
      addLog(errorMessage(error));
    }
  }

async function registerMiner() {
  addLog("Register button clicked.");

  try {
    const result = await window.minerApi.register({
      minerName,
      rewardWallet,
      apiNodeUrl: apiUrl,
      networkId,
      chainId,
    });

    addLog(result.message || "Miner registration started.");
  } catch (error) {
    addLog(errorMessage(error));
  }
}

  async function refreshStatus() {
  try {
      const result = await window.minerApi.status();

      if (result?.status) setStatus(result.status);
      if (result?.currentTask) setCurrentTask(result.currentTask);
      if (result?.hashrate) setHashrate(result.hashrate);
      if (typeof result?.workers === "number") setActiveWorkers(result.workers);
      if (typeof result?.miningIntensity === "number") setActiveIntensity(result.miningIntensity);
      if (typeof result?.idleDelaySeconds === "number") setIdleDelaySeconds(result.idleDelaySeconds);
      if (result?.status === "stopped" && !result?.running) setHashrate("0 H/s");
      if (Array.isArray(result?.logs) && result.logs.length > 0) {
        setLogs(result.logs.slice(0, MAX_LOG_LINES));
      }

      setApiStatus("online");
    } catch {
      
      setApiStatus("offline");
    }
  }

  async function loadSavedIdentity() {
    try {
      const identity = (await window.minerApi.identity()) as SavedMinerIdentity;
      if (!identity?.exists) return;

      if (identity.minerName) setMinerName(identity.minerName);
      if (identity.rewardWallet) setRewardWallet(identity.rewardWallet);

      const label = identity.minerId || identity.identityPath || "miner identity";
      addLog(`Loaded miner identity: ${label}`);
    } catch (error) {
      addLog(errorMessage(error));
    }
  }

  useEffect(() => {
    void loadSavedIdentity();
  }, []);

  useEffect(() => {
    saveSettings({
      minerName,
      rewardWallet,
      apiUrl,
      networkId,
      chainId,
      miningIntensity,
    });
  }, [minerName, rewardWallet, apiUrl, networkId, chainId, miningIntensity]);

  useEffect(() => {
    void refreshStatus();
    const timer = window.setInterval(() => void refreshStatus(), 5000);
    return () => window.clearInterval(timer);
  }, [apiUrl]);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <img className="brand-mark" src={picoinLogo} alt="Picoin" />
          <div>
            <strong>Picoin</strong>
            <span>Desktop Miner</span>
          </div>
        </div>

        <nav>
          <button className={page === "dashboard" ? "nav-item active" : "nav-item"} onClick={() => setPage("dashboard")}>
            <Cpu size={20} />
            <span>Miner</span>
          </button>

          <button className={page === "settings" ? "nav-item active" : "nav-item"} onClick={() => setPage("settings")}>
            <Settings size={20} />
            <span>Settings</span>
          </button>
        </nav>

        <div className="sidebar-footer">
          <Power size={18} />
          <span>Proof of Pi mining node</span>
        </div>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">Picoin Desktop Miner V1</p>
            <h1>{page === "dashboard" ? "Miner Dashboard" : "Miner Settings"}</h1>
          </div>

          <div className="topbar-actions">
            <span className={`status-pill ${apiStatus === "online" ? "online" : "offline"}`}>
              {apiStatus}
            </span>
            <button className="icon-button" onClick={() => void refreshStatus()} title="Refresh">
              <RefreshCw size={18} />
            </button>
          </div>
        </header>

        {page === "dashboard" && (
          <section className="page-grid dashboard-grid">
            <section className="panel hero-panel">
              <div>
                <p className="eyebrow">Miner</p>
                <div className="balance">{minerName}</div>
                <p className="muted">{rewardWallet || "No reward wallet configured"}</p>
              </div>
              <img className="hero-logo" src={picoinLogo} alt="Picoin" />
            </section>

            <section className="panel">
              <h2>Mining Status</h2>
              <div className="metric-list">
                <Metric icon={<Power />} label="Status" value={status} />
                <Metric icon={<Gauge />} label="Hashrate" value={hashrate} />
                <Metric icon={<Cpu />} label="CPU" value={status === "mining" ? `${activeIntensity}% / ${activeWorkers} workers` : `${miningIntensity}% ready`} />
                <Metric icon={<Network />} label="API" value={apiStatus} />
              </div>

              <div className="button-row">
                <button className="primary-button" onClick={() => void startMining()} disabled={status === "mining" || status === "starting"}>
                  <Play size={18} />
                  Start Mining
                </button>

                <button className="secondary-button" onClick={() => void stopMining()}>
                  <Square size={18} />
                  Stop
                </button>
              </div>
            </section>

            <section className="panel">
              <h2>Current Task</h2>
              <div className="large-value">{currentTask}</div>
              <p className="muted">API node: {apiUrl}</p>
              <p className="muted">network_id: {networkId}</p>
              <p className="muted">chain_id: {chainId}</p>
              <p className="muted">mining intensity: {status === "mining" ? activeIntensity : miningIntensity}%</p>
              <p className="muted">idle delay: {idleDelaySeconds}s</p>
            </section>

            <section className="panel wide-panel">
              <h2>Activity Log</h2>
              <div className="table">
                {logs.length === 0 && <p className="muted">No activity yet.</p>}
                {logs.map((log, index) => (
                  <div className="table-row" key={index}>
                    <span>{log}</span>
                  </div>
                ))}
              </div>
            </section>
          </section>
        )}

        {page === "settings" && (
          <section className="page-grid settings-grid">
            <section className="panel wide-panel">
              <h2>Miner Settings</h2>

             
              <label className="form-row">
                <span>Miner name</span>
                <input value={minerName} onChange={(e) => setMinerName(e.target.value)} />
              </label>

              <label className="form-row">
                <span>Reward wallet address</span>
                <input value={rewardWallet} onChange={(e) => setRewardWallet(e.target.value)} placeholder="PI..." />
              </label>

              <label className="form-row">
                <span>API node URL</span>
                <input value={apiUrl} onChange={(e) => setApiUrl(e.target.value)} />
              </label>

              <label className="form-row">
                <span>network_id</span>
                <input value={networkId} onChange={(e) => setNetworkId(e.target.value)} placeholder="public-testnet" />
              </label>

              <label className="form-row">
                <span>chain_id</span>
                <input value={chainId} onChange={(e) => setChainId(e.target.value)} placeholder="picoin-public-testnet-v018" />
              </label>
              
              <label className="form-row">
                <span>Mining speed / CPU usage</span>

                <div style={{ padding: "10px 0" }}>
                    <input
                    type="range"
                    min="1"
                    max="100"
                    value={miningIntensity}
                    onChange={(e) => setMiningIntensity(Number(e.target.value))}
                    style={{
                        width: "100%",
                        accentColor: "#47dcb8",
                    }}
                    />

                    <div
                    style={{
                        marginTop: 10,
                        display: "flex",
                        justifyContent: "space-between",
                        color: "#91a0b9",
                        fontSize: 13,
                    }}
                    >
                    <span>Low</span>

                    <strong style={{ color: "#47dcb8" }}>
                        {miningIntensity}%
                    </strong>

                    <span>Max</span>
                    </div>
                </div>
                </label>  


              <button className="primary-button" onClick={() => void registerMiner()}>
                Register Miner
              </button>
            </section>

            <section className="panel">
              <h2>API Connection</h2>
              <div className="metric-list">
                <Metric icon={<Power />} label="Status" value={apiStatus} />
                <Metric icon={<Network />} label="Node" value={apiUrl} />
                <Metric icon={<Network />} label="Network" value={networkId} />
                <Metric icon={<Activity />} label="Chain ID" value={chainId} />
                <Metric icon={<Cpu />} label="Applied CPU" value={`${activeIntensity}% / ${activeWorkers} workers`} />
              </div>
            </section>
          </section>
        )}
      </main>
    </div>
  );
}

function Metric({ icon, label, value }: { icon: JSX.Element; label: string; value: React.ReactNode }) {
  return (
    <div className="metric">
      <span className="metric-icon">{icon}</span>
      <span className="metric-label">{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return String(error || "Unknown error");
}

function loadSavedSettings(): MinerSettings {
  try {
    const raw = window.localStorage.getItem(SETTINGS_STORAGE_KEY);
    if (!raw) return DEFAULT_SETTINGS;
    const saved = JSON.parse(raw) as Partial<MinerSettings>;
    return {
      ...DEFAULT_SETTINGS,
      ...saved,
      miningIntensity: Number(saved.miningIntensity || DEFAULT_SETTINGS.miningIntensity),
    };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

function saveSettings(settings: MinerSettings) {
  window.localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(settings));
}

