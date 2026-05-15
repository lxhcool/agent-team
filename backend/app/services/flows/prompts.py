"""Prompt builders for staged flow conversations."""

from typing import Any, Dict, List, Optional

from app.models.models import Workspace, WorkspaceStage, WorkspaceStageKey, WorkspaceStageMessage, WorkspaceStageStatus


def stage_responsibility_contract(stage_key: WorkspaceStageKey) -> Dict[str, Any]:
    contract_map: Dict[WorkspaceStageKey, Dict[str, Any]] = {
        WorkspaceStageKey.REQUIREMENTS: {
            "chat_scope": "确认产品定位和最基本的使用方式。",
            "chat_focus": "产品定义、基本使用方式、必要参与者关系、会影响后续方向的前提",
            "chat_avoid": [
                "产品方案、功能清单、功能拆分、页面结构、管理结构",
                "规则细则、交互字段、运营口径",
                "完整业务流程、技术实现、数据设计、交付方式",
            ],
            "ask_rule": "只有缺少产品定义、基本使用方式或会改变产品形态的参与者关系时，才补 1 个问题；问题不能要求用户设计功能、页面、后台、权限或技术方案。",
            "completion_requirements": [
                "产品定义已经清楚，不只是一个名字",
                "最基本的使用或呈现方式已经清楚",
                "会改变后续方向的关键前提已经明确",
                "如果确实存在会改变结构的多主体关系，相关关系已经对齐",
            ],
            "readiness_rules": [
                "只有会改变产品定位或基本使用方式的缺口，才算 blocker",
                "不要把功能、字段、页面、技术实现当成 blocker",
                "对结构很简单的产品，不要为了完整度强行补额外主体或边界",
            ],
            "document_identity": "需求确认文档",
            "document_must_answer": [
                "这是一个什么产品",
                "它最基本是怎么被使用、被阅读、被提交或被消费的",
                "主要解决什么事",
                "当前已经明确的结构性前提",
                "为什么这些信息已经足够支撑后续阶段",
            ],
            "document_avoid": [
                "功能模块、页面结构、后台结构、技术方案",
                "待确认项、未完成清单",
                "把实现细节写成结构性前提",
                "没有内容支撑的固定栏目",
            ],
            "forbidden_sections": [
                "功能拆分类栏目",
                "页面结构类栏目",
                "管理结构类栏目",
                "技术方案类栏目",
                "未结束事项类栏目",
            ],
            "upstream_rule": "这是第一阶段文档，直接围绕产品理解展开。",
            "style": "自然、克制，围绕当前内容组织。",
        },
        WorkspaceStageKey.PRODUCT: {
            "chat_scope": "搭出稳定的方案骨架。",
            "chat_focus": "功能模块、模块关系、页面结构、主流程",
            "chat_avoid": [
                "重写上游产品理解和基础前提",
                "规则细节、边界口径、异常处理",
                "实现方案、技术选型、数据组织",
            ],
            "ask_rule": "先给方案骨架，再按反馈微调；只有骨架缺口才追问。",
            "completion_requirements": [
                "已经形成方案层新增内容，不只是重写第一阶段",
                "主要功能模块已经成体系",
                "模块之间的关系已经说清楚",
                "页面结构已经能承接模块设计",
                "主要流程已经闭环",
            ],
            "readiness_rules": [
                "blocker 只围绕模块结构、页面结构、主流程骨架",
                "不要把规则细节或实现设计当成 blocker",
            ],
            "document_identity": "结构化方案稿",
            "document_must_answer": [
                "主要功能模块有哪些",
                "模块之间如何协作",
                "页面结构如何组织",
                "主要流程如何流转",
            ],
            "document_avoid": [
                "重写上游背景或目标",
                "重新展开上游已确认约束",
                "深入权限、状态流转、异常规则细节",
            ],
            "forbidden_sections": ["上游背景类栏目", "上游对象定义类栏目", "上游目标类栏目", "上游前提总览类栏目"],
            "upstream_rule": "如需承接上游，只做极短说明；正文从本阶段新增方案开始。",
            "style": "按模块、关系、结构、流程组织，保持自然。",
        },
        WorkspaceStageKey.UI_DIRECTION: {
            "chat_scope": "把规则、状态和边界讲清楚。",
            "chat_focus": "角色权限、状态流转、异常处理、数据口径、边界条件",
            "chat_avoid": [
                "回到产品定义或方案骨架层重讲",
                "重讲功能结构或页面结构",
                "实现方案或技术设计",
            ],
            "ask_rule": "优先补关键规则并直接给口径；只有真的会改方向才追问。",
            "completion_requirements": [
                "已经形成规则层新增内容，不只是重复方案骨架",
                "角色权限已经清楚，或已经有足够简单的默认口径",
                "关键状态流转已经清楚",
                "主要异常和边界已经覆盖",
            ],
            "readiness_rules": [
                "规则已经足够支撑开发时，不要强行补新的权限、状态或异常问题",
                "blocker 必须是规则层问题，不要回退成结构或技术选型问题",
            ],
            "document_identity": "规则文档",
            "document_must_answer": [
                "角色和权限怎么定义",
                "状态如何流转",
                "异常情况如何处理",
                "数据口径如何统一",
                "关键边界条件是什么",
            ],
            "document_avoid": [
                "重讲模块结构或整体方案",
                "技术架构或实现方案",
                "页面设计说明",
            ],
            "forbidden_sections": ["上游背景类栏目", "上游对象定义类栏目", "方案骨架类栏目", "页面结构总览类栏目"],
            "upstream_rule": "如需承接上游，只做极短说明；正文只写规则、状态和边界。",
            "style": "围绕规则、状态、边界展开，避免回写上游。",
        },
        WorkspaceStageKey.TECHNICAL: {
            "chat_scope": "整理开发可接手的实现方案。",
            "chat_focus": "实现路径、模块拆分、关键实现约定、依赖风险、实施顺序",
            "chat_avoid": [
                "回头再问产品定义、方案骨架或页面结构",
                "把常规实现判断都甩给用户",
                "把方案写成已经开发完成",
                "制造新的待确认项",
            ],
            "ask_rule": "先给可落地的默认实现方案；只有会改变交付路径或风险的点才追问。",
            "completion_requirements": [
                "已经形成开发可接手的实现方案，不只是业务复述",
                "技术落地方式已经明确",
                "模块拆分和关键实现约定已经清楚",
                "依赖、风险和实施顺序已经说明",
            ],
            "readiness_rules": [
                "不要因为理论上还能继续展开就返回 false",
                "只有会改变交付路径或实现风险的缺口，才算 blocker",
                "blocker 必须停留在实现交接层",
            ],
            "document_identity": "开发方案文档",
            "document_must_answer": [
                "技术落地方式是什么",
                "模块怎么拆",
                "开发接手时最关键的实现约定是什么",
                "依赖和风险是什么",
                "建议的开发顺序是什么",
            ],
            "document_avoid": [
                "重复产品背景和整体需求说明",
                "只停留在技术栈口号",
                "为了完整度硬补接口清单、数据库设计或部署架构",
                "伪装成代码已经完成",
            ],
            "forbidden_sections": ["上游背景类栏目", "上游对象定义类栏目", "业务价值类栏目", "规则总览类栏目"],
            "upstream_rule": "如需承接上游，只做极短说明；正文只写实现与交接层内容。",
            "style": "工程化、直接，复杂项目展开更细，简单项目保持轻量。",
        },
        WorkspaceStageKey.DEPLOYMENT: {
            "chat_scope": "整理最终总结与交付文档。",
            "chat_focus": "最终总结、交付文档、附件索引、下载方式",
            "chat_avoid": [
                "重新讨论前面阶段的方案内容",
                "再生成一篇重复的阶段总结",
                "新增会改变结论的内容",
            ],
            "ask_rule": "默认不追问，直接整理最终交付。",
            "completion_requirements": [
                "当前阶段主体已经是最终总结和交付文档",
                "文档集合已经齐全",
                "每份文档的作用已经说明",
                "获取方式已经说明",
            ],
            "readiness_rules": [
                "blocker 只能是交付物缺失、附件缺失或交付方式不清",
                "不要把产品、规则、实现方案层的问题重新带回这个阶段",
            ],
            "document_identity": "最终交付文档",
            "document_must_answer": [
                "最终确认下来的项目结论是什么",
                "一共有哪些文档",
                "每份文档属于哪个阶段",
                "每份文档解决什么问题",
                "如何单独下载",
                "如何整体打包下载",
            ],
            "document_avoid": [
                "大段复述前面阶段正文",
                "重新生成一篇空泛项目总结",
                "新增核心方案内容",
            ],
            "forbidden_sections": ["上游背景类栏目", "上游对象定义类栏目", "功能结构类栏目", "页面结构类栏目", "技术方案正文类栏目"],
            "upstream_rule": "这里只承接前面阶段的最终结果；主体是最终总结和交付说明。",
            "style": "像正式交付稿，先总结，再说明交付物。",
        },
    }
    return contract_map.get(
        stage_key,
        {
            "chat_scope": "围绕当前阶段的职责继续推进。",
            "chat_focus": "只处理当前阶段真正新增的内容。",
            "chat_avoid": ["不要重复上游文档内容", "不要提前进入后续阶段"],
            "ask_rule": "优先吸收上下文并推进，只有真的阻塞时才补问题。",
            "completion_requirements": ["当前阶段的核心信息已经补完整。"],
            "readiness_rules": ["只把真正阻塞当前阶段结束的问题当成 blocker。"],
            "document_identity": "阶段文档",
            "document_must_answer": ["回答当前阶段最核心的问题"],
            "document_avoid": ["不要重复上游文档内容"],
            "forbidden_sections": [],
            "upstream_rule": "如需承接上游，只能简短带过。",
            "style": "自然、直接，只保留本阶段新增确认。",
        },
    )


