# Workspace Development Progress

Last updated: 2026-04-30

## Product Direction

Team Agent is moving toward an AI delivery manager for small teams. The product helps users turn a new idea or iteration request into staged, reviewable, ready-to-start delivery artifacts.

The core product unit is a workspace. A workspace represents a product project, not just a code repository.

## Current Flow

1. Create workspace
2. Confirm requirements
3. Confirm product plan
4. Confirm UI direction
5. Confirm prototype
6. Confirm implementation readiness
7. Confirm acceptance criteria
8. Review delivery overview

Each stage must support:

- AI/team recommendation
- User feedback
- Confirm approval
- Revision request
- Versioned artifacts over time

## Completed

- Electron desktop dev shell for macOS/Windows direction.
- Desktop startup stabilization with local backend and Next frontend.
- Desktop startup timeout hardening:
  - wait for the frontend TCP port, then prewarm `/login` before Electron loads it
  - allow slower first-page compilation in Next dev mode
  - clear stale `.next` dev build cache on Electron dev startup to avoid broken vendor/static chunks
  - clean up backend/frontend child process groups when Electron exits
- macOS frameless titlebar offset for native window controls.
- Desktop login persistence:
  - Electron stores the current token and user in the app user data directory
  - desktop login survives frontend dev port changes and app restarts
  - password input supports show/hide toggle
  - new JWTs expire after 30 days
- Workspace data model:
  - `workspaces`
  - `workspace_members`
  - `workspace_stages`
- Workspace API:
  - create/list/get/update/delete workspace
  - list/update/approve/revise stages
- Workspace UI:
  - `/workspaces`
  - `/workspaces/[id]`
  - top navigation entry
- Unified homepage input:
  - homepage input now only creates a workspace for a new requirement
  - planning work is handled inside workspace stages instead of a separate user-facing planning session
  - stage revision text is entered directly inside the workspace stage
- Stage recommendation generation:
  - LLM-first generation using user model settings
  - rule-based fallback when model is unavailable or returns invalid JSON
  - source display in UI

## In Progress

- UI visual prototype loop:
  - generate real HTML prototype: done
  - save prototype as workspace artifact: done
  - show preview in the prototype stage: done
  - generate desktop/mobile design drafts: done
  - generate design artifacts without extra browser dependency: done
  - generate browser-rendered screenshots from HTML prototype: later
  - run multimodal UI review: next

## Next

- Improve design draft generation:
  - generate richer page-specific design drafts with LLM-produced layout JSON
  - support multiple pages per workspace
  - allow user to choose a design direction and regenerate
- Add multimodal UI review:
  - send generated design drafts or rendered previews to vision-capable model
  - write review summary back to prototype stage
- Add richer staged artifacts:
  - dynamic artifact structures per project type
  - stage-specific review checklist
  - revision comparison and artifact diff summary
- Strengthen iteration intake:
  - ingest existing docs and notes
  - detect scope changes and open questions
  - summarize impact across stages

## Engineering Notes

- Every workspace-owned record must be isolated by `workspace_id`.
- Every user-owned access path must be authorized through `workspace_members`.
- Planning sessions are legacy/advanced records; the normal user flow should treat workspace stages as the product planning surface.
- Prototype confirmation must favor real rendered HTML/CSS over decorative generated images.
- Concept images are useful for style exploration, but approval should be based on rendered previews and screenshots.
- In development mode, frontend page changes hot reload through Next. Electron main/preload changes and backend process changes still require restarting the desktop app.
- Desktop auth storage keeps only token and user profile data. It must not store raw passwords.
