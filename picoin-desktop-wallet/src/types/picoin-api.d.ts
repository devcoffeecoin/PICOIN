import type { PicoinDesktopAPI } from "../../electron/preload/preload";

declare global {
  interface Window {
    picoin: PicoinDesktopAPI;
  }
}

export {};

