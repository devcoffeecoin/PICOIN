import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent } from "react";
import {
  Copy,
  Gauge,
  Home,
  Lock,
  Network,
  Power,
  QrCode,
  RefreshCw,
  Send,
  Settings,
  Shield,
  Wallet,
} from "lucide-react";
import { QRCodeCanvas } from "qrcode.react";
import picoinLogo from "./assets/picoin-logo.png";
import type {
  AccountBalance,
  ApiStatus,
  AppSettings,
  NetworkConfig,
  NetworkId,
  SendTransactionResult,
  TransactionRecord,
  WalletSummary,
} from "../shared/types";

type Page = "dashboard" | "send" | "receive" | "wallet" | "settings";

const emptyApiStatus: ApiStatus = {
  status: "offline",
  network: "mainnet",
  apiUrl: "https://api.picoin.science",
  blockHeight: null,
  syncStatus: "unavailable",
};

const emptyWallet: WalletSummary = {
  hasWallet: false,
  locked: true,
  address: null,
  publicKey: null,
};

export default function App() {
  const [page, setPage] = useState<Page>("dashboard");
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [networks, setNetworks] = useState<Record<NetworkId, NetworkConfig> | null>(null);
  const [apiStatus, setApiStatus] = useState<ApiStatus>(emptyApiStatus);
  const [wallet, setWallet] = useState<WalletSummary>(emptyWallet);
  const [balance, setBalance] = useState<AccountBalance | null>(null);
  const [history, setHistory] = useState<TransactionRecord[]>([]);
  const [notice, setNotice] = useState<string>("");
  const [noticeTxHash, setNoticeTxHash] = useState<string | null>(null);
  const refreshInFlight = useRef<Promise<void> | null>(null);
  const activeNetwork = settings?.selectedNetwork || "mainnet";
  const showNotice = useCallback((message: string, txHash?: string | null) => {
    setNotice(message);
    setNoticeTxHash(txHash || null);
  }, []);

  const refreshAll = useCallback(() => {
    if (refreshInFlight.current) {
      return refreshInFlight.current;
    }
    const refresh = (async () => {
      try {
        const [nextSettings, nextNetworks, nextApiStatus, nextWallet] = await Promise.all([
          window.picoin.settings.get(),
          window.picoin.settings.networks(),
          window.picoin.api.status(),
          window.picoin.wallet.summary(),
        ]);
        setSettings(nextSettings);
        setNetworks(nextNetworks);
        setApiStatus(nextApiStatus);
        setWallet(nextWallet);
        if (nextWallet.address) {
          try {
            const [nextBalance, nextHistory] = await Promise.all([
              window.picoin.api.getBalance(nextWallet.address),
              window.picoin.api.getHistory(nextWallet.address),
            ]);
            setBalance(nextBalance);
            setHistory(nextHistory);
          } catch (error) {
            showNotice(
              isTransientUiError(error)
                ? "Wallet data refresh timed out. Showing the last loaded balance."
                : errorMessage(error),
            );
          }
        } else {
          setBalance(null);
          setHistory([]);
        }
      } catch (error) {
        showNotice(errorMessage(error));
      }
    })();
    refreshInFlight.current = refresh.finally(() => {
      refreshInFlight.current = null;
    });
    return refreshInFlight.current;
  }, [showNotice]);

  useEffect(() => {
    void refreshAll();
    const timer = window.setInterval(() => void refreshAll(), 30_000);
    return () => window.clearInterval(timer);
  }, [refreshAll]);

  const pageTitle = useMemo(() => {
    return {
      dashboard: "Dashboard",
      send: "Send",
      receive: "Receive",
      wallet: "Wallet",
      settings: "Settings",
    }[page];
  }, [page]);

  return (
    <div className="app-shell">
      <Sidebar page={page} onChange={setPage} />
      <main className="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">Picoin Desktop Wallet V1</p>
            <h1>{pageTitle}</h1>
          </div>
          <div className="topbar-actions">
            <StatusPill status={apiStatus.status} />
            <button className="icon-button" onClick={() => void refreshAll()} title="Refresh">
              <RefreshCw size={18} />
            </button>
          </div>
        </header>

        {notice && (
          <div className="notice">
            <span>
              {notice}
              {noticeTxHash && (
                <>
                  {" "}
                  <a href={transactionExplorerUrl(noticeTxHash)} target="_blank" rel="noreferrer">
                    {shortHash(noticeTxHash)}
                  </a>
                </>
              )}
            </span>
            <button onClick={() => {
              setNotice("");
              setNoticeTxHash(null);
            }}>Dismiss</button>
          </div>
        )}

        {page === "dashboard" && (
          <Dashboard
            wallet={wallet}
            balance={balance}
            history={history}
            apiStatus={apiStatus}
            activeNetwork={activeNetwork}
          />
        )}
        {page === "send" && <SendPage wallet={wallet} onSent={(message, txHash) => {
          showNotice(message, txHash);
          void refreshAll();
        }} />}
        {page === "receive" && <ReceivePage wallet={wallet} />}
        {page === "wallet" && <WalletPage wallet={wallet} onWalletChange={(message) => {
          showNotice(message);
          void refreshAll();
        }} />}
        {page === "settings" && settings && networks && (
          <SettingsPage
            settings={settings}
            networks={networks}
            apiStatus={apiStatus}
            onChanged={(message) => {
              showNotice(message);
              void refreshAll();
            }}
          />
        )}
      </main>
    </div>
  );
}

