import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  Activity,
  BadgeCheck,
  Cable,
  CheckCircle2,
  CircleDollarSign,
  Database,
  KeyRound,
  Network,
  Play,
  RefreshCw,
  RotateCw,
  Settings,
  ShieldCheck,
  Square,
  Upload,
  Wallet,
} from "lucide-react";
import picoinLogo from "./picoin-logo.png";

type Page = "dashboard" | "stake" | "settings";

type ValidatorSettings = {
  validatorName: string;
  rewardWallet: string;
  apiUrl: string;
  networkId: string;
  chainId: string;
  nodePort: number;
  nodeAddress: string;
  validationSleep: number;
  walletPath: string;
  stakeAmount: string;
  stakeFee: string;
};

type Identity = {
  exists?: boolean;
  identityPath?: string;
  validatorId?: string | null;
  validatorName?: string | null;
  rewardWallet?: string | null;
  publicKey?: string | null;
};

type ProcessStatus = {
  nodeStatus?: string;
  validatorStatus?: string;
  currentTask?: string;
  logs?: string[];
  nodeRunning?: boolean;
  validatorRunning?: boolean;
  identity?: Identity;
  dataDir?: string;
  dbPath?: string;
};

const SETTINGS_STORAGE_KEY = "picoin-desktop-validator-settings";
const MAINNET_NETWORK_ID = "picoin-mainnet-v1";
const MAINNET_CHAIN_ID = "314159";
const CANONICAL_MAINNET_API = "https://api.picoin.science";
const MAINNET_GENESIS_HASH = "da286143167d14044c053fbb23fcf4673bb11bcd34fb1a11e5510ee8f8edb6e7";
const DEFAULT_SETTINGS: ValidatorSettings = {
  validatorName: "picoin-desktop-validator",
  rewardWallet: "",
  apiUrl: CANONICAL_MAINNET_API,
  networkId: MAINNET_NETWORK_ID,
  chainId: MAINNET_CHAIN_ID,
  nodePort: 8131,
  nodeAddress: "",
  validationSleep: 1,
  walletPath: "",
  stakeAmount: "31.416",
  stakeFee: "0.001",
};

