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

function log(message) {
  console.log(`[desktop] ${message}`);
}

function createWindow() {
  const useMacTitlebar = process.platform === "darwin";

  mainWindow = new BrowserWindow({
    width: 1280,
    height: 840,
    minWidth: 1080,
    minHeight: 720,
    title: "Team Agent",
    backgroundColor: "#0f172a",
    show: true,
    icon: appIconPath,
    titleBarStyle: useMacTitlebar ? "hiddenInset" : "hidden",
    trafficLightPosition: useMacTitlebar ? { x: 16, y: 17 } : undefined,
    titleBarOverlay: useMacTitlebar ? false : {
      color: "#00000000",
      symbolColor: "#94a3b8",
      height: 56,
    },
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  mainWindow.loadURL(
    "data:text/html;charset=utf-8," +
      encodeURIComponent(`
        <!doctype html>
        <html>
          <head>
            <meta charset="utf-8" />
            <style>
              html, body {
                margin: 0;
                height: 100%;
                background: #0f172a;
                color: #e2e8f0;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
              }
              body {
                display: grid;
                place-items: center;
              }
              .box {
                display: flex;
                align-items: center;
                gap: 12px;
                font-size: 14px;
                color: #94a3b8;
              }
              .dot {
                width: 8px;
                height: 8px;
                border-radius: 999px;
                background: #818cf8;
                animation: pulse 1s ease-in-out infinite;
              }
              @keyframes pulse {
                0%, 100% { opacity: .35; transform: scale(.85); }
                50% { opacity: 1; transform: scale(1); }
              }
            </style>
          </head>
          <body>
            <div class="box"><span class="dot"></span><span>Starting Team Agent...</span></div>
          </body>
        </html>
      `)
  );

  mainWindow.webContents.on("did-fail-load", (_event, code, description, url) => {
    log(`window failed to load ${url}: ${code} ${description}`);
  });
  mainWindow.webContents.on("console-message", (_event, level, message, line, sourceId) => {
    log(`renderer console[${level}] ${sourceId}:${line} ${message}`);
  });
  mainWindow.webContents.on("did-navigate", (_event, url) => {
    log(`window navigated: ${url}`);
  });
  mainWindow.webContents.on("did-navigate-in-page", (_event, url) => {
    log(`window in-page navigation: ${url}`);
  });
  mainWindow.webContents.on("did-start-loading", () => {
    log("window started loading");
  });
  mainWindow.webContents.on("did-finish-load", () => {
    log(`window finished loading: ${mainWindow.webContents.getURL()}`);
  });
  mainWindow.webContents.on("dom-ready", () => {
    log(`window dom ready: ${mainWindow.webContents.getURL()}`);
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });

  if (!app.isPackaged && process.env.ELECTRON_ENABLE_LOGGING) {
    mainWindow.webContents.openDevTools({ mode: "detach" });
  }

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

function waitForHttp(url, timeoutMs = 60000, label = url) {
  const started = Date.now();
  let lastLogAt = 0;
  let lastState = "not checked yet";

  return new Promise((resolve, reject) => {
    const poll = () => {
      const req = http.get(url, (res) => {
        res.resume();
        if (res.statusCode && res.statusCode >= 200 && res.statusCode < 400) {
          resolve();
          return;
        }
        lastState = `HTTP ${res.statusCode || "unknown"}`;
        retry();
      });

      req.on("error", (error) => {
        lastState = error.message;
        retry();
      });
      req.setTimeout(3000, () => {
        lastState = "request timed out";
        req.destroy();
        retry();
      });
    };

    const retry = () => {
      if (Date.now() - started > timeoutMs) {
        reject(new Error(`Timed out waiting for ${label}: ${lastState}`));
        return;
      }
      if (Date.now() - lastLogAt > 3000) {
        lastLogAt = Date.now();
        log(`still waiting for ${label}: ${lastState}`);
      }
      setTimeout(poll, 500);
    };

    poll();
  });
}

function spawnManaged(command, args, options) {
  log(`spawn: ${command} ${args.join(" ")}`);
  const child = spawn(command, args, {
    stdio: "inherit",
    windowsHide: true,
    ...options,
  });

  childProcesses.add(child);
  child.once("exit", () => childProcesses.delete(child));
  return child;
}

function waitForManagedHttp(url, child, label, timeoutMs = 60000) {
  return Promise.race([
    waitForHttp(url, timeoutMs, label),
    new Promise((_, reject) => {
      child.once("exit", (code, signal) => {
        reject(new Error(`${label} process exited before it was ready (code=${code}, signal=${signal || "none"})`));
      });
      child.once("error", (error) => {
        reject(new Error(`${label} failed to start: ${error.message}`));
      });
    }),
  ]);
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
  log(`ports: backend=${resolvedBackendPort}, frontend=${resolvedFrontendPort}`);

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

  const backendProcess = spawnManaged(
    pythonCommand,
    ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(resolvedBackendPort)],
    {
      cwd: backendDir,
      env: backendEnv,
    }
  );

  log(`waiting for backend: http://127.0.0.1:${resolvedBackendPort}/api/health`);
  await waitForManagedHttp(
    `http://127.0.0.1:${resolvedBackendPort}/api/health`,
    backendProcess,
    "backend"
  );
  log("backend is ready");

  const npmCommand = process.platform === "win32" ? "npm.cmd" : "npm";
  const frontendProcess = spawnManaged(
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

  const frontendUrl = `http://127.0.0.1:${resolvedFrontendPort}`;
  const initialPageUrl = `${frontendUrl}/login`;
  log(`waiting for frontend: ${initialPageUrl}`);
  await waitForManagedHttp(initialPageUrl, frontendProcess, "frontend", 90000);
  log("frontend is ready");

  return {
    backendUrl: `http://127.0.0.1:${resolvedBackendPort}`,
    frontendUrl: initialPageUrl,
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

    log(`loading frontend: ${urls.frontendUrl}`);
    await window.loadURL(urls.frontendUrl);
    setTimeout(async () => {
      if (window.isDestroyed()) return;
      try {
        const snapshot = await window.webContents.executeJavaScript(`
          ({
            url: window.location.href,
            title: document.title,
            bodyText: document.body ? document.body.innerText.slice(0, 300) : "",
            bodyChildCount: document.body ? document.body.children.length : 0,
            rootHtml: document.getElementById("__next")?.innerHTML.slice(0, 300) || ""
          })
        `);
        log(`renderer snapshot: ${JSON.stringify(snapshot)}`);
      } catch (probeError) {
        log(`renderer probe failed: ${probeError instanceof Error ? probeError.message : String(probeError)}`);
      }
    }, 3000);
  } catch (error) {
    log(`startup failed: ${error instanceof Error ? error.stack || error.message : String(error)}`);
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
