# Workspace Development Progress

Last updated: 2026-04-29

## Product Direction

Team Agent is moving from a CLI-oriented developer tool to a desktop app where a non-technical user can create a website, mini app, dashboard, or app through an AI development-team workflow.

The core product unit is a workspace. A workspace represents a product project, not just a code repository.

## Current Flow

1. Create workspace
2. Confirm requirements
3. Confirm product plan
4. Confirm UI direction
5. Confirm prototype
6. Confirm technical plan
7. Execute development
8. Preview acceptance
9. Deploy test build

Each stage must support:

- AI/team recommendation
- User feedback
- Confirm approval
- Revision request
- Versioned artifacts over time

## Completed

- Electron desktop dev shell for macOS/Windows direction.
- Desktop startup stabilization with local backend and Next frontend.
- macOS frameless titlebar offset for native window controls.
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
- Stage recommendation generation:
  - LLM-first generation using user model settings
  - rule-based fallback when model is unavailable or returns invalid JSON
  - source display in UI

## In Progress

- UI visual prototype loop:
  - generate real HTML prototype: done
  - save prototype as workspace artifact: done
  - show preview in the prototype stage: done
  - generate desktop/mobile screenshots: next
  - run multimodal UI review: next

## Next

- Add Playwright screenshot generation:
  - desktop screenshot
  - mobile screenshot
  - artifact records for screenshots
- Add multimodal UI review:
  - send generated screenshots to vision-capable model
  - write review summary back to prototype stage
- Add workspace project repository:
  - local project path per workspace
  - checkpoint before code changes
  - file change summaries
  - rollback
- Connect workspace stages to Planning/Execution:
  - create planning session from workspace
  - write planning output back to stages
  - run development execution under workspace boundary

## Engineering Notes

- Every workspace-owned record must be isolated by `workspace_id`.
- Every user-owned access path must be authorized through `workspace_members`.
- Desktop UI can hide CLI details, but CLI should remain available for advanced users.
- Prototype confirmation must favor real rendered HTML/CSS over decorative generated images.
- Concept images are useful for style exploration, but approval should be based on rendered previews and screenshots.