export default function App() {
  const [savedSettings] = useState(loadSavedSettings);
  const [page, setPage] = useState<Page>("dashboard");
  const [validatorName, setValidatorName] = useState(savedSettings.validatorName);
  const [rewardWallet, setRewardWallet] = useState(savedSettings.rewardWallet);
  const [apiUrl, setApiUrl] = useState(savedSettings.apiUrl);
  const [networkId, setNetworkId] = useState(savedSettings.networkId);
  const [chainId, setChainId] = useState(savedSettings.chainId);
  const [nodePort, setNodePort] = useState(savedSettings.nodePort);
  const [nodeAddress, setNodeAddress] = useState(savedSettings.nodeAddress);
  const [validationSleep, setValidationSleep] = useState(savedSettings.validationSleep);
  const [walletPath, setWalletPath] = useState(savedSettings.walletPath);
  const [walletPassword, setWalletPassword] = useState("");
  const [stakeAmount, setStakeAmount] = useState(savedSettings.stakeAmount);
  const [stakeFee, setStakeFee] = useState(savedSettings.stakeFee);
  const [processStatus, setProcessStatus] = useState<ProcessStatus>({});
  const [apiStatus, setApiStatus] = useState("offline");
  const [protocol, setProtocol] = useState<any>(null);
  const [syncStatus, setSyncStatus] = useState<any>(null);
  const [validatorsStatus, setValidatorsStatus] = useState<any>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  const localNodeUrl = useMemo(() => `http://127.0.0.1:${nodePort || 8131}`, [nodePort]);
  const identity = processStatus.identity || {};
  const validatorRow = useMemo(() => {
    const rows = validatorsStatus?.validators;
    if (!Array.isArray(rows) || !identity.validatorId) return null;
    return rows.find((row: any) => row.validator_id === identity.validatorId) || null;
  }, [validatorsStatus, identity.validatorId]);

  const settings = useMemo<ValidatorSettings>(
    () => ({
      validatorName,
      rewardWallet,
      apiUrl: apiUrl.replace(/\/$/, ""),
      networkId,
      chainId,
      nodePort,
      nodeAddress,
      validationSleep,
      walletPath,
      stakeAmount,
      stakeFee,
    }),
    [validatorName, rewardWallet, apiUrl, networkId, chainId, nodePort, nodeAddress, validationSleep, walletPath, stakeAmount, stakeFee],
  );

  function addLog(message: string) {
    setLogs((prev) => [`[${new Date().toLocaleTimeString()}] ${message}`, ...prev].slice(0, 20));
  }

  async function apiRequest(method: string, url: string, body?: unknown) {
    return window.validatorApi.request(method, url, body);
  }

  async function refreshAll() {
    try {
      const status = (await window.validatorApi.status()) as ProcessStatus;
      setProcessStatus(status);
      if (Array.isArray(status.logs)) setLogs(status.logs);
      if (status.identity?.validatorName && !validatorName) setValidatorName(status.identity.validatorName);
      if (status.identity?.rewardWallet && !rewardWallet) setRewardWallet(status.identity.rewardWallet);
    } catch (error) {
      addLog(errorMessage(error));
    }

    try {
      const nextProtocol = await apiRequest("GET", `${settings.apiUrl}/protocol`);
      setProtocol(nextProtocol);
      setApiStatus("online");
    } catch {
      setApiStatus("offline");
    }

    try {
      setSyncStatus(await apiRequest("GET", `${localNodeUrl}/node/sync-status`));
    } catch {
      setSyncStatus(null);
    }

    try {
      setValidatorsStatus(await apiRequest("GET", `${settings.apiUrl}/validators/status`));
    } catch {
      setValidatorsStatus(null);
    }
  }

  async function runAction(label: string, action: () => Promise<any>) {
    setBusy(true);
    addLog(label);
    try {
      const result = await action();
      addLog(result?.message || "Done.");
      await refreshAll();
      return result;
    } catch (error) {
      addLog(errorMessage(error));
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function registerIdentity() {
    const result = await runAction("Registering validator identity.", () => window.validatorApi.register(settings));
    const nextIdentity = result?.identity as Identity | undefined;
    if (nextIdentity?.validatorName) setValidatorName(nextIdentity.validatorName);
    if (nextIdentity?.rewardWallet) setRewardWallet(nextIdentity.rewardWallet);
  }

  async function updateRewardWallet() {
    if (!identity.exists) {
      addLog("Register identity before updating the reward wallet.");
      return;
    }
    const result = await runAction("Saving validator reward wallet.", () => window.validatorApi.updateRewardWallet(settings));
    const nextIdentity = result?.identity as Identity | undefined;
    if (nextIdentity?.rewardWallet) setRewardWallet(nextIdentity.rewardWallet);
  }

  async function chooseWallet() {
    const result = await window.validatorApi.chooseWallet();
    if (!result?.canceled && result?.path) {
      setWalletPath(result.path);
      addLog("Wallet file selected.");
    }
  }

  async function submitStake() {
    if (!identity.validatorId) {
      addLog("Validator identity is required before staking.");
      return;
    }
    if (!walletPath) {
      addLog("Select a wallet JSON before staking.");
      return;
    }
    const ok = window.confirm(`Stake ${stakeAmount} PI to ${identity.validatorId}?`);
    if (!ok) return;
    const result = await runAction("Submitting validator stake transaction.", () =>
      window.validatorApi.stake({ ...settings, walletPath, walletPassword, amount: stakeAmount, fee: stakeFee }),
    );
    setWalletPassword("");
    const txHash = result?.result?.tx_hash;
    if (txHash) addLog(`Stake tx: ${txHash}`);
  }

  async function submitUnstake() {
    if (!identity.validatorId) {
      addLog("Validator identity is required before unstaking.");
      return;
    }
    if (!walletPath) {
      addLog("Select the stake owner wallet JSON before unstaking.");
      return;
    }
    const ok = window.confirm(`Unstake ${stakeAmount} PI from ${identity.validatorId}?`);
    if (!ok) return;
    const result = await runAction("Submitting validator unstake transaction.", () =>
      window.validatorApi.unstake({ ...settings, walletPath, walletPassword, amount: stakeAmount, fee: stakeFee }),
    );
    setWalletPassword("");
    const txHash = result?.result?.tx_hash;
    if (txHash) addLog(`Unstake tx: ${txHash}`);
  }

  useEffect(() => {
    saveSettings(settings);
  }, [settings]);

  useEffect(() => {
    void refreshAll();
    const timer = window.setInterval(() => void refreshAll(), 5000);
    return () => window.clearInterval(timer);
  }, [settings.apiUrl, localNodeUrl]);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <img className="brand-mark" src={picoinLogo} alt="Picoin" />
          <div>
            <strong>Picoin</strong>
            <span>Desktop Validator</span>
          </div>
        </div>

        <nav>
          <NavButton active={page === "dashboard"} onClick={() => setPage("dashboard")} icon={<ShieldCheck />} label="Validator" />
          <NavButton active={page === "stake"} onClick={() => setPage("stake")} icon={<CircleDollarSign />} label="Stake" />
          <NavButton active={page === "settings"} onClick={() => setPage("settings")} icon={<Settings />} label="Settings" />
        </nav>

        <div className="sidebar-footer">
          <BadgeCheck size={18} />
          <span>{networkId}</span>
        </div>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">Picoin Desktop Validator V1</p>
            <h1>{page === "dashboard" ? "Validator Dashboard" : page === "stake" ? "Validator Stake" : "Validator Settings"}</h1>
          </div>

          <div className="topbar-actions">
            <span className={`status-pill ${apiStatus === "online" ? "online" : "offline"}`}>{apiStatus}</span>
            <button className="icon-button" onClick={() => void refreshAll()} title="Refresh">
              <RefreshCw />
            </button>
          </div>
        </header>

        {page === "dashboard" && (
          <section className="page-grid dashboard-grid">
            <section className="panel hero-panel">
              <div>
                <p className="eyebrow">Validator</p>
                <div className="identity-value">{identity.validatorId || "No identity"}</div>
                <p className="muted">{rewardWallet || identity.rewardWallet || "No reward wallet configured"}</p>
              </div>
              <img className="hero-logo" src={picoinLogo} alt="Picoin" />
            </section>

            <section className="panel">
              <h2>Status</h2>
              <div className="metric-list">
                <Metric icon={<Database />} label="Node" value={processStatus.nodeStatus || "stopped"} />
                <Metric icon={<ShieldCheck />} label="Validator" value={processStatus.validatorStatus || "stopped"} />
                <Metric icon={<Activity />} label="Eligible" value={validatorRow ? String(Boolean(validatorRow.eligible)) : "unknown"} />
                <Metric icon={<Network />} label="Quorum" value={`${validatorsStatus?.eligible_validators ?? "-"} / ${validatorsStatus?.required_validator_approvals ?? "-"}`} />
              </div>
              <div className="button-row">
                <button className="primary-button" disabled={busy || processStatus.validatorRunning} onClick={() => void runAction("Starting validator.", () => window.validatorApi.start(settings))}>
                  <Play />
                  Start Validator
                </button>
                <button className="secondary-button" disabled={busy} onClick={() => void runAction("Stopping validator.", () => window.validatorApi.stop())}>
                  <Square />
                  Stop
                </button>
              </div>
            </section>

            <section className="panel">
              <h2>Local Node</h2>
              <div className="metric-list">
                <Metric icon={<Cable />} label="RPC" value={localNodeUrl} />
                <Metric icon={<Database />} label="Height" value={syncStatus?.effective_latest_block_height ?? syncStatus?.latest_block_height ?? "-"} />
                <Metric icon={<RotateCw />} label="Replay" value={syncStatus?.replay?.sync_status || "-"} />
                <Metric icon={<Activity />} label="Lag" value={validatorRow?.sync_lag ?? "-"} />
              </div>
              <div className="button-row">
                <button className="secondary-button" disabled={busy || processStatus.nodeRunning} onClick={() => void runAction("Starting local node.", () => window.validatorApi.startNode(settings))}>
                  <Play />
                  Start Node
                </button>
                <button className="secondary-button" disabled={busy} onClick={() => void runAction("Fast syncing from API.", () => window.validatorApi.restoreSnapshot(settings))}>
                  <Upload />
                  Fast Sync
                </button>
                <button className="secondary-button" disabled={busy} onClick={() => void runAction("Catching up local node.", () => window.validatorApi.catchUp(settings))}>
                  <RefreshCw />
                  Catch Up
                </button>
                <button className="secondary-button" disabled={busy} onClick={() => void runAction("Stopping local node.", () => window.validatorApi.stopNode())}>
                  <Square />
                  Stop Node
                </button>
              </div>
            </section>

            <section className="panel">
              <h2>Network</h2>
              <div className="metric-list">
                <Metric icon={<Network />} label="network_id" value={protocol?.network_id || networkId} />
                <Metric icon={<Activity />} label="chain_id" value={String(protocol?.chain_id ?? chainId)} />
                <Metric icon={<CheckCircle2 />} label="protocol" value={protocol?.protocol_version || "-"} />
                <Metric icon={<CircleDollarSign />} label="Min stake" value={`${protocol?.min_validator_stake ?? "31.416"} PI`} />
              </div>
            </section>

            <section className="panel wide-panel">
              <h2>Activity Log</h2>
              <div className="log-box">
                {logs.length === 0 && <p className="muted">No activity yet.</p>}
                {logs.map((log, index) => (
                  <div className="log-row" key={`${log}-${index}`}>
                    {log}
                  </div>
                ))}
              </div>
            </section>
          </section>
        )}

        {page === "stake" && (
          <section className="page-grid settings-grid">
            <section className="panel wide-panel">
              <h2>Stake / Unstake</h2>
              <div className="stake-summary">
                <Metric icon={<KeyRound />} label="Validator ID" value={identity.validatorId || "Register identity first"} />
                <Metric icon={<Wallet />} label="Stake status" value={validatorRow ? `${validatorRow.wallet_stake_locked ?? 0} PI` : "-"} />
              </div>

              <label className="form-row">
                <span>Wallet JSON</span>
                <div className="input-with-button">
                  <input value={walletPath} onChange={(event) => setWalletPath(event.target.value)} placeholder="Select wallet JSON" />
                  <button className="secondary-button" onClick={() => void chooseWallet()}>Browse</button>
                </div>
              </label>

              <label className="form-row">
                <span>Wallet password</span>
                <input
                  type="password"
                  value={walletPassword}
                  onChange={(event) => setWalletPassword(event.target.value)}
                  placeholder="Required for encrypted Picoin Wallet keystore"
                />
              </label>

              <div className="split-form">
                <label className="form-row">
                  <span>Amount</span>
                  <input value={stakeAmount} onChange={(event) => setStakeAmount(event.target.value)} />
                </label>
                <label className="form-row">
                  <span>Fee</span>
                  <input value={stakeFee} onChange={(event) => setStakeFee(event.target.value)} />
                </label>
              </div>

              <button className="primary-button" disabled={busy || !identity.validatorId} onClick={() => void submitStake()}>
                <CircleDollarSign />
                Stake Validator
              </button>
              <button className="danger-button" disabled={busy || !identity.validatorId} onClick={() => void submitUnstake()}>
                <Wallet />
                Unstake Validator
              </button>
            </section>

            <section className="panel">
              <h2>Validator Record</h2>
              <div className="metric-list">
                <Metric icon={<Activity />} label="Online" value={validatorRow?.online_status || "-"} />
                <Metric icon={<RotateCw />} label="Sync" value={validatorRow?.sync_status || "-"} />
                <Metric icon={<CheckCircle2 />} label="Eligible" value={validatorRow ? String(Boolean(validatorRow.eligible)) : "-"} />
                <Metric icon={<Database />} label="Height" value={validatorRow?.effective_height ?? "-"} />
              </div>
            </section>
          </section>
        )}

        {page === "settings" && (
          <section className="page-grid settings-grid">
            <section className="panel wide-panel">
              <h2>Settings</h2>
              <label className="form-row">
                <span>Validator name</span>
                <input value={validatorName} onChange={(event) => setValidatorName(event.target.value)} />
              </label>

              <label className="form-row">
                <span>Reward wallet</span>
                <input value={rewardWallet} onChange={(event) => setRewardWallet(event.target.value)} placeholder="PI..." />
              </label>

              <label className="form-row">
                <span>Mainnet API</span>
                <input value={apiUrl} onChange={(event) => setApiUrl(event.target.value)} />
              </label>

              <div className="split-form">
                <label className="form-row">
                  <span>network_id</span>
                  <input value={networkId} onChange={(event) => setNetworkId(event.target.value)} />
                </label>
                <label className="form-row">
                  <span>chain_id</span>
                  <input value={chainId} onChange={(event) => setChainId(event.target.value)} />
                </label>
              </div>

              <div className="split-form">
                <label className="form-row">
                  <span>Local RPC port</span>
                  <input type="number" value={nodePort} onChange={(event) => setNodePort(Number(event.target.value))} />
                </label>
                <label className="form-row">
                  <span>Validation sleep</span>
                  <input type="number" step="0.1" value={validationSleep} onChange={(event) => setValidationSleep(Number(event.target.value))} />
                </label>
              </div>

              <label className="form-row">
                <span>Advertised node address</span>
                <input value={nodeAddress} onChange={(event) => setNodeAddress(event.target.value)} placeholder={localNodeUrl} />
              </label>

              <div className="button-row">
                <button className="primary-button" disabled={busy || identity.exists} onClick={() => void registerIdentity()}>
                  <KeyRound />
                  Register Identity
                </button>
                <button className="secondary-button" disabled={busy || !identity.exists} onClick={() => void updateRewardWallet()}>
                  <Wallet />
                  Save Reward Wallet
                </button>
                <button className="secondary-button" disabled={busy} onClick={() => void refreshAll()}>
                  <RefreshCw />
                  Refresh
                </button>
              </div>
            </section>

            <section className="panel">
              <h2>Paths</h2>
              <div className="metric-list">
                <Metric icon={<KeyRound />} label="Identity" value={identity.identityPath || "-"} />
                <Metric icon={<Database />} label="Data" value={processStatus.dataDir || "-"} />
                <Metric icon={<Database />} label="DB" value={processStatus.dbPath || "-"} />
                <Metric icon={<CheckCircle2 />} label="Genesis" value={MAINNET_GENESIS_HASH.slice(0, 16)} />
              </div>
            </section>
          </section>
        )}
      </main>
    </div>
  );
}

function NavButton({ active, onClick, icon, label }: { active: boolean; onClick: () => void; icon: ReactNode; label: string }) {
  return (
    <button className={active ? "nav-item active" : "nav-item"} onClick={onClick}>
      {icon}
      <span>{label}</span>
    </button>
  );
}

function Metric({ icon, label, value }: { icon: ReactNode; label: string; value: ReactNode }) {
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

function loadSavedSettings(): ValidatorSettings {
  try {
    const raw = window.localStorage.getItem(SETTINGS_STORAGE_KEY);
    if (!raw) return DEFAULT_SETTINGS;
    const saved = JSON.parse(raw) as Partial<ValidatorSettings>;
    const merged = {
      ...DEFAULT_SETTINGS,
      ...saved,
      nodePort: Number(saved.nodePort || DEFAULT_SETTINGS.nodePort),
      validationSleep: Number(saved.validationSleep || DEFAULT_SETTINGS.validationSleep),
    };
    const usesLegacyTestnet = merged.networkId === "public-testnet" || merged.chainId === "picoin-public-testnet-v018";
    if (usesLegacyTestnet) {
      return {
        ...merged,
        networkId: MAINNET_NETWORK_ID,
        chainId: MAINNET_CHAIN_ID,
        apiUrl: CANONICAL_MAINNET_API,
      };
    }
    return merged;
  } catch {
    return DEFAULT_SETTINGS;
  }
}

function saveSettings(settings: ValidatorSettings) {
  window.localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(settings));
}
