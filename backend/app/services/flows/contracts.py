"""Parsing and normalization helpers for staged flow LLM contracts."""

import json
from typing import Any, Dict


def json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def parse_json_object(text: str) -> Dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end >= start:
        cleaned = cleaned[start:end + 1]
    return json.loads(cleaned)


def sanitize_llm_artifact(payload: Dict[str, Any]) -> tuple[Dict[str, Any], str]:
    recommendation = payload.get("recommendation") if isinstance(payload.get("recommendation"), dict) else {}
    content = payload.get("content") if isinstance(payload.get("content"), str) else ""
    raw_options = recommendation.get("options") if isinstance(recommendation.get("options"), list) else []
    options = []
    for index, item in enumerate(raw_options):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or f"方案 {index + 1}").strip()
        description = str(item.get("description") or "").strip()
        option_content = str(item.get("content") or "").strip()
        options.append({
            "title": title,
            "description": description,
            "content": option_content,
            "recommended": bool(item.get("recommended")),
        })

    selected_option = str(recommendation.get("selected_option") or "").strip()
    if options and selected_option not in {item["title"] for item in options}:
        selected_option = next((item["title"] for item in options if item["recommended"]), options[0]["title"])

    sanitized = {
        "summary": str(recommendation.get("summary") or "AI 团队已生成本阶段推荐方案。"),
        "recommended_action": str(recommendation.get("recommended_action") or "请确认本阶段方案，或提交反馈让 AI 重新调整。"),
        "focus": recommendation.get("focus") if isinstance(recommendation.get("focus"), list) else [],
        "options": options,
        "selected_option": selected_option or None,
        "artifacts": recommendation.get("artifacts") if isinstance(recommendation.get("artifacts"), list) else [],
    }
    if not content and selected_option:
        selected = next((item for item in options if item["title"] == selected_option), None)
        if selected and selected.get("content"):
            content = selected["content"]
    if not content:
        content = "AI 已生成推荐，但没有返回详细产物。请提交反馈后重新生成。"
    return sanitized, content


def finalize_recommendation(recommendation: Dict[str, Any], fallback_content: str) -> tuple[Dict[str, Any], str]:
    raw_options = recommendation.get("options") if isinstance(recommendation.get("options"), list) else []
    options = []
    for index, item in enumerate(raw_options):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or f"方案 {index + 1}").strip()
        description = str(item.get("description") or "").strip()
        option_content = str(item.get("content") or "").strip()
        if not title:
            continue
        options.append({
            "title": title,
            "description": description,
            "content": option_content,
            "recommended": bool(item.get("recommended")),
        })

    selected_option = str(recommendation.get("selected_option") or "").strip()
    titles = {item["title"] for item in options}
    if options and selected_option not in titles:
        selected_option = next((item["title"] for item in options if item["recommended"]), options[0]["title"])

    selected_content = ""
    if selected_option:
        selected = next((item for item in options if item["title"] == selected_option), None)
        if selected:
            selected_content = selected.get("content") or ""
    if not selected_content:
        selected_content = fallback_content

    recommendation["options"] = options
    recommendation["selected_option"] = selected_option or None
    return recommendation, selected_content
