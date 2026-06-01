declare module "*.css";
declare module "*.png";
declare module "*.jpg";
declare module "*.jpeg";
declare module "*.svg";

interface Window {
  validatorApi: {
    request: (method: string, url: string, body?: unknown) => Promise<any>;
    startNode: (config: unknown) => Promise<any>;
    stopNode: () => Promise<any>;
    register: (config: unknown) => Promise<any>;
    start: (config: unknown) => Promise<any>;
    stop: () => Promise<any>;
    status: () => Promise<any>;
    identity: () => Promise<any>;
    restoreSnapshot: (config: unknown) => Promise<any>;
    catchUp: (config: unknown) => Promise<any>;
    chooseWallet: () => Promise<any>;
    stake: (config: unknown) => Promise<any>;
    unstake: (config: unknown) => Promise<any>;
    updateRewardWallet: (config: unknown) => Promise<any>;
  };
}
