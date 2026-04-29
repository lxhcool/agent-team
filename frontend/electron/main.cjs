const { app, BrowserWindow, dialog, shell } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const http = require("http");
const net = require("net");
const path = require("path");

const isPackaged = app.isPackaged;
const frontendDir = path.resolve(__dirname, "..");
const repoRoot = path.resolve(frontendDir, "..");
const backendDir = isPackaged
  ? path.join(process.resourcesPath, "backend")
  : path.join(repoRoot, "backend");
const appIconPath = path.join(frontendDir, "electron", "assets", "icon.png");

const childProcesses = new Set();
let mainWindow = null;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 840,
    minWidth: 1080,
    minHeight: 720,
    title: "Team Agent",
    backgroundColor: "#0f172a",
    show: false,
    icon: appIconPath,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  mainWindow.once("ready-to-show", () => {
    mainWindow.show();
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });

  return mainWindow;
}

function findFreePort(startPort) {
  return new Promise((resolve, reject) => {
    const tryPort = (port) => {
      const server = net.createServer();
      server.unref();
      server.on("error", () => tryPort(port + 1));
      server.listen({ port, host: "127.0.0.1" }, () => {
        const { port: freePort } = server.address();
        server.close(() => resolve(freePort));
      });
    };

    if (!Number.isInteger(startPort) || startPort <= 0) {
      reject(new Error(`Invalid start port: ${startPort}`));
      return;
    }
    tryPort(startPort);
  });
}

function waitForHttp(url, timeoutMs = 60000) {
  const started = Date.now();

  return new Promise((resolve, reject) => {
    const poll = () => {
      const req = http.get(url, (res) => {
        res.resume();
        if (res.statusCode && res.statusCode >= 200 && res.statusCode < 500) {
          resolve();
          return;
        }
        retry();
      });

      req.on("error", retry);
      req.setTimeout(3000, () => {
        req.destroy();
        retry();
      });
    };

    const retry = () => {
      if (Date.now() - started > timeoutMs) {
        reject(new Error(`Timed out waiting for ${url}`));
        return;
      }
      setTimeout(poll, 500);
    };

    poll();
  });
}

function spawnManaged(command, args, options) {
  const child = spawn(command, args, {
    stdio: "inherit",
    windowsHide: true,
    ...options,
  });

  childProcesses.add(child);
  child.once("exit", () => childProcesses.delete(child));
  return child;
}

function getPythonCommand() {
  const venvPython = process.platform === "win32"
    ? path.join(backendDir, ".venv", "Scripts", "python.exe")
    : path.join(backendDir, ".venv", "bin", "python");

  if (fs.existsSync(venvPython)) {
    return venvPython;
  }

  return process.platform === "win32" ? "python" : "python3";
}

async function startLocalServices() {
  const backendPort = Number(process.env.TEAM_AGENT_BACKEND_PORT || 8200);
  const frontendPort = Number(process.env.TEAM_AGENT_FRONTEND_PORT || 3200);
  const resolvedBackendPort = await findFreePort(backendPort);
  const resolvedFrontendPort = await findFreePort(frontendPort);

  const pythonCommand = getPythonCommand();
  const backendEnv = {
    ...process.env,
    HOST: "127.0.0.1",
    PORT: String(resolvedBackendPort),
    CORS_ORIGINS: JSON.stringify([
      `http://localhost:${resolvedFrontendPort}`,
      `http://127.0.0.1:${resolvedFrontendPort}`,
    ]),
  };

  spawnManaged(
    pythonCommand,
    ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(resolvedBackendPort)],
    {
      cwd: backendDir,
      env: backendEnv,
    }
  );

  await waitForHttp(`http://127.0.0.1:${resolvedBackendPort}/api/health`);

  const npmCommand = process.platform === "win32" ? "npm.cmd" : "npm";
  spawnManaged(
    npmCommand,
    ["run", "dev", "--", "--hostname", "127.0.0.1", "--port", String(resolvedFrontendPort)],
    {
      cwd: frontendDir,
      env: {
        ...process.env,
        NEXT_PUBLIC_BACKEND_PORT: String(resolvedBackendPort),
        NEXT_PUBLIC_API_URL: `http://127.0.0.1:${resolvedBackendPort}`,
      },
    }
  );

  await waitForHttp(`http://127.0.0.1:${resolvedFrontendPort}`);

  return {
    backendUrl: `http://127.0.0.1:${resolvedBackendPort}`,
    frontendUrl: `http://127.0.0.1:${resolvedFrontendPort}`,
  };
}

function stopLocalServices() {
  for (const child of childProcesses) {
    if (!child.killed) {
      child.kill();
    }
  }
  childProcesses.clear();
}

async function bootstrap() {
  const window = createWindow();

  try {
    const externalUrl = process.env.TEAM_AGENT_WEB_URL;
    const urls = externalUrl
      ? { frontendUrl: externalUrl }
      : await startLocalServices();

    await window.loadURL(urls.frontendUrl);
  } catch (error) {
    dialog.showErrorBox(
      "Team Agent failed to start",
      error instanceof Error ? error.message : String(error)
    );
    app.quit();
  }
}

app.whenReady().then(() => {
  if (process.platform === "darwin" && app.dock && fs.existsSync(appIconPath)) {
    app.dock.setIcon(appIconPath);
  }
  return bootstrap();
});

app.on("window-all-closed", () => {
  stopLocalServices();
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("before-quit", stopLocalServices);

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    bootstrap();
  }
});