function Sidebar({ page, onChange }: { page: Page; onChange: (page: Page) => void }) {
  const items: Array<{ page: Page; label: string; icon: JSX.Element }> = [
    { page: "dashboard", label: "Dashboard", icon: <Home size={20} /> },
    { page: "send", label: "Send", icon: <Send size={20} /> },
    { page: "receive", label: "Receive", icon: <QrCode size={20} /> },
    { page: "wallet", label: "Wallet", icon: <Wallet size={20} /> },
    { page: "settings", label: "Settings", icon: <Settings size={20} /> },
  ];
  return (
    <aside className="sidebar">
      <div className="brand">
        <img className="brand-mark" src={picoinLogo} alt="Picoin" />
        <div>
          <strong>Picoin</strong>
          <span>Science Wallet</span>
        </div>
      </div>
      <nav>
        {items.map((item) => (
          <button
            key={item.page}
            className={page === item.page ? "nav-item active" : "nav-item"}
            onClick={() => onChange(item.page)}
          >
            {item.icon}
            <span>{item.label}</span>
          </button>
        ))}
      </nav>
      <div className="sidebar-footer">
        <Shield size={18} />
        <span>Local keys, encrypted keystore</span>
      </div>
    </aside>
  );
}

function Dashboard({
  wallet,
  balance,
  history,
  apiStatus,
  activeNetwork,
}: {
  wallet: WalletSummary;
  balance: AccountBalance | null;
  history: TransactionRecord[];
  apiStatus: ApiStatus;
  activeNetwork: NetworkId;
}) {
  return (
    <section className="page-grid dashboard-grid">
      <Panel className="hero-panel">
        <div>
          <p className="eyebrow">Balance</p>
          <div className="balance">{balance ? balance.balance.toFixed(6) : "0.000000"} PI</div>
          <p className="muted">{wallet.address ? shortAddress(wallet.address) : "No wallet loaded"}</p>
        </div>
        <img className="hero-logo" src={picoinLogo} alt="Picoin Proof of Pi" />
      </Panel>

      <Panel>
        <h2>API Connection</h2>
        <div className="metric-list">
          <Metric icon={<Gauge />} label="Block height" value={apiStatus.blockHeight ?? "n/a"} />
          <Metric icon={<Network />} label="Sync" value={apiStatus.syncStatus} />
          <Metric icon={<Power />} label="Status" value={apiStatus.status} />
        </div>
      </Panel>

      <Panel>
        <h2>Network</h2>
        <div className="large-value">{activeNetwork}</div>
        <p className="muted">API {apiStatus.apiUrl}</p>
      </Panel>

      <Panel className="wide-panel">
        <h2>Recent Activity</h2>
        <div className="table">
          {history.length === 0 && <p className="muted">No transactions found.</p>}
          {history.slice(0, 8).map((tx, index) => (
            <div className="table-row" key={String(tx.tx_hash || index)}>
              <span>{tx.tx_type || "tx"}</span>
              <span>{tx.amount ?? ""} PI</span>
              <span>{tx.status || "unknown"}</span>
              <span>
                {tx.tx_hash ? (
                  <a href={transactionExplorerUrl(tx.tx_hash)} target="_blank" rel="noreferrer">
                    {shortHash(tx.tx_hash)}
                  </a>
                ) : ""}
              </span>
            </div>
          ))}
        </div>
      </Panel>
    </section>
  );
}