def stage_completion_requirements(stage_key: WorkspaceStageKey) -> List[str]:
    contract = stage_responsibility_contract(stage_key)
    return contract.get("completion_requirements", ["当前阶段的核心信息已经补完整。"])


def build_auxiliary_context_block(
    *,
    stage_skill_context: str = "",
    external_reference_context: str = "",
) -> str:
    sections: List[str] = []
    if stage_skill_context.strip():
        sections.append(
            "\n".join(
                [
                    "当前阶段辅助能力（只作为你的工作方式约束，不要原样复述给用户）：",
                    stage_skill_context.strip(),
                ]
            )
        )
    if external_reference_context.strip():
        sections.append(
            "\n".join(
                [
                    "外部成熟案例参考（仅作参考，不要生搬硬套，也不要逐条引用给用户）：",
                    external_reference_context.strip(),
                ]
            )
        )
    return "\n\n".join(sections).strip()


def messages_to_prompt_text(messages: List[WorkspaceStageMessage]) -> str:
    lines: List[str] = []
    for message in messages:
        speaker = "用户" if message.role == "user" else "助手"
        lines.append(f"{speaker}：{message.content.strip()}")
    return "\n".join(lines).strip()


def recent_messages_to_prompt_text(
    messages: List[WorkspaceStageMessage],
    *,
    limit_messages: int = 8,
    limit_chars: int = 2200,
) -> str:
    selected = [item for item in messages if item.content.strip()][-limit_messages:]
    transcript = messages_to_prompt_text(selected)
    if len(transcript) <= limit_chars:
        return transcript
    return f"…\n{transcript[-limit_chars:]}"


