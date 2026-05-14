"""Structured stage snapshots for compact cross-stage handoff."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.models.models import Workspace, WorkspaceStage, WorkspaceStageStatus

SNAPSHOT_VERSION = 2


def _load_recommendation(stage: WorkspaceStage) -> Dict[str, Any]:
    if not stage.recommendation_json:
        return {}
    try:
        value = json.loads(stage.recommendation_json)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _clean_inline(text: str) -> str:
    value = (text or "").strip()
    value = re.sub(r"^#{1,6}\s*", "", value)
    value = re.sub(r"[*_`]+", "", value)
    value = re.sub(r"^\s*[-*]\s+", "", value)
    value = re.sub(r"^\s*\d+\.\s+", "", value)
    value = re.sub(r"^\s*[一二三四五六七八九十]+\s*[、.．]\s*", "", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _compact_excerpt(text: str, *, max_len: int = 180) -> str:
    value = _clean_inline(text)
    if not value:
        return ""
    if len(value) <= max_len:
        return value
    shortened = value[: max_len - 1].rstrip("，、；：,. ")
    return f"{shortened}…"


def _split_non_empty_lines(text: str) -> List[str]:
    return [_clean_inline(line) for line in (text or "").splitlines() if _clean_inline(line)]


def _sentence_count(text: str) -> int:
    return len(re.findall(r"[。！？!?]", text or ""))


def _bullet_count(text: str) -> int:
    return len(re.findall(r"(?m)^\s*(?:[-*]|\d+\.)\s+", text or ""))


def _metadata_like_line_count(text: str) -> int:
    count = 0
    for line in _split_non_empty_lines(text):
        if len(line) > 28:
            continue
        colon_count = line.count("：") + line.count(":")
        if colon_count == 1:
            count += 1
    return count


def _question_count(text: str) -> int:
    return (text or "").count("？") + (text or "").count("?")


def _content_signal_score(title: str, body: str) -> int:
    normalized_title = _clean_inline(title)
    normalized_body = normalize_whitespace(body)
    body_len = len(normalized_body)
    sentence_count = _sentence_count(body)
    bullet_count = _bullet_count(body)
    metadata_like_count = _metadata_like_line_count(body)
    question_count = _question_count(body)

    score = body_len
    score += min(sentence_count, 6) * 18
    score += min(bullet_count, 6) * 14
    score -= metadata_like_count * 18
    score -= question_count * 12

    if len(normalized_title) <= 4:
        score -= 12
    if body_len < 30:
        score -= 50
    return score


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _is_low_signal_summary(text: str) -> bool:
    value = _clean_inline(text)
    if not value:
        return True
    if len(value) < 24:
        return True
    if _question_count(value) >= 2:
        return True
    if len(value) <= 40 and _metadata_like_line_count(value) >= 1:
        return True
    return False


def _should_skip_section(title: str, body: str) -> bool:
    normalized_title = _clean_inline(title)
    if not normalized_title:
        return True
    if _content_signal_score(normalized_title, body) < 48:
        return True
    return False


def _split_paragraphs(content: str) -> List[str]:
    return [
        _clean_inline(block)
        for block in re.split(r"\n\s*\n", content or "")
        if _clean_inline(block)
    ]


def _extract_markdown_sections(content: str) -> List[Dict[str, str]]:
    sections: List[Dict[str, str]] = []
    current_title: Optional[str] = None
    current_lines: List[str] = []

    for raw_line in (content or "").splitlines():
        line = raw_line.rstrip()
        heading_match = re.match(r"^\s{0,3}#{2,4}\s+(.+?)\s*$", line)
        if heading_match:
            if current_title:
                body = "\n".join(current_lines).strip()
                if body:
                    sections.append({"title": current_title, "body": body})
            current_title = _clean_inline(heading_match.group(1))
            current_lines = []
            continue
        if current_title:
            current_lines.append(line)

    if current_title:
        body = "\n".join(current_lines).strip()
        if body:
            sections.append({"title": current_title, "body": body})
    return sections


def _extract_key_points(content: str, *, limit: int = 4) -> List[str]:
    items: List[str] = []
    seen: set[str] = set()
    for raw_line in (content or "").splitlines():
        match = re.match(r"^\s*(?:[-*]|\d+\.)\s+(.+?)\s*$", raw_line)
        if not match:
            continue
        cleaned = _compact_excerpt(match.group(1), max_len=120)
        normalized = re.sub(r"\W+", "", cleaned)
        if not cleaned or not normalized or normalized in seen:
            continue
        seen.add(normalized)
        items.append(cleaned)
        if len(items) >= limit:
            break
    return items


def build_stage_snapshot(stage: WorkspaceStage, recommendation: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    rec = recommendation if isinstance(recommendation, dict) else _load_recommendation(stage)
    existing = rec.get("approved_snapshot")
    if (
        isinstance(existing, dict)
        and int(existing.get("snapshot_version") or 0) == SNAPSHOT_VERSION
        and existing.get("content_source") == (stage.content or "").strip()
    ):
        return existing

    content = (stage.content or "").strip()
    if not content:
        return {}

    sections: List[Dict[str, str]] = []
    for section in _extract_markdown_sections(content):
        title = _clean_inline(section.get("title", ""))
        body = section.get("body", "")
        if _should_skip_section(title, body):
            continue
        summary = _compact_excerpt(body, max_len=190)
        if not summary or _is_low_signal_summary(summary):
            continue
        sections.append({"title": title, "summary": summary})
        if len(sections) >= 4:
            break

    if not sections:
        for paragraph in _split_paragraphs(content):
            summary = _compact_excerpt(paragraph, max_len=190)
            if not summary or _is_low_signal_summary(summary):
                continue
            sections.append({"title": "摘要", "summary": summary})
            if len(sections) >= 3:
                break

    overview = sections[0]["summary"] if sections else _compact_excerpt(content, max_len=220)
    if _is_low_signal_summary(overview):
        for section in sections[1:]:
            candidate = str(section.get("summary") or "").strip()
            if candidate and not _is_low_signal_summary(candidate):
                overview = candidate
                break
    key_points = _extract_key_points(content, limit=4)
    if not key_points:
        key_points = [item["summary"] for item in sections[:3]]
    key_points = [item for item in key_points if not _is_low_signal_summary(item)]
    if not key_points:
        key_points = [item["summary"] for item in sections[:3] if not _is_low_signal_summary(item.get("summary", ""))]

    raw_artifacts = rec.get("artifacts") if isinstance(rec.get("artifacts"), list) else []
    artifacts: List[Dict[str, str]] = []
    for item in raw_artifacts[:4]:
        if not isinstance(item, dict):
            continue
        artifacts.append({
            "type": str(item.get("type") or "").strip(),
            "label": str(item.get("label") or "").strip(),
            "artifact_id": str(item.get("artifact_id") or "").strip(),
            "url": str(item.get("url") or "").strip(),
        })

    return {
        "snapshot_version": SNAPSHOT_VERSION,
        "stage_key": stage.stage_key.value,
        "stage_title": stage.title,
        "overview": overview,
        "sections": sections,
        "key_points": key_points[:4],
        "artifacts": artifacts,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "content_source": content,
    }


def with_stage_snapshot(recommendation: Dict[str, Any], stage: WorkspaceStage) -> Dict[str, Any]:
    snapshot = build_stage_snapshot(stage, recommendation)
    if snapshot:
        recommendation["approved_snapshot"] = snapshot
    return recommendation


def stage_snapshot(stage: WorkspaceStage) -> Dict[str, Any]:
    recommendation = _load_recommendation(stage)
    snapshot = recommendation.get("approved_snapshot")
    if (
        isinstance(snapshot, dict)
        and int(snapshot.get("snapshot_version") or 0) == SNAPSHOT_VERSION
        and snapshot.get("content_source") == (stage.content or "").strip()
    ):
        return snapshot
    return build_stage_snapshot(stage, recommendation)


def _approved_upstream_stages(stages: List[WorkspaceStage], current_stage: WorkspaceStage) -> List[WorkspaceStage]:
    return [
        item
        for item in stages
        if item.id != current_stage.id
        and item.status == WorkspaceStageStatus.APPROVED
        and (item.content or "").strip()
    ]


def build_stage_snapshot_digest(stages: List[WorkspaceStage], current_stage: WorkspaceStage) -> str:
    approved_stages = _approved_upstream_stages(stages, current_stage)
    if not approved_stages:
        return "暂无可用的已批准阶段快照。"

    blocks: List[str] = []
    for item in approved_stages:
        snapshot = stage_snapshot(item)
        if not snapshot:
            continue
        lines = [f"[{snapshot.get('stage_title') or item.title}]"]
        overview = str(snapshot.get("overview") or "").strip()
        if overview:
            lines.append(f"- 阶段结论：{overview}")
        for section in snapshot.get("sections", [])[:3]:
            if not isinstance(section, dict):
                continue
            title = _clean_inline(str(section.get("title") or ""))
            summary = _clean_inline(str(section.get("summary") or ""))
            if title and summary:
                lines.append(f"- {title}：{summary}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) if blocks else "暂无可用的已批准阶段快照。"


def collect_stage_artifacts(stages: List[WorkspaceStage], current_stage: WorkspaceStage) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in stages:
        if item.id == current_stage.id:
            continue
        recommendation = _load_recommendation(item)
        artifacts = recommendation.get("artifacts")
        if not isinstance(artifacts, list):
            continue
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            artifact_id = str(artifact.get("artifact_id") or "").strip()
            url = str(artifact.get("url") or "").strip()
            if not artifact_id and not url:
                continue
            dedupe_key = artifact_id or url
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            entry = dict(artifact)
            entry["stage_key"] = item.stage_key.value
            entry["stage_title"] = item.title
            entry.setdefault("label", f"{item.title}文档")
            entries.append(entry)
    return entries


def build_stage_artifact_digest(stages: List[WorkspaceStage], current_stage: WorkspaceStage) -> str:
    artifacts = collect_stage_artifacts(stages, current_stage)
    if not artifacts:
        return "- 暂无可用交付物。"
    lines = []
    for artifact in artifacts[:8]:
        lines.append(
            f"- {str(artifact.get('stage_title') or '对应阶段').strip()}："
            f"{str(artifact.get('label') or artifact.get('type') or '阶段文档').strip()}"
        )
    return "\n".join(lines)


def render_final_delivery_summary(
    workspace: Workspace,
    stages: List[WorkspaceStage],
    current_stage: WorkspaceStage,
) -> str:
    approved_stages = _approved_upstream_stages(stages, current_stage)
    if not approved_stages:
        return "当前还没有足够的已确认阶段内容，暂时无法整理最终交付文档。"

    snapshot_map = {item.stage_key.value: stage_snapshot(item) for item in approved_stages}
    artifacts = collect_stage_artifacts(stages, current_stage)

    def _append_points(lines: List[str], snapshot: Optional[Dict[str, Any]], *, limit: int = 3) -> None:
        if not snapshot:
            return
        points = snapshot.get("key_points") if isinstance(snapshot.get("key_points"), list) else []
        appended = 0
        for point in points:
            text = _clean_inline(str(point or ""))
            if not text:
                continue
            lines.append(f"- {text}")
            appended += 1
            if appended >= limit:
                break

    def _append_section(
        lines: List[str],
        title: str,
        snapshots: List[Optional[Dict[str, Any]]],
        *,
        point_limit: int = 4,
    ) -> None:
        valid = [item for item in snapshots if item]
        if not valid:
            return

        lines.extend([f"## {title}", ""])
        for index, snapshot in enumerate(valid):
            overview = _clean_inline(str(snapshot.get("overview") or ""))
            if overview:
                lines.append(overview)
                lines.append("")
            _append_points(lines, snapshot, limit=point_limit if index == 0 else max(1, point_limit - 1))
            if lines and lines[-1] != "":
                lines.append("")

    project_name = _clean_inline(getattr(workspace, "name", "") or "")
    title = "最终交付文档"
    if project_name:
        title = f"{project_name} 最终交付文档"

    lines: List[str] = [f"# {title}", ""]

    requirements = snapshot_map.get("requirements")
    product = snapshot_map.get("product")
    ui_direction = snapshot_map.get("ui_direction")
    technical = snapshot_map.get("technical")

    combined_overviews = [
        _clean_inline(str(snapshot.get("overview") or ""))
        for snapshot in [requirements, product, ui_direction, technical]
        if snapshot and _clean_inline(str(snapshot.get("overview") or ""))
    ]
    if combined_overviews:
        lines.extend([
            "这份文档基于已确认阶段内容整理，供汇报、交接和继续执行使用。",
            "",
        ])

    _append_section(lines, "项目结论", [requirements, product], point_limit=3)
    _append_section(lines, "规则与边界", [ui_direction], point_limit=4)
    _append_section(lines, "开发实现", [technical], point_limit=4)

    lines.extend([
        "## 交付文档",
        "",
        "| 阶段 | 文档 | 用途 |",
        "| --- | --- | --- |",
    ])
    for artifact in artifacts:
        lines.append(
            f"| {str(artifact.get('stage_title') or '对应阶段').strip()} | "
            f"{str(artifact.get('label') or artifact.get('type') or '阶段文档').strip()} | "
            "用于该阶段的确认与后续交接 |"
        )

    lines.extend([
        "",
        "以上内容已经足以作为完整交付物继续流转。",
    ])
    return "\n".join(lines).strip()


def render_final_delivery_fallback(stages: List[WorkspaceStage], current_stage: WorkspaceStage) -> str:
    class _WorkspaceStub:
        name = ""

    return render_final_delivery_summary(_WorkspaceStub(), stages, current_stage)
