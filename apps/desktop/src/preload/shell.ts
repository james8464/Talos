import { contextBridge, ipcRenderer } from "electron";

import {
  SHELL_IPC,
  type TalosShellApi,
  type TitlebarState,
} from "../shared/shell.js";

// Sandboxed preload for the local title bar only.
const api: TalosShellApi = {
  getState: () => ipcRenderer.invoke(SHELL_IPC.state),
  onState: (callback) => {
    const listener = (_event: unknown, state: TitlebarState): void =>
      callback(state);
    ipcRenderer.on(SHELL_IPC.stateEvent, listener);
    return () => ipcRenderer.off(SHELL_IPC.stateEvent, listener);
  },
  command: (name) => ipcRenderer.send(SHELL_IPC.command, name),
};

contextBridge.exposeInMainWorld("talosShell", api);