def requirements_user_messages(messages: List[WorkspaceStageMessage]) -> List[str]:
    return [item.content.strip() for item in messages if item.role == "user" and item.content.strip()]


def requirements_latest_user_message(messages: List[WorkspaceStageMessage]) -> str:
    for item in reversed(messages):
        if item.role == "user" and item.content.strip():
            return item.content.strip()
    return ""


def stage_document_guidance(stage_key: WorkspaceStageKey) -> Dict[str, Any]:
    contract = stage_responsibility_contract(stage_key)
    return {
        "identity": contract.get("document_identity", "阶段文档"),
        "must_answer": contract.get("document_must_answer", ["回答当前阶段最核心的问题"]),
        "avoid": contract.get("document_avoid", ["不要重复上游文档内容"]),
        "upstream_rule": contract.get("upstream_rule", "如需承接上游，只能简短带过。"),
        "forbidden_sections": contract.get("forbidden_sections", []),
        "style": contract.get("style", "只保留本阶段新增确认。"),
    }


def format_bullets(items: List[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "- 无"


def build_stage_output_completion_prompt(
    stage: WorkspaceStage,
    *,
    should_finalize: bool,
    content: str,
) -> str:
    guidance = stage_document_guidance(stage.stage_key)
    return f"""
当前阶段：{stage.title}
输出类型：{"阶段结论文档" if should_finalize else "阶段对话回复"}

请判断下面这段输出是否已经完整。

判断原则：
1. 如果内容明显像被截断、半句话、半个问题、编号没写完、列点没写完，返回 false。
2. 如果内容语义上已经完整结束，返回 true。
3. 如果是阶段结论文档，除是否截断外，还要判断它是否已经基本覆盖当前阶段该有的内容重心。
4. 如果只是还能写得更长，但当前已经完整，不要因为“还能再补充”就返回 false。

当前阶段重心：
- 文档身份：{guidance["identity"]}
- 需要回答：{", ".join(guidance["must_answer"])}

当前输出：
{content[-5000:]}

只返回 JSON：
{{"is_complete": true, "reason": "一句话原因"}}
""".strip()


def build_stage_output_continuation_prompt(
    stage: WorkspaceStage,
    *,
    should_finalize: bool,
    content: str,
) -> str:
    completion_escape = (
        "\n5. 如果前文其实已经完整，只是你上一轮被误判为未完成，请只返回 `__COMPLETE__`，不要点评前文，不要改写前文，不要给编辑建议。"
        if should_finalize
        else ""
    )
    return f"""
你刚才那段{"文档" if should_finalize else "回复"}还没写完整。

请继续刚才的内容，只补尚未说完的部分。

要求：
1. 不要重复前文，不要从头再写。
2. 直接顺着刚才中断的地方继续。
3. 保持同样的语气和结构。
4. 如果前文已经完整，就不要另起一段泛泛补充。
{completion_escape}

已生成内容：
{content[-4000:]}
""".strip()


def build_stage_finalization_readiness_prompt(
    workspace: Workspace,
    stage: WorkspaceStage,
    messages: List[WorkspaceStageMessage],
) -> str:
    transcript = messages_to_prompt_text(messages)
    requirement = (workspace.description or workspace.name or "").strip()
    guidance = stage_document_guidance(stage.stage_key)
    contract = stage_responsibility_contract(stage.stage_key)
    completion_requirements = stage_completion_requirements(stage.stage_key)
    readiness_rules = contract.get("readiness_rules", [])
    checklist_instruction = "\n".join(
        [f"{index}. {item}" for index, item in enumerate(readiness_rules, start=1)]
    )
    return f"""
当前阶段：{stage.title}
项目原始需求：{requirement}

请判断：基于当前阶段对话，是否已经可以结束这一阶段，并整理出一份合格的阶段结论文档。

判断标准：
1. 只有在当前信息已经足够支撑本阶段正式结束，并且不会一进入下一阶段就发现明显缺口时，才返回 true。
2. 用户表达想结束当前阶段，不等于真的可以结束；你要按内容是否足够来判断。
3. 如果只是还能补充更多细节，但不影响下一阶段推进，可以返回 true。
4. 如果还缺少会影响下一阶段结构或当前阶段文档质量的关键点，返回 false。
5. 如果当前内容大部分只是复述上游阶段，而本阶段新增确认还没有形成，必须返回 false。
6. blockers 只保留 1 到 3 个，必须是真正阻塞当前阶段结束的问题。
7. 可以把当前对话里已经形成的稳定理解一起纳入判断，不要机械要求每个点都由用户单独重复确认一遍。
8. 如果上一轮助手已经把当前阶段的理解、判断或方案整理得很清楚，而用户这轮只是口语化地确认、收束或要求推进，并且没有新增会改变判断的信息，只要内容本身足够，就应该返回 true，不要再多走一轮聊天。

当前阶段文档要回答：
- 文档身份：{guidance["identity"]}
- 需要回答：{", ".join(guidance["must_answer"])}

当前阶段职责边界：
- 当前阶段只负责：{contract.get("chat_focus", guidance["identity"])}
- 当前阶段不要展开：{", ".join(contract.get("chat_avoid", [])) or "无"}

当前阶段至少要满足：
{format_bullets(completion_requirements)}

额外判断规则：
{checklist_instruction or "1. 只把真正阻塞当前阶段结束的问题当成 blocker。"}

当前阶段对话：
{transcript}

只返回 JSON：
{{"can_finalize": true, "reason": "一句话原因", "blockers": [], "signals": ["列出你认为已经确认好的 2 到 4 个关键信号"]}}
""".strip()


def build_finalize_not_ready_instruction(stage: WorkspaceStage, blockers: List[str]) -> str:
    blocker_lines = format_bullets(blockers[:3]) if blockers else "- 当前还有关键缺口，暂时还不能结束这一阶段。"
    return f"""
额外要求：
用户刚刚是在尝试结束「{stage.title}」阶段，但按当前信息，这一阶段还不能结束。

这次不要整理结论文档，也不要展开下一阶段。
直接说明：为什么现在还不能结束，以及还差哪 1-3 个真正阻塞当前阶段的关键点。

重点缺口：
{blocker_lines}
""".strip()


def build_stage_readiness_instruction(stage: WorkspaceStage, can_finalize: bool, blockers: List[str]) -> str:
    if can_finalize:
        return f"""
当前阶段信息已经足够。
不要继续追问，也不要顺手展开下一阶段内容。
""".strip()

    blocker_lines = format_bullets(blockers[:3]) if blockers else "- 当前还有关键缺口。"
    return f"""
当前阶段还有真实缺口。
只围绕下面这些阻塞点推进，不要重复追问已经说过的点。

阻塞点：
{blocker_lines}
""".strip()


def merge_stage_instructions(*parts: Optional[str]) -> Optional[str]:
    normalized = [part.strip() for part in parts if part and part.strip()]
    if not normalized:
        return None
    return "\n\n".join(normalized)


def build_requirements_chat_prompt(
    workspace: Workspace,
    messages: List[WorkspaceStageMessage],
    memory_context: str,
    extra_instruction: Optional[str] = None,
    stage_skill_context: str = "",
    external_reference_context: str = "",
) -> str:
    transcript = recent_messages_to_prompt_text(messages, limit_messages=8, limit_chars=2200)
    latest = requirements_latest_user_message(messages)
    requirement = (workspace.description or workspace.name or "").strip()
    contract = stage_responsibility_contract(WorkspaceStageKey.REQUIREMENTS)
    auxiliary_context = build_auxiliary_context_block(
        stage_skill_context=stage_skill_context,
        external_reference_context=external_reference_context,
    )
    prompt = f"""
你正在和用户进行“需求确认”阶段的自然对话。

阶段背景：
- 当前阶段关注：{contract.get("chat_focus")}
- 暂不展开：{", ".join(contract.get("chat_avoid", []))}

硬边界：
1. 这一阶段不是“方案设计”，不要问用户要功能模块、页面结构、后台管理、权限规则、技术实现、字段清单或完整业务流程。
2. 只能确认：这是个什么产品、最基本如何使用或呈现、是否有会改变产品方向的硬前提；使用者或参与者关系只有在会明显改变产品形态时才追问。
3. 对用户输入已经能自然推出使用主体的产品，先按上下文做合理默认；只有不同使用主体会改变产品结构、权限关系或核心流程时，才追问使用者或参与者关系。
4. 如果用户给的信息已经足够表达当前理解，就直接给当前理解；最多补 1 个真正影响方向的问题。
5. 允许追问的范围：只问会导致产品形态分叉的关键点。
6. 禁止把后续阶段的问题包装成第一阶段问题，尤其不要让用户提前设计功能模块、页面、后台、权限、技术栈或完整流程。

回复原则：
1. 先理解用户最新消息本身：可能是追问解释、纠正、补充需求、表达不满、确认或要求继续。
2. 如果用户在追问你上一轮的表达，直接解释上一轮没说明白的地方，不要继续抛新问题。
3. 如果用户在纠正你，先承认并按纠正后的信息更新理解。
4. 如果用户在补充需求，再基于上下文推进当前阶段。
5. 不要把阶段流程写到用户面前，不要生成阶段文档，不要展开下一阶段。
6. 回复要自然、具体、短，不要像检查清单。
7. 如果要追问，只问一个第一阶段问题，且这个问题必须符合上面的硬边界。
8. 不要为了补齐字段而追问；先做合理默认，再问真正会改变方向的点。

原始需求：
{requirement}

本轮用户最新补充：
{latest}

跨阶段记忆（只作为上下文，不要原样复述）：
{memory_context}

{auxiliary_context}

到目前为止的对话：
{transcript}
""".strip()
    if extra_instruction and extra_instruction.strip():
        prompt = f"{prompt}\n\n{extra_instruction.strip()}"
    return prompt


def build_generic_stage_chat_prompt(
    workspace: Workspace,
    stage: WorkspaceStage,
    stages: List[WorkspaceStage],
    messages: List[WorkspaceStageMessage],
    memory_context: str,
    extra_instruction: Optional[str] = None,
    stage_skill_context: str = "",
    external_reference_context: str = "",
) -> str:
    transcript = recent_messages_to_prompt_text(messages, limit_messages=8, limit_chars=2400)
    latest = requirements_latest_user_message(messages)
    contract = stage_responsibility_contract(stage.stage_key)
    approved_context: List[str] = []
    for item in stages:
        if item.id == stage.id or item.status != WorkspaceStageStatus.APPROVED or not item.content:
            continue
        approved_context.append(f"[{item.title}]\n{item.content.strip()[:600]}")
    approved_block = "\n\n".join(approved_context) or "暂无"
    requirement = (workspace.description or workspace.name or "").strip()
    auxiliary_context = build_auxiliary_context_block(
        stage_skill_context=stage_skill_context,
        external_reference_context=external_reference_context,
    )
    prompt = f"""
你正在和用户进行「{stage.title}」阶段的自然对话。

阶段背景：
- 当前阶段关注：{contract.get("chat_focus")}
- 暂不展开：{", ".join(contract.get("chat_avoid", []))}

回复原则：
1. 先理解用户最新消息本身：可能是追问解释、纠正、补充需求、表达不满、确认或要求继续。
2. 如果用户在追问你上一轮的表达，直接解释上一轮没说明白的地方，不要继续抛新问题。
3. 如果用户在纠正你，先承认并按纠正后的信息更新当前阶段内容。
4. 如果用户在补充需求，再结合上游信息推进当前阶段。
5. 不要把阶段流程写到用户面前，不要生成阶段文档，不要展开下一阶段。
6. 回复要自然、具体、短，不要像检查清单。

项目原始需求：
{requirement}

当前阶段说明：
{stage.description or stage.title}

本阶段推进重点：
{contract.get("chat_scope")}

已确认的上游阶段信息（这些只作为输入前提，不要原样重写进当前阶段正文）：
{approved_block}

用户本轮最新补充：
{latest}

跨阶段记忆（只作为上下文，不要原样复述）：
{memory_context}

{auxiliary_context}

当前阶段到目前为止的对话：
{transcript}
""".strip()
    if extra_instruction and extra_instruction.strip():
        prompt = f"{prompt}\n\n{extra_instruction.strip()}"
    return prompt


def build_requirements_conclusion_prompt(
    workspace: Workspace,
    messages: List[WorkspaceStageMessage],
    memory_context: str,
    extra_instruction: Optional[str] = None,
    stage_skill_context: str = "",
    external_reference_context: str = "",
) -> str:
    transcript = messages_to_prompt_text(messages)
    requirement = (workspace.description or workspace.name or "").strip()
    contract = stage_responsibility_contract(WorkspaceStageKey.REQUIREMENTS)
    auxiliary_context = build_auxiliary_context_block(
        stage_skill_context=stage_skill_context,
        external_reference_context=external_reference_context,
    )
    prompt = f"""
你现在要把一段需求确认对话整理成正式文档。

要求：
1. 这不是对话回复，而是需求文档。
2. 只整理第一阶段真正确认下来的内容，不要开始写方案设计。
3. 正文围绕当前内容自然组织，不要为了完整度硬凑固定栏目。
4. 重点写清：产品是什么、最基本怎么被使用、主要解决什么事、哪些前提已经定下来了、为什么这些信息已经足够支撑后续阶段。
5. 没有明确说过的内容不要补默认值，也不要写大段“当前未明确”。
6. 不要强行拆角色、补互动、后台、权限等默认设定；只有当前内容明确支撑时才写。
7. 不要复述过程话术，不要抄对话原文。
8. 不要输出待确认项、未完成清单。
9. 第一阶段如果写“结构性前提”，只能写产品层约束，不能写实现细节。
10. 不要把后续阶段才该讨论的问题提前写进来，尤其不要展开：{", ".join(contract.get("chat_avoid", []))}
11. 如果产品方向已经对齐，就明确写出这一阶段已经足以支撑后续阶段。
12. 正文要有清楚段落，段落之间空一行；不要写成一整大段。
13. 不要把下面这些内容提前写进第一阶段文档，除非它已经被明确确认为影响产品骨架的硬约束：
{format_bullets(contract.get("chat_avoid", []))}
14. 不要重复结论，不要出现两个意思几乎一样的结尾小节。

原始需求：
{requirement}

当前已确认的结构化记忆：
{memory_context}

{auxiliary_context}

对话记录：
{transcript}
""".strip()
    if extra_instruction and extra_instruction.strip():
        prompt = f"{prompt}\n\n{extra_instruction.strip()}"
    return prompt


def build_deployment_conclusion_prompt(
    workspace: Workspace,
    approved_stage_block: str,
    artifact_inventory: str,
    memory_context: str,
    extra_instruction: Optional[str] = None,
    stage_skill_context: str = "",
    external_reference_context: str = "",
) -> str:
    requirement = (workspace.description or workspace.name or "").strip()
    auxiliary_context = build_auxiliary_context_block(
        stage_skill_context=stage_skill_context,
        external_reference_context=external_reference_context,
    )
    prompt = f"""
你现在要整理最后一个阶段「最终交付」的正式结论。

这份内容必须由两部分组成，而且都要写：
1. 一段最终总结结论：基于前面几个阶段已经确认的内容，重新总结这次项目最后沉淀下来的产品结论、方案方向、规则口径和开发交接结果。
2. 一份最终交付文档说明：说明每份文档属于哪个阶段、解决什么问题、当前怎么获取。

要求：
1. 最终总结必须是重新归纳，不是把前面阶段内容拼接起来，也不是逐段改写。
2. 不要写成套话式模板，不要出现空泛开场或重复性交代。
3. 总结部分先讲最后定下来的项目方向，再概括方案、规则、开发落地各自沉淀下来的关键结论。
4. 交付说明部分清楚列出已有文档，可以用表格或列表。
5. 可以提到单独下载和整体打包下载，但不要把正文写成下载说明模板。
6. 不要新增新的产品方案、规则方案或技术方案。
7. 不要输出“待确认项”“后续再讨论”这类未结束表达。
8. 用 Markdown 输出，段落清楚，避免一整大段。
9. 标题结构由内容自行决定，不要求固定小节名称。
10. 最后明确说明：当前内容已经可以作为完整交付物交接。

项目原始需求：
{requirement}

前面阶段已确认的结构化阶段快照（这些是摘要输入，不是原始全文；请基于它们重新总结，不要拼接复述）：
{approved_stage_block}

当前已确认的结构化记忆：
{memory_context}

已有文档与附件：
{artifact_inventory}

{auxiliary_context}
""".strip()
    if extra_instruction and extra_instruction.strip():
        prompt = f"{prompt}\n\n{extra_instruction.strip()}"
    return prompt
