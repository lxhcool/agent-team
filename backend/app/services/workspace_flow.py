import json
import re
from typing import Any, Dict, List, Optional

from app.models.models import Workspace, WorkspaceStage, WorkspaceStageKey


def _stage_content(stages: List[WorkspaceStage], key: WorkspaceStageKey) -> str:
    for stage in stages:
        if stage.stage_key == key and stage.content:
            return stage.content
    return ""


def _stage_selected_option(stages: List[WorkspaceStage], key: WorkspaceStageKey) -> str:
    for stage in stages:
        if stage.stage_key != key or not stage.recommendation_json:
            continue
        try:
            recommendation = json.loads(stage.recommendation_json)
        except json.JSONDecodeError:
            continue
        selected = recommendation.get("selected_option")
        if isinstance(selected, str) and selected.strip():
            return selected.strip()
    return ""


def _platform_label(target_platform: str) -> str:
    return {
        "website": "网站",
        "miniapp": "小程序",
        "dashboard": "管理后台",
        "app": "应用",
    }.get(target_platform, target_platform)


def _compact_join(items: List[str], limit: int = 4) -> str:
    cleaned = [str(item).strip() for item in items if str(item).strip()]
    if not cleaned:
        return "待补充"
    return "、".join(cleaned[:limit])


def _score_keywords(text: str, keywords: List[str]) -> int:
    lowered = (text or "").lower()
    return sum(1 for keyword in keywords if keyword in lowered)


def _first_sentence(value: str, fallback: str) -> str:
    cleaned = " ".join((value or "").split())
    if not cleaned:
        return fallback
    parts = re.split(r"[。！？!?；;\n]", cleaned, maxsplit=1)
    return (parts[0] or cleaned)[:120]


def _extract_product_subject(*values: str) -> str:
    patterns = [
        r"^(帮我|请|想要|我要|我想|希望|需要|麻烦)?",
        r"(开发|做|制作|创建|搭建|实现|设计|生成|写|做个|搞一个)",
        r"(一个|一款|一套|一个能|一版|一个用于)?",
    ]
    suffix_pattern = r"(的)?(网页版本|网页版|web版|网站版|网站|网页|小程序|app|APP|应用|平台|系统)$"
    for raw in values:
        cleaned = " ".join((raw or "").split())
        if not cleaned:
            continue
        cleaned = re.split(r"[。！？!?；;\n]", cleaned, maxsplit=1)[0].strip()
        cleaned = re.split(r"[，,]", cleaned, maxsplit=1)[0].strip()
        cleaned = re.split(r"(需要|包括|包含|并且|并需|用于|用来|支持|展示|介绍)", cleaned, maxsplit=1)[0].strip()
        for pattern in patterns:
            cleaned = re.sub(pattern, "", cleaned, count=1).strip()
        cleaned = re.sub(suffix_pattern, "", cleaned).strip("：:，,。 ")
        if len(cleaned) >= 2:
            return cleaned[:24]
    return "这个产品"


def _display_name(workspace: Workspace, requirement: str, product: str) -> str:
    return _extract_product_subject(workspace.name, product, requirement, workspace.description or "") or workspace.name


