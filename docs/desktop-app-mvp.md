# Desktop App MVP

This branch starts the migration from "Web + CLI" to a macOS/Windows desktop app.

## Current Scope

The first desktop milestone is a development shell:

1. Electron starts a local FastAPI backend.
2. Electron starts the existing Next.js frontend.
3. Electron loads the frontend in a desktop window.
4. The existing web UI, auth, sessions, SSE stream, settings, artifacts, and CLI-oriented execution flow remain intact.

This keeps the product usable while moving the primary entrypoint away from manual CLI usage.

## Run Locally

From the frontend directory:

```bash
npm install
npm run desktop:dev
```

The Electron main process will:

- choose local backend and frontend ports,
- start `uvicorn app.main:app` from `backend`,
- start `next dev` from `frontend`,
- point the frontend at the local backend,
- stop child processes when the desktop app exits.

## Environment Overrides

```bash
TEAM_AGENT_BACKEND_PORT=8200 TEAM_AGENT_FRONTEND_PORT=3200 npm run desktop:dev
```

To load an already-running web UI instead of launching local services:

```bash
TEAM_AGENT_WEB_URL=http://127.0.0.1:3200 npm run desktop:dev
```

## Next Work

The production packaging path still needs a dedicated pass:

- package Python backend/executor for macOS and Windows,
- decide whether Next.js runs as a bundled server or is replaced by a packaged desktop renderer,
- add process-tree cleanup for Windows child processes,
- add `electron-builder` signing/notarization configuration,
- add an in-app "local execution" button so users never see CLI commands.
