const { contextBridge } = require("electron");

contextBridge.exposeInMainWorld("teamAgentDesktop", {
  platform: process.platform,
  isDesktop: true,
});