function SendPage({ wallet, onSent }: { wallet: WalletSummary; onSent: (message: string, txHash?: string | null) => void }) {
  const [to, setTo] = useState("");
  const [amount, setAmount] = useState("0.01");
  const [fee, setFee] = useState("0.001");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!wallet.address || wallet.locked) {
      onSent("Unlock a wallet before sending.");
      return;
    }
    const parsedAmount = Number(amount);
    const parsedFee = Number(fee);
    if (!to.trim() || !Number.isFinite(parsedAmount) || parsedAmount <= 0 || !Number.isFinite(parsedFee) || parsedFee < 0) {
      onSent("Check destination, amount and fee.");
      return;
    }
    const confirmed = window.confirm(`Send ${parsedAmount} PI to ${to}? Fee: ${parsedFee} PI.`);
    if (!confirmed) {
      return;
    }
    setBusy(true);
    try {
      const result = (await window.picoin.wallet.send({
        to: to.trim(),
        amount: parsedAmount,
        fee: parsedFee,
      })) as SendTransactionResult;
      const status = result.status ? ` (${result.status})` : "";
      const verified = result.verified ? " and verified" : "";
      onSent(`Transaction submitted${verified}${status}.`, result.txHash);
      setTo("");
    } catch (error) {
      onSent(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="single-column">
      <Panel>
        <h2>Send PI</h2>
        <FormRow label="Destination address">
          <input value={to} onChange={(event) => setTo(event.target.value)} placeholder="PI..." />
        </FormRow>
        <div className="form-grid">
          <FormRow label="Amount">
            <input value={amount} onChange={(event) => setAmount(event.target.value)} inputMode="decimal" />
          </FormRow>
          <FormRow label="Fee">
            <input value={fee} onChange={(event) => setFee(event.target.value)} inputMode="decimal" />
          </FormRow>
        </div>
        <button className="primary-button" disabled={busy || wallet.locked || !wallet.hasWallet} onClick={() => void submit()}>
          <Send size={18} />
          {busy ? "Sending..." : "Send"}
        </button>
      </Panel>
    </section>
  );
}

function ReceivePage({ wallet }: { wallet: WalletSummary }) {
  const copyAddress = async () => {
    if (wallet.address) {
      await navigator.clipboard.writeText(wallet.address);
    }
  };
  return (
    <section className="single-column">
      <Panel className="receive-panel">
        <h2>Receive PI</h2>
        {wallet.address ? (
          <>
            <div className="qr-wrap">
              <QRCodeCanvas value={wallet.address} size={188} bgColor="#ffffff" fgColor="#111827" />
            </div>
            <div className="address-box">{wallet.address}</div>
            <button className="secondary-button" onClick={() => void copyAddress()}>
              <Copy size={18} />
              Copy address
            </button>
          </>
        ) : (
          <p className="muted">Create or import a wallet first.</p>
        )}
      </Panel>
    </section>
  );
}

