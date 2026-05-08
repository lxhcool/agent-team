# Agent Constraints

This repository uses conversational, stage-based product workflows. Agents working here must follow these rules:

## Conversation Rules

- Treat each stage as a real multi-turn conversation, not a template renderer.
- Do not regenerate a full stage summary on every user reply.
- On follow-up turns, respond only to the new information the user added and push the conversation forward.
- When producing a stage conclusion, write a true summary of confirmed decisions. Do not reuse the last assistant reply as the conclusion.
- In requirement clarification, do not push basic product thinking back to the user. The agent should first provide a sensible recommended version, then let the user confirm or correct it.
- Separate requirement content into must-have workflow items, recommended defaults, and true decision points. Must-have items should be included directly, not asked back as questions.
- Use A/B/C choices only for decisions that materially affect scope, complexity, or delivery. Always mark a recommended option and allow the user to simply reply OK to accept it.

## Decision Rules

- Do not use hardcoded keyword lists to infer user intent for semantic workflow decisions.
- In particular, do not use fixed trigger phrases to decide whether a stage should end, whether the user is satisfied, or whether a summary should be generated.
- These decisions must be made from full conversational context, preferably by model judgment with conservative fallback behavior.

## Output Rules

- Avoid report-like template headings in stage chat replies unless the user explicitly asked for a document.
- Avoid generic boilerplate such as "当前匹配判断", "已对上的信息", "第一版主线", "范围边界", or similar analysis framing in conversational replies.
- Requirement clarification should sound like a product teammate advancing the discussion, not like a static form or rubric.
- First-stage replies should be concrete to the user's product category. They may include short lists, but should not read like a reusable questionnaire.

## Fallback Rules

- If a model-based semantic decision is unavailable, fail conservatively instead of introducing brittle hardcoded heuristics.
- For requirement clarification, fallback behavior should still preserve conversational continuity and should not fall back to generic product templates.
