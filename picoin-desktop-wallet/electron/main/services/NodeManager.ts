import fs from "node:fs";
import { spawn, type ChildProcess } from "node:child_process";
import type { NetworkConfig, NetworkId, NodeStatus } from "../../../shared/types";
import { PicoinRPC } from "./PicoinRPC";

const RPC_WAIT_TIMEOUT_MS = 30_000;
const RPC_WAIT_INTERVAL_MS = 500;

export class NodeManager {
  private child: ChildProcess | null = null;
  private status: NodeStatus;
  private rpc: PicoinRPC;
  private nodePath: string;
  private network: NetworkConfig;

  constructor(nodePath: string, network: NetworkConfig) {
    this.nodePath = nodePath;
    this.network = network;
    this.rpc = new PicoinRPC(network.rpcUrl);
    this.status = {
      status: "stopped",
      network: network.id,
      rpcUrl: network.rpcUrl,
      blockHeight: null,
      syncStatus: "stopped",
      peers: null,
    };
  }

  getRpc(): PicoinRPC {
    return this.rpc;
  }

  getStatus(): NodeStatus {
    return this.status;
  }

  async start(nodePath: string, network: NetworkConfig): Promise<NodeStatus> {
    this.nodePath = nodePath;
    this.network = network;
    this.rpc.setRpcUrl(network.rpcUrl);
    this.status = {
      status: "starting",
      network: network.id,
      rpcUrl: network.rpcUrl,
      blockHeight: null,
      syncStatus: "starting",
      peers: null,
      startedAt: new Date().toISOString(),
    };

    if (await this.rpc.isAvailable()) {
      return this.refreshStatus("running");
    }

    if (!fs.existsSync(this.nodePath)) {
      this.status = {
        ...this.status,
        status: "error",
        syncStatus: "node-binary-missing",
        message: `Node binary not found at ${this.nodePath}`,
      };
      return this.status;
    }

    const child = spawn(this.nodePath, network.nodeArgs, {
      stdio: "ignore",
      windowsHide: true,
      env: {
        ...process.env,
        PICOIN_NETWORK: network.id === "mainnet" ? "mainnet" : "public-testnet",
        PICOIN_DATA_DIR: network.dataDir,
      },
    });
    this.child = child;
    child.once("exit", (code) => {
      if (this.status.status !== "stopped") {
        this.status = {
          ...this.status,
          status: code === 0 ? "stopped" : "error",
          syncStatus: code === 0 ? "stopped" : "node-exited",
          message: code === 0 ? undefined : `Node process exited with code ${code}`,
        };
      }
      this.child = null;
    });
    child.once("error", (error) => {
      this.status = {
        ...this.status,
        status: "error",
        syncStatus: "spawn-error",
        message: error.message,
      };
      this.child = null;
    });

    const ready = await this.waitForRpc();
    if (!ready) {
      this.status = {
        ...this.status,
        status: "error",
        syncStatus: "rpc-timeout",
        message: "Node process started, but local RPC did not become available",
      };
      return this.status;
    }
    return this.refreshStatus("running");
  }

  async restart(nodePath: string, network: NetworkConfig): Promise<NodeStatus> {
    await this.stop();
    return this.start(nodePath, network);
  }

  async stop(): Promise<NodeStatus> {
    if (this.child) {
      const child = this.child;
      this.child = null;
      child.kill();
      await new Promise<void>((resolve) => {
        child.once("exit", () => resolve());
        setTimeout(resolve, 2500);
      });
    }
    this.status = {
      ...this.status,
      status: "stopped",
      syncStatus: "stopped",
      message: undefined,
      peers: null,
    };
    return this.status;
  }

  async refreshStatus(defaultStatus: NodeStatus["status"] = "running"): Promise<NodeStatus> {
    if (!(await this.rpc.isAvailable())) {
      if (this.status.status !== "error") {
        this.status = {
          ...this.status,
          status: this.child ? "starting" : "stopped",
          syncStatus: this.child ? "waiting-rpc" : "stopped",
          blockHeight: null,
          peers: null,
        };
      }
      return this.status;
    }

    const sync = await this.rpc.getSyncStatus();
    const peers = await this.rpc.getPeers();
    const latest = sync.latestBlockHeight ?? sync.blockHeight;
    const local = sync.localBlockHeight ?? sync.blockHeight;
    const synced = latest !== null && local !== null && local >= latest;
    this.status = {
      ...this.status,
      status: synced ? "synced" : defaultStatus === "starting" ? "starting" : "syncing",
      syncStatus: synced ? "synced" : sync.status,
      blockHeight: sync.blockHeight,
      peers: peers.length,
      message: undefined,
    };
    return this.status;
  }

  private async waitForRpc(): Promise<boolean> {
    const deadline = Date.now() + RPC_WAIT_TIMEOUT_MS;
    while (Date.now() < deadline) {
      if (await this.rpc.isAvailable(RPC_WAIT_INTERVAL_MS)) {
        return true;
      }
      await new Promise((resolve) => setTimeout(resolve, RPC_WAIT_INTERVAL_MS));
    }
    return false;
  }
}