function WalletPage({ wallet, onWalletChange }: { wallet: WalletSummary; onWalletChange: (message: string) => void }) {
  const [password, setPassword] = useState("");
  const [seedPhrase, setSeedPhrase] = useState("");
  const [privateKey, setPrivateKey] = useState("");
  const [walletJson, setWalletJson] = useState("");
  const [createdSeed, setCreatedSeed] = useState("");

  const withPassword = async (operation: () => Promise<unknown>, success: string) => {
    try {
      if (password.length < 8) {
        onWalletChange("Password must be at least 8 characters.");
        return;
      }
      const result = await operation();
      if (typeof result === "object" && result && "seedPhrase" in result) {
        setCreatedSeed(String((result as { seedPhrase: string }).seedPhrase));
      }
      onWalletChange(success);
    } catch (error) {
      onWalletChange(errorMessage(error));
    }
  };

  const exportKeystore = async () => {
    try {
      const exportData = await window.picoin.wallet.exportKeystore(password);
      const blob = new Blob([exportData.keystore], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = exportData.fileName;
      anchor.click();
      URL.revokeObjectURL(url);
      onWalletChange("Encrypted keystore exported.");
    } catch (error) {
      onWalletChange(errorMessage(error));
    }
  };

  const importJsonFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }
    try {
      setWalletJson(await file.text());
      onWalletChange("Wallet JSON loaded. Enter the password and import it.");
    } catch (error) {
      onWalletChange(errorMessage(error));
    } finally {
      event.target.value = "";
    }
  };

  return (
    <section className="page-grid wallet-grid">
      <Panel>
        <h2>Wallet Status</h2>
        <div className="metric-list">
          <Metric icon={<Wallet />} label="Address" value={wallet.address ? shortAddress(wallet.address) : "none"} />
          <Metric icon={<Lock />} label="Lock" value={wallet.locked ? "locked" : "unlocked"} />
        </div>
        <FormRow label="Password">
          <input type="password" value={password} onChange={(event) => setPassword(event.target.value)} />
        </FormRow>
        <div className="button-row">
          <button className="secondary-button" onClick={() => void withPassword(() => window.picoin.wallet.unlock(password), "Wallet unlocked.")}>
            Unlock
          </button>
          <button className="secondary-button" onClick={() => void window.picoin.wallet.lock().then(() => onWalletChange("Wallet locked."))}>
            Lock
          </button>
        </div>
      </Panel>

      <Panel>
        <h2>Create</h2>
        <button className="primary-button" onClick={() => void withPassword(() => window.picoin.wallet.create(password), "Wallet created.")}>
          Create wallet
        </button>
        {createdSeed && (
          <div className="seed-box">
            <strong>Seed phrase</strong>
            <p>{createdSeed}</p>
          </div>
        )}
      </Panel>

      <Panel>
        <h2>Import Seed</h2>
        <textarea value={seedPhrase} onChange={(event) => setSeedPhrase(event.target.value)} placeholder="BIP39 seed phrase" />
        <button className="secondary-button" onClick={() => void withPassword(() => window.picoin.wallet.importSeed(seedPhrase, password), "Wallet imported from seed.")}>
          Import seed
        </button>
      </Panel>

      <Panel>
        <h2>Import Key</h2>
        <textarea value={privateKey} onChange={(event) => setPrivateKey(event.target.value)} placeholder="ed25519:..." />
        <button className="secondary-button" onClick={() => void withPassword(() => window.picoin.wallet.importPrivateKey(privateKey, password), "Wallet imported from private key.")}>
          Import private key
        </button>
      </Panel>

      <Panel className="wide-panel">
        <h2>Import JSON</h2>
        <input type="file" accept="application/json,.json" onChange={(event) => void importJsonFile(event)} />
        <textarea
          value={walletJson}
          onChange={(event) => setWalletJson(event.target.value)}
          placeholder="Paste web wallet JSON or encrypted keystore JSON"
        />
        <button
          className="secondary-button"
          onClick={() => void withPassword(() => window.picoin.wallet.importJson(walletJson, password), "Wallet imported from JSON.")}
        >
          Import JSON
        </button>
      </Panel>

      <Panel className="wide-panel">
        <h2>Export</h2>
        <button className="secondary-button" disabled={!wallet.hasWallet} onClick={() => void exportKeystore()}>
          Export encrypted keystore
        </button>
      </Panel>
    </section>
  );
}