def _normalize_line(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _split_units(*values: str) -> List[str]:
    units: List[str] = []
    for value in values:
        if not value:
            continue
        for part in re.split(r"[\n。！？!?；;]+", value):
            cleaned = _normalize_line(part)
            if len(cleaned) >= 4:
                units.append(cleaned)
    return units


def _dedupe(items: List[str], limit: int = 6) -> List[str]:
    seen = set()
    result = []
    for item in items:
        cleaned = _normalize_line(item)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
        if len(result) >= limit:
            break
    return result


def _derive_core_actions(text: str) -> List[str]:
    catalog = [
        ("开始体验", ["开始", "体验", "进入", "试玩"]),
        ("浏览内容", ["浏览", "阅读", "文章", "内容", "查看"]),
        ("提交信息", ["提交", "填写", "录入", "创建"]),
        ("输入提示词", ["提示词", "prompt", "指令"]),
        ("生成结果", ["生成", "输出", "分析", "总结", "转换"]),
        ("选择模型", ["模型", "llm", "gpt", "claude", "推理"]),
        ("上传材料", ["上传", "导入", "附件", "文件"]),
        ("查看列表", ["列表", "清单", "汇总"]),
        ("处理详情", ["详情", "编辑", "审核", "处理"]),
        ("查看数据", ["数据", "指标", "报表", "统计"]),
        ("联系咨询", ["咨询", "联系", "预约", "留资"]),
        ("继续协作", ["协作", "分配", "评论", "同步"]),
    ]
    hits = [label for label, keywords in catalog if any(keyword in text for keyword in keywords)]
    if hits:
        return _dedupe(hits, 4)
    return ["开始体验", "查看结果"]


def _derive_core_objects(text: str) -> List[str]:
    catalog = [
        ("任务", ["任务", "待办", "清单"]),
        ("文章", ["文章", "博客", "内容", "专栏"]),
        ("用户", ["用户", "成员", "客户"]),
        ("提示词", ["提示词", "prompt", "指令"]),
        ("模型", ["模型", "llm", "gpt", "claude"]),
        ("生成任务", ["生成任务", "创作任务", "生成"]),
        ("生成结果", ["结果", "输出", "生成内容", "成品"]),
        ("订单", ["订单", "交易", "支付"]),
        ("数据", ["数据", "指标", "报表", "统计"]),
        ("文件", ["文件", "文档", "附件", "素材"]),
        ("页面", ["页面", "界面", "首页"]),
        ("规则", ["规则", "流程", "步骤"]),
        ("案例", ["案例", "作品", "项目"]),
    ]
    hits = [label for label, keywords in catalog if any(keyword in text for keyword in keywords)]
    return _dedupe(hits or ["核心内容", "关键操作", "状态反馈"], 4)


def _derive_core_pages(text: str, actions: List[str]) -> List[Dict[str, str]]:
    catalog = [
        ("首页", ["首页", "首屏", "主页"], "承接第一眼认知、当前状态和主要入口。"),
        ("工作台", ["工作台", "控制台", "dashboard"], "承接登录后的主入口、任务状态和快捷操作。"),
        ("提示词输入", ["提示词", "prompt", "输入"], "收集生成指令、参数和辅助材料。"),
        ("输入", ["输入", "填写", "上传", "提交"], "收集本轮必须提供的信息与材料。"),
        ("结果", ["结果", "输出", "分析", "报告"], "承接生成结果、反馈状态和后续动作。"),
        ("历史记录", ["历史", "记录", "作品", "生成记录"], "回看之前生成过的任务、结果和状态。"),
        ("列表", ["列表", "清单", "汇总"], "集中展示对象集合、筛选条件和批量处理入口。"),
        ("详情", ["详情", "编辑", "审核", "处理"], "承接单个对象的细节、状态与进一步操作。"),
        ("数据总览", ["数据", "指标", "报表", "统计"], "优先回答当前整体状态，再引导进入具体处理。"),
        ("内容区", ["文章", "博客", "内容", "阅读"], "承接真实内容、摘要和继续浏览路径。"),
        ("规则说明", ["规则", "帮助", "说明"], "只保留用户继续操作所需的必要说明。"),
        ("联系入口", ["联系", "咨询", "预约", "留资"], "承接下一步沟通、咨询或转化动作。"),
        ("设置", ["设置", "配置", "偏好"], "放置非首要但需要长期维护的系统项。"),
    ]
    pages: List[Dict[str, str]] = []
    seen = set()
    for name, keywords, purpose in catalog:
        if any(keyword in text for keyword in keywords):
            pages.append({"name": name, "purpose": purpose})
            seen.add(name)
    if not pages:
        pages = [
            {"name": "首页", "purpose": "承接第一眼认知、当前状态和主要入口。"},
            {"name": actions[0] if actions else "核心流程", "purpose": "围绕当前需求中的主动作展开主要流程。"},
            {"name": "结果", "purpose": "承接反馈、状态和下一步动作。"},
        ]
    return pages[:4]


def _derive_open_questions(text: str, actions: List[str]) -> List[str]:
    questions: List[str] = []
    lowered = text.lower()
    if not any(token in lowered for token in ["用户", "角色", "给谁", "面向", "客户", "团队"]):
        questions.append("谁是第一版的核心使用者？")
    if not any(token in lowered for token in ["目标", "解决", "核心", "最重要", "主流程", "主操作"]):
        questions.append("这一版最核心的动作是什么？")
    if not any(token in lowered for token in ["暂不", "不做", "后续", "边界", "范围", "优先级"]):
        questions.append("这次明确不做什么，哪些后置？")
    if not any(token in lowered for token in ["数据", "内容来源", "导入", "上传", "同步", "接口"]):
        questions.append("关键内容或数据从哪里来？")
    if not actions:
        questions.append("用户完成主目标前需要经过哪些步骤？")
    return questions[:4]


def _build_requirement_brief(workspace: Workspace, stages: List[WorkspaceStage]) -> Dict[str, Any]:
    requirement = _stage_content(stages, WorkspaceStageKey.REQUIREMENTS) or workspace.description or workspace.name
    product = _stage_content(stages, WorkspaceStageKey.PRODUCT) or ""
    ui_mode = _stage_selected_option(stages, WorkspaceStageKey.UI_DIRECTION) or "专业清晰型"
    request_text = "\n".join(filter(None, [workspace.name, workspace.description or "", requirement, product]))
    surface = workspace.target_platform or "product"
    actions = _derive_core_actions(request_text)
    pages = _derive_core_pages(request_text, actions)
    objects = _derive_core_objects(request_text)
    sentences = _split_units(requirement, product, workspace.description or "")
    summary = _first_sentence(product or requirement, workspace.name)
    return {
        "surface": surface,
        "ui_mode": ui_mode,
        "app_name": _display_name(workspace, requirement, product),
        "summary": summary,
        "requirement": requirement,
        "product_plan": product,
        "core_actions": actions,
        "core_objects": objects,
        "core_pages": pages,
        "open_questions": _derive_open_questions(request_text, actions),
        "supporting_points": _dedupe(sentences[1:], 5),
        "source_units": _dedupe(sentences, 6),
    }


def _build_shape_from_brief(workspace: Workspace, brief: Dict[str, Any]) -> Dict[str, Any]:
    surface = str(brief["surface"])
    pages = brief.get("core_pages") if isinstance(brief.get("core_pages"), list) else []
    actions = brief.get("core_actions") if isinstance(brief.get("core_actions"), list) else []
    objects = brief.get("core_objects") if isinstance(brief.get("core_objects"), list) else []
    supporting = brief.get("supporting_points") if isinstance(brief.get("supporting_points"), list) else []
    blocks = [(str(item.get("name") or "模块"), str(item.get("purpose") or "围绕当前需求展开。")) for item in pages[:3] if isinstance(item, dict)]
    cards = []
    for index, label in enumerate(objects[:3]):
        purpose = supporting[index] if index < len(supporting) else f"{label} 需要直接承接当前需求里的真实信息与操作。"
        cards.append((label, purpose))
    sample_items = _dedupe(actions + objects + [str(item.get("name") or "") for item in pages], 5)
    sample_descriptions = supporting[:5]
    while len(sample_descriptions) < len(sample_items):
        label = sample_items[len(sample_descriptions)]
        sample_descriptions.append(f"{label} 这一项需要围绕当前需求提供真实内容，而不是说明性占位。")
    nav_items = [str(item.get("name") or "") for item in pages[:4] if isinstance(item, dict) and str(item.get("name") or "").strip()]
    if not nav_items:
        nav_items = _dedupe(actions or objects or ["首页"], 4)
    confirmation_points = _dedupe([
        "当前产物是否真正回应了原始需求？",
        f"主路径是否已经围绕「{actions[0]}」建立？" if actions else "主路径是否已经建立？",
        f"页面结构是否覆盖了 {_compact_join(nav_items, 3)}？",
        *brief.get("open_questions", []),
    ], 4)
    feature_label = f"{brief['app_name']} · {surface}"
    return {
        "surface": surface,
        "ui_mode": brief["ui_mode"],
        "app_name": brief["app_name"],
        "hero_title": brief["app_name"] or workspace.name,
        "hero_subtitle": brief["summary"],
        "confirmation_summary": brief["summary"],
        "primary_action": actions[0] if actions else "继续确认",
        "secondary_action": actions[1] if len(actions) > 1 else "补充细节",
        "nav_items": nav_items,
        "feature_label": feature_label,
        "status_label": "待确认",
        "panel_title": "当前结构理解",
        "blocks": blocks or [("核心模块", "围绕当前需求组织页面与流程。")] * 3,
        "cards": cards or [("核心内容", "需要承接真实信息与操作。")] * 3,
        "confirmation_points": confirmation_points,
        "sample_items": sample_items or ["当前目标", "主路径", "下一步"],
        "sample_descriptions": sample_descriptions or ["围绕当前需求补充真实内容。"] * 3,
        "primary_surface": surface,
        "workspace_name": workspace.name,
        "target_platform": workspace.target_platform,
        "platform_label": _platform_label(workspace.target_platform),
        "requirement": brief["requirement"],
        "product_plan": brief["product_plan"],
        "summary": brief["summary"],
        "core_actions": actions,
        "core_objects": objects,
        "core_pages": pages,
        "open_questions": brief["open_questions"],
        "experience_mode": "structured",
        "primary_goal": "confirm",
    }


def build_stage_generation_shape(workspace: Workspace, stages: List[WorkspaceStage]) -> Dict[str, Any]:
    brief = _build_requirement_brief(workspace, stages)
    return _build_shape_from_brief(workspace, brief)


def _module_component(name: str, surface: str) -> str:
    lowered = name.lower()
    if "输入" in name or "上传" in name:
        return "form"
    if "列表" in name or "清单" in name:
        return "table"
    if "详情" in name:
        return "detail"
    if "数据" in name or "总览" in name:
        return "stats"
    if "内容" in name or "文章" in name:
        return "article-list"
    if "联系" in name:
        return "contact"
    if "规则" in name:
        return "faq"
    return "feature-grid"


def build_interface_schema(workspace: Workspace, stages: List[WorkspaceStage]) -> Dict[str, Any]:
    brief = _build_requirement_brief(workspace, stages)
    shape = _build_shape_from_brief(workspace, brief)
    modules = []
    for index, page in enumerate(shape.get("core_pages", [])[:4]):
        if not isinstance(page, dict):
            continue
        label = str(page.get("name") or f"模块 {index + 1}")
        purpose = str(page.get("purpose") or "围绕当前需求展开。")
        component = _module_component(label, str(shape["primary_surface"]))
        modules.append({
            "id": f"module-{index + 1}",
            "key": f"module-{index + 1}",
            "label": label,
            "component": component,
            "title": f"{label} 需要直接承接当前需求中的真实内容",
            "summary": purpose,
            "bullets": _dedupe([
                f"{label} 不应该只是占位说明，而要能独立成立。",
                f"这里优先服务于「{shape['primary_action']}」这条主路径。",
                *brief.get("supporting_points", []),
            ], 3),
        })

    if not modules:
        modules = [{
            "id": "module-1",
            "key": "module-1",
            "label": "核心模块",
            "component": "feature-grid",
            "title": "先把核心模块组织出来",
            "summary": "围绕当前需求组织真实页面与交互，而不是套固定栏目。",
            "bullets": ["先确认主路径。", "再确认页面组成。", "最后补辅助信息。"],
        }]

    components = {module["component"] for module in modules}
    if "playground" in components:
        layout_mode = "immersive"
    elif {"stats", "table", "detail"} & components:
        layout_mode = "console"
    elif "article-list" in components:
        layout_mode = "editorial"
    else:
        layout_mode = "marketing"

    return {
        "app_name": shape["app_name"],
        "platform_label": shape["platform_label"],
        "layout_mode": layout_mode,
        "ui_mode": shape["ui_mode"],
        "hero_title": shape["hero_title"],
        "hero_subtitle": shape["hero_subtitle"],
        "primary_action": shape["primary_action"],
        "secondary_action": shape["secondary_action"],
        "nav_items": shape["nav_items"],
        "modules": modules,
        "focus_points": shape["confirmation_points"],
        "summary": shape["summary"],
        "feature_label": f"{shape['app_name']} · schema",
        "status_label": "可确认",
    }


def _make_option(title: str, description: str, content: str, recommended: bool = False) -> Dict[str, Any]:
    return {
        "title": title,
        "description": description,
        "content": content,
        "recommended": recommended,
    }


def _finalize_recommendation(recommendation: Dict[str, Any], fallback_content: str) -> tuple[Dict[str, Any], str]:
    raw_options = recommendation.get("options") if isinstance(recommendation.get("options"), list) else []
    options = []
    for index, item in enumerate(raw_options):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or f"方案 {index + 1}").strip()
        if not title:
            continue
        options.append({
            "title": title,
            "description": str(item.get("description") or "").strip(),
            "content": str(item.get("content") or "").strip(),
            "recommended": bool(item.get("recommended")),
        })
    selected_option = str(recommendation.get("selected_option") or "").strip()
    titles = {item["title"] for item in options}
    if options and selected_option not in titles:
        selected_option = next((item["title"] for item in options if item["recommended"]), options[0]["title"])
    selected_content = fallback_content
    if selected_option:
        selected = next((item for item in options if item["title"] == selected_option), None)
        if selected and selected.get("content"):
            selected_content = selected["content"]
    recommendation["options"] = options
    recommendation["selected_option"] = selected_option or None
    return recommendation, selected_content


def generate_stage_artifact(
    workspace: Workspace,
    stages: List[WorkspaceStage],
    stage: WorkspaceStage,
    instruction: Optional[str],
) -> tuple[Dict[str, Any], str]:
    content = "\n".join([
        "这个规则生成器已经停用，不再负责输出阶段内容。",
        "",
        "当前流程已经改成模型优先的真实对话与文档整理，所以这里不再返回任何模板式阶段稿。",
        "如果当前阶段没有拿到模型回复，系统应该直接提示模型不可用，而不是继续伪造内容。",
    ])
    recommendation = {
        "source": "legacy_rule_generator_disabled",
        "summary": "规则生成器已停用。",
        "recommended_action": "请改走模型对话链，或先检查模型配置。",
        "focus": ["模型可用性", "真实对话", "禁用模板回退"],
        "options": [_make_option("规则生成器已停用", "当前不再使用固定规则输出阶段内容。", content, True)],
        "selected_option": "规则生成器已停用",
        "artifacts": [],
    }
    return _finalize_recommendation(recommendation, content)
