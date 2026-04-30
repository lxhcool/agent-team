const { contextBridge, ipcRenderer } = require("electron");

function markDesktopShell() {
  document.documentElement.classList.add(
    "team-agent-desktop",
    `platform-${process.platform}`
  );
}

if (document.readyState === "loading") {
  window.addEventListener("DOMContentLoaded", markDesktopShell, { once: true });
} else {
  markDesktopShell();
}

contextBridge.exposeInMainWorld("teamAgentDesktop", {
  platform: process.platform,
  isDesktop: true,
  auth: {
    get: () => ipcRenderer.invoke("desktop-auth:get"),
    set: (auth) => ipcRenderer.invoke("desktop-auth:set", auth),
    clear: () => ipcRenderer.invoke("desktop-auth:clear"),
  },
});
