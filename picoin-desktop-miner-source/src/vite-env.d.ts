/// <reference types="vite/client" />

interface Window {
  minerApi: {
    request: (method: string, url: string, body?: unknown) => Promise<unknown>;
    start: (config: unknown) => Promise<any>;
    stop: () => Promise<any>;
    status: () => Promise<any>;
    register: (config: unknown) => Promise<any>;
    identity: () => Promise<any>;
  };
}
