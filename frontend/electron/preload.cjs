const { contextBridge } = require("electron");

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
});