function SettingsPage({
  settings,
  networks,
  apiStatus,
  onChanged,
}: {
  settings: AppSettings;
  networks: Record<NetworkId, NetworkConfig>;
  apiStatus: ApiStatus;
  onChanged: (message: string) => void;
}) {
  const [draftTestnetApi, setDraftTestnetApi] = useState(settings.apiUrls.testnet);
  const [draftMainnetApi, setDraftMainnetApi] = useState(settings.apiUrls.mainnet);

  useEffect(() => {
    setDraftTestnetApi(settings.apiUrls.testnet);
    setDraftMainnetApi(settings.apiUrls.mainnet);
  }, [settings]);

  const updateNetwork = async (network: NetworkId) => {
    try {
      await window.picoin.settings.setNetwork(network);
      onChanged(`Network changed to ${network}.`);
    } catch (error) {
      onChanged(errorMessage(error));
    }
  };

  const saveApiUrls = async () => {
    try {
      await window.picoin.settings.update({
        apiUrls: {
          testnet: draftTestnetApi,
          mainnet: draftMainnetApi,
        },
      });
      onChanged("API settings saved.");
    } catch (error) {
      onChanged(errorMessage(error));
    }
  };

  return (
    <section className="page-grid settings-grid">
      <Panel>
        <h2>Network</h2>
        <div className="segmented">
          <button className={settings.selectedNetwork === "testnet" ? "selected" : ""} onClick={() => void updateNetwork("testnet")}>
            Testnet
          </button>
          <button className={settings.selectedNetwork === "mainnet" ? "selected" : ""} onClick={() => void updateNetwork("mainnet")}>
            Mainnet
          </button>
        </div>
        <div className="network-detail">
          <strong>{networks[settings.selectedNetwork].chainName}</strong>
          <span>{networks[settings.selectedNetwork].apiUrl}</span>
          <span>network: {networks[settings.selectedNetwork].network}</span>
          <span>chain_id: {String(networks[settings.selectedNetwork].chainId)}</span>
        </div>
      </Panel>

      <Panel>
        <h2>API Status</h2>
        <div className="metric-list">
          <Metric icon={<Power />} label="Status" value={apiStatus.status} />
          <Metric icon={<Network />} label="API URL" value={apiStatus.apiUrl} />
          <Metric icon={<Gauge />} label="Height" value={apiStatus.blockHeight ?? "n/a"} />
        </div>
      </Panel>

      <Panel className="wide-panel">
        <h2>API Connections</h2>
        <FormRow label="Testnet API URL">
          <input value={draftTestnetApi} onChange={(event) => setDraftTestnetApi(event.target.value)} />
        </FormRow>
        <FormRow label="Mainnet API URL">
          <input value={draftMainnetApi} onChange={(event) => setDraftMainnetApi(event.target.value)} />
        </FormRow>
        <button className="primary-button" onClick={() => void saveApiUrls()}>
          Save settings
        </button>
      </Panel>
    </section>
  );
}

function Panel({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <section className={`panel ${className}`}>{children}</section>;
}

function FormRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="form-row">
      <span>{label}</span>
      {children}
    </label>
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

function StatusPill({ status }: { status: string }) {
  return <span className={`status-pill ${status}`}>{status}</span>;
}

function shortAddress(address: string): string {
  return `${address.slice(0, 10)}...${address.slice(-8)}`;
}

function shortHash(hash: string): string {
  return `${hash.slice(0, 10)}...${hash.slice(-6)}`;
}

function transactionExplorerUrl(hash: string): string {
  return `https://www.picoin.science/transaction.html?hash=${encodeURIComponent(hash)}`;
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return String(error || "Unknown error");
}

function isTransientUiError(error: unknown): boolean {
  if (!(error instanceof Error)) {
    return false;
  }
  const message = error.message.toLowerCase();
  return error.name === "AbortError" || message.includes("abort") || message.includes("timeout") || message.includes("timed out");
}
