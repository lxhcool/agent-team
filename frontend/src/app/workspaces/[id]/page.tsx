"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  ArrowLeft,
  Check,
  CheckCircle2,
  Clock3,
  Code2,
  Eye,
  FileText,
  GitBranch,
  Layers3,
  Loader2,
  MessageSquare,
  Palette,
  PauseCircle,
  RefreshCcw,
  Rocket,
  Send,
  ShieldCheck,
  Sparkles,
  Wand2,
} from "lucide-react";

import { TopNav } from "../../components/topnav";

type StageKey =
  | "requirements"
  | "product"
  | "ui_direction"
  | "prototype"
  | "technical"
  | "development"
  | "acceptance"
  | "deployment";

type JourneyStepId = "direction" | "pages" | "expansion" | "polish" | "delivery";

type StageStatus =
  | "draft"
  | "awaiting_confirmation"
  | "approved"
  | "revision_requested"
  | "skipped";

type Recommendation = {
  summary?: string;
  recommended_action?: string;
  focus?: string[];
  options?: {
    title: string;
    description: string;
    content?: string;
    recommended?: boolean;
  }[];
  selected_option?: string | null;
  artifacts?: {
    type?: string;
    status?: string;
    label?: string;
    artifact_id?: string;
    url?: string;
    mime_type?: string;
    created_at?: string;
  }[];
  source?: string;
  model?: string;
  provider?: string;
  agent_name?: string;
  execution_session_id?: string;
  project_path?: string;
  checkpoint_id?: string;
  task_items?: {
    id: string;
    title: string;
    status: string;
    assigned_agent?: string;
    result_summary?: string;
  }[];
  feedback_used?: string;
};

type ArtifactRecord = NonNullable<Recommendation["artifacts"]>[number];
type ArtifactSection = {
  title: string;
  body: string;
};

const extractApiErrorDetail = (payload: unknown) => {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail.trim()) return detail.trim();
    if (detail && typeof detail === "object") {
      const message = (detail as { message?: unknown }).message;
      if (typeof message === "string" && message.trim()) return message.trim();
      return JSON.stringify(detail);
    }
  }
  return "";
};

const formatApiError = (fallback: string, payload: unknown) => {
  const detail = extractApiErrorDetail(payload);
  if (!detail) return fallback;

  const lowered = detail.toLowerCase();
  if (
    lowered.includes("timeout")
    || lowered.includes("timed out")
    || lowered.includes("readtimeout")
  ) {
    return `${fallback}：这一步生成较慢，已暂时超时，请稍后重试。`;
  }
  if (
    lowered.includes("image_generation")
    || lowered.includes("gpt-image-1")
    || lowered.includes("gpt-image-2")
    || lowered.includes("/v1/images/")
  ) {
    return `${fallback}：当前图片生成方式暂不可用，我会优先继续提供页面预览。`;
  }
  if (lowered.includes("tool choice") || lowered.includes("tool not found")) {
    return `${fallback}：当前生成方式不可用，我已切换到其他可继续的方式。`;
  }
  if (lowered.includes("all providers failed")) {
    return `${fallback}：当前模型服务暂时没有成功返回结果，请稍后再试。`;
  }
  if (detail.length > 120) {
    return fallback;
  }
  return `${fallback}：${detail}`;
};

const formatActionError = (fallback: string, err: unknown) => {
  const message = err instanceof Error ? err.message : String(err || "");
  const lowered = message.toLowerCase();
  if (
    lowered.includes("timeout")
    || lowered.includes("timed out")
    || lowered.includes("readtimeout")
  ) {
    return `${fallback}：这一步处理较慢，请稍后再试。`;
  }
  if (lowered.includes("500") || lowered.includes("internal server error")) {
    return `${fallback}：系统刚才没有顺利完成这一步，请重试。`;
  }
  return fallback;
};

const artifactPreviewKind = (artifact?: ArtifactRecord) =>
  artifact?.mime_type === "text/html" ? "iframe" as const : "image" as const;

const backendBaseUrl = () => {
  const configured = process.env.NEXT_PUBLIC_API_BASE_URL;
  if (configured) return configured.replace(/\/$/, "");
  const port = process.env.NEXT_PUBLIC_BACKEND_PORT || "8200";
  return `http://127.0.0.1:${port}`;
};

const longRunningApiFetch = (path: string, init: RequestInit = {}) => {
  const token = typeof window !== "undefined" ? localStorage.getItem("agent_team_token") : null;
  const headers = new Headers(init.headers || {});
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return fetch(`${backendBaseUrl()}${path}`, { ...init, headers });
};

type WorkspaceStage = {
  id: string;
  workspace_id: string;
  stage_key: StageKey;
  title: string;
  description: string | null;
  status: StageStatus;
  order: number;
  recommendation: Recommendation | null;
  content: string | null;
  user_feedback: string | null;
  approved_at: string | null;
};

type Workspace = {
  id: string;
  name: string;
  description: string | null;
  target_platform: string;
  binding_id: string | null;
  storage_mode: "server" | "local";
  root_path: string | null;
  local_directory_exists?: boolean | null;
  local_manifest_exists?: boolean | null;
  binding_state?: "healthy" | "missing_directory" | "missing_manifest" | "server_managed" | null;
  current_stage: StageKey;
  stage_total: number;
  stage_approved: number;
  stages: WorkspaceStage[];
};

const STAGE_ICON: Record<StageKey, React.ComponentType<{ size?: number; className?: string }>> = {
  requirements: FileText,
  product: Layers3,
  ui_direction: Palette,
  prototype: Eye,
  technical: GitBranch,
  development: Code2,
  acceptance: CheckCircle2,
  deployment: Rocket,
};

const STATUS_LABEL: Record<StageStatus, string> = {
  draft: "未开始",
  awaiting_confirmation: "待确认",
  approved: "已确认",
  revision_requested: "需调整",
  skipped: "已跳过",
};

const STATUS_CLASS: Record<StageStatus, string> = {
  draft: "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400",
  awaiting_confirmation: "bg-sky-50 text-sky-700 dark:bg-sky-500/15 dark:text-sky-300",
  approved: "bg-emerald-50 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-300",
  revision_requested: "bg-amber-50 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300",
  skipped: "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400",
};

const ARTIFACT_STATUS_LABEL: Record<string, string> = {
  ready: "已就绪",
  pending: "生成中",
  failed: "失败",
};

const ARTIFACT_STATUS_CLASS: Record<string, string> = {
  ready: "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300",
  pending: "bg-amber-50 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300",
  failed: "bg-red-50 text-red-600 dark:bg-red-500/15 dark:text-red-300",
};

const AUTO_STOP_STAGES = new Set<StageKey>(["prototype", "acceptance", "deployment"]);

const JOURNEY_ORDER: JourneyStepId[] = ["direction", "pages", "expansion", "polish", "delivery"];

const JOURNEY_META: Record<JourneyStepId, {
  title: string;
  description: string;
  icon: React.ComponentType<{ size?: number; className?: string }>;
}> = {
  direction: {
    title: "明确方向",
    description: "先把一句话需求补成一个清晰的前台方向和默认方案。",
    icon: Sparkles,
  },
  pages: {
    title: "生成核心页面",
    description: "开始生成首页和核心页面，让结果尽快可见。",
    icon: Layers3,
  },
  expansion: {
    title: "准备后续扩展",
    description: "整理后续可扩展能力，但不抢前台成果的优先级。",
    icon: GitBranch,
  },
  polish: {
    title: "完善页面体验",
    description: "把页面做成更完整的前端结果，并修正关键细节。",
    icon: Code2,
  },
  delivery: {
    title: "交付预览版本",
    description: "整理完整可预览结果，让你直接验收和继续调整。",
    icon: Rocket,
  },
};

function toJourneyStepId(stageKey: StageKey): JourneyStepId {
  switch (stageKey) {
    case "requirements":
    case "product":
      return "direction";
    case "ui_direction":
    case "prototype":
      return "pages";
    case "technical":
      return "expansion";
    case "development":
    case "acceptance":
      return "polish";
    case "deployment":
      return "delivery";
  }
}

const STAGE_AGENT_META: Record<StageKey, { name: string; label: string; skills: string[] }> = {
  requirements: {
    name: "requirements-analyst",
    label: "Requirements Analyst",
    skills: ["需求澄清", "MVP 范围控制", "用户表达器"],
  },
  product: {
    name: "product-designer",
    label: "Product Designer",
    skills: ["产品流程设计", "决策呈现器", "用户表达器"],
  },
  ui_direction: {
    name: "ui-ux-designer",
    label: "UI/UX Designer",
    skills: ["界面方向设计", "原型结构构建", "用户表达器"],
  },
  prototype: {
    name: "ui-ux-designer",
    label: "UI/UX Designer",
    skills: ["界面方向设计", "原型结构构建", "用户表达器"],
  },
  technical: {
    name: "technical-architect",
    label: "Technical Architect",
    skills: ["技术方案设计", "上下文摘要器"],
  },
  development: {
    name: "implementation-engineer",
    label: "Implementation Engineer",
    skills: ["实现执行", "上下文摘要器"],
  },
  acceptance: {
    name: "qa-reviewer",
    label: "QA Reviewer",
    skills: ["验收检查", "决策呈现器"],
  },
  deployment: {
    name: "release-operator",
    label: "Release Operator",
    skills: ["发布安全检查", "上下文摘要器"],
  },
};

function formatDate(value: string | null) {
  if (!value) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function withAuthToken(url: string) {
  if (typeof window === "undefined") return url;
  const token = localStorage.getItem("agent_team_token");
  if (!token) return url;
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}token=${encodeURIComponent(token)}`;
}

function aggregateStageStatus(stages: WorkspaceStage[]): StageStatus {
  if (!stages.length) return "draft";
  if (stages.every((stage) => stage.status === "approved")) return "approved";
  if (stages.some((stage) => stage.status === "revision_requested")) return "revision_requested";
  if (stages.some((stage) => stage.status === "awaiting_confirmation")) return "awaiting_confirmation";
  if (stages.every((stage) => stage.status === "skipped")) return "skipped";
  return "draft";
}

function pickRepresentativeStage(stages: WorkspaceStage[], currentStageKey?: StageKey | null) {
  if (!stages.length) return null;
  if (currentStageKey) {
    const current = stages.find((stage) => stage.stage_key === currentStageKey);
    if (current) return current;
  }
  return (
    stages.find((stage) => stage.status === "awaiting_confirmation")
    || stages.find((stage) => stage.status === "revision_requested")
    || stages.find((stage) => Boolean(stage.recommendation?.source))
    || stages[0]
  );
}

function hasVisibleProgress(stage: WorkspaceStage) {
  return Boolean(
    stage.recommendation?.source
    || stage.content
    || stage.user_feedback
    || stage.status !== "draft"
    || (stage.recommendation?.artifacts || []).length
  );
}

function getProgressAction(stage: WorkspaceStage) {
  switch (stage.stage_key) {
    case "requirements":
      if (stage.status === "approved") return "已理解核心需求和目标";
      if (stage.status === "revision_requested") return "正在根据你的意见重整方向";
      return "正在整理需求和默认推荐";
    case "product":
      if (stage.status === "approved") return "已补齐页面结构和基础内容";
      if (stage.status === "revision_requested") return "正在重整页面结构和内容范围";
      return "正在补全页面和内容结构";
    case "ui_direction":
      if (stage.status === "approved") return "已确定页面风格和阅读气质";
      return "正在确定界面风格和信息层级";
    case "prototype": {
      const hasPreview = (stage.recommendation?.artifacts || []).some(
        (artifact) => artifact.status === "ready" && artifact.url
      );
      if (hasPreview) return "已生成可预览页面，正在继续补齐";
      return "正在生成首页和核心页面预览";
    }
    case "technical":
      if (stage.status === "approved") return "已整理好后续扩展边界";
      return "正在整理后续扩展建议";
    case "development":
      if (stage.status === "approved") return "已生成一版可运行前端";
      return "正在把页面做成可运行前端";
    case "acceptance":
      if (stage.status === "approved") return "已完成当前一轮页面检查";
      return "正在检查预览并修正细节";
    case "deployment":
      if (stage.status === "approved") return "已整理好最终交付预览";
      return "正在整理完整可预览结果";
  }
}

type JourneyStep = {
  id: JourneyStepId;
  title: string;
  description: string;
  icon: React.ComponentType<{ size?: number; className?: string }>;
  stages: WorkspaceStage[];
  representativeStage: WorkspaceStage;
  actionableStage: WorkspaceStage;
  status: StageStatus;
  currentAction: string;
};

function shouldShowJourneyStep(id: JourneyStepId, stages: WorkspaceStage[], currentStageKey: StageKey) {
  const related = stages.filter((stage) => toJourneyStepId(stage.stage_key) === id);
  if (!related.length) return false;

  if (id === "direction" || id === "pages") return true;
  if (id === "expansion") {
    return related.some((stage) => stage.stage_key === currentStageKey || hasVisibleProgress(stage));
  }
  if (id === "polish" || id === "delivery") {
    return related.some((stage) => stage.stage_key === currentStageKey || hasVisibleProgress(stage));
  }
  return true;
}

function buildJourneySteps(stages: WorkspaceStage[], currentStageKey: StageKey): JourneyStep[] {
  return JOURNEY_ORDER.flatMap((id) => {
    if (!shouldShowJourneyStep(id, stages, currentStageKey)) {
      return [];
    }

    const relatedStages = stages.filter((stage) => toJourneyStepId(stage.stage_key) === id);
    const representativeStage =
      pickRepresentativeStage(relatedStages, relatedStages.some((stage) => stage.stage_key === currentStageKey) ? currentStageKey : null)
      || relatedStages[0];
    const actionableStage =
      relatedStages.find((stage) => stage.stage_key === currentStageKey)
      || relatedStages.find((stage) => stage.status === "awaiting_confirmation")
      || relatedStages.find((stage) => stage.status === "revision_requested")
      || representativeStage;

    return [{
      id,
      title: JOURNEY_META[id].title,
      description: JOURNEY_META[id].description,
      icon: JOURNEY_META[id].icon,
      stages: relatedStages,
      representativeStage,
      actionableStage,
      status: aggregateStageStatus(relatedStages),
      currentAction: getProgressAction(actionableStage),
    }];
  });
}

function getJourneyActionCopy(stepId: JourneyStepId | null) {
  switch (stepId) {
    case "direction":
      return {
        generate: "刷新推荐方案",
        approve: "确认当前方向",
        revisionTitle: "调整方向",
        revisionPlaceholder: "补充你真正想要的东西，例如：更像独立开发者博客、首页先不要订阅、强调文章阅读感。",
      };
    case "pages":
      return {
        generate: "继续生成页面",
        approve: "确认当前页面方向",
        revisionTitle: "调整页面",
        revisionPlaceholder: "告诉系统你想改哪些页面或体验，例如：去掉关于页、首页更有杂志感、阅读区更长。",
      };
    case "expansion":
      return {
        generate: "整理扩展建议",
        approve: "确认扩展建议",
        revisionTitle: "调整扩展建议",
        revisionPlaceholder: "告诉系统哪些后续能力先不要展开，例如：先不要后台、上传功能后置、只保留前台页面。",
      };
    case "polish":
      return {
        generate: "继续完善页面",
        approve: "确认当前版本",
        revisionTitle: "继续修改",
        revisionPlaceholder: "描述你还要继续改什么，例如：继续补交互、调整页面细节、继续完善移动端。",
      };
    case "delivery":
      return {
        generate: "刷新完整预览",
        approve: "确认交付版本",
        revisionTitle: "继续调整交付结果",
        revisionPlaceholder: "描述交付前还要改什么，例如：补一个页面、修正间距、优化预览打开后的体验。",
      };
    default:
      return {
        generate: "生成推荐",
        approve: "确认通过",
        revisionTitle: "需要调整",
        revisionPlaceholder: "告诉系统你希望怎么改。",
      };
  }
}

function getArtifactStatusLabel(status?: string | null) {
  if (!status) return "待生成";
  return ARTIFACT_STATUS_LABEL[status] || status;
}

function getArtifactStatusClass(status?: string | null) {
  if (!status) return "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400";
  return ARTIFACT_STATUS_CLASS[status] || "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400";
}

function getArtifactHeadline(stepId: JourneyStepId | null) {
  switch (stepId) {
    case "direction":
      return "这里先展示系统已经补齐的推荐方向，让右侧从一开始就有东西可看。";
    case "pages":
      return "这里优先展示首页和核心页面预览，而不是只给一段抽象说明。";
    case "expansion":
      return "这里展示后续扩展建议，但不会抢走前台成果的主位置。";
    case "polish":
      return "这里应该看到更完整的前端页面和最新修正版。";
    case "delivery":
      return "这里集中展示当前最完整的可预览结果，方便直接验收。";
    default:
      return "这里展示当前阶段的核心产物。";
  }
}

function getOptionImpactCopy(stepId: JourneyStepId | null, optionTitle: string | null) {
  if (!optionTitle) {
    return "当前还没有锁定方向，生成结果可能继续变化。";
  }

  switch (stepId) {
    case "direction":
      return `已选「${optionTitle}」，后续页面结构和默认内容会沿用这个方向继续展开。`;
    case "pages":
      return `已选「${optionTitle}」，后续首页和核心页面会优先按这个方向生成。`;
    case "expansion":
      return `已选「${optionTitle}」，后续扩展建议会按这个边界继续收敛。`;
    case "polish":
      return `已选「${optionTitle}」，后续页面优化会继续围绕这个版本细化。`;
    case "delivery":
      return `已选「${optionTitle}」，后续完整预览会继续沿用这个方向整理。`;
    default:
      return `已选「${optionTitle}」，后续生成会优先沿用这个方向。`;
  }
}

function parseArtifactContent(content: string) {
  const lines = content
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);

  const lead: string[] = [];
  const sections: ArtifactSection[] = [];
  const bullets: string[] = [];

  const pushLead = (value: string) => {
    if (value && lead.length < 2) {
      lead.push(value);
    }
  };

  for (const line of lines) {
    const normalized = line.replace(/^(\d+[\.\、]\s*|[-*•]\s*)/, "").trim();
    const numbered = /^(\d+[\.\、]\s*|[-*•]\s*)/.test(line);
    const colonIndex = normalized.indexOf("：");

    if (colonIndex > 0 && colonIndex <= 18) {
      const title = normalized.slice(0, colonIndex).trim();
      const body = normalized.slice(colonIndex + 1).trim();
      if (title && body) {
        sections.push({ title, body });
        continue;
      }
    }

    if (numbered) {
      bullets.push(normalized);
      continue;
    }

    if (normalized.length <= 32 && !lead.length) {
      pushLead(normalized);
      continue;
    }

    if (lead.length < 2) {
      pushLead(normalized);
      continue;
    }

    bullets.push(normalized);
  }

  if (!lead.length && bullets.length) {
    pushLead(bullets.shift() as string);
  }

  return {
    lead,
    sections,
    bullets,
  };
}

export default function WorkspaceDetailPage() {
  const params = useParams<{ id: string }>();
  const workspaceId = params.id;
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [selectedKey, setSelectedKey] = useState<StageKey | null>(null);
  const [selectedOptionTitle, setSelectedOptionTitle] = useState<string | null>(null);
  const [feedback, setFeedback] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [selectingOption, setSelectingOption] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [generatingPrototype, setGeneratingPrototype] = useState(false);
  const [generatingDesigns, setGeneratingDesigns] = useState(false);
  const [autoFollowAI, setAutoFollowAI] = useState(false);
  const [autoStatus, setAutoStatus] = useState("");
  const [error, setError] = useState("");
  const [importNotice, setImportNotice] = useState("");
  const [isDesktop, setIsDesktop] = useState(false);
  const [rebinding, setRebinding] = useState(false);
  const [rebindPath, setRebindPath] = useState("");

  const loadWorkspace = async () => {
    setLoading(true);
    setError("");
    try {
      const res = await fetch(`/api/workspaces/${workspaceId}`);
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "获取工作区失败");
      }
      const data = await res.json();
      setWorkspace(data);
      setSelectedKey((current) => current || data.current_stage);
    } catch (err: any) {
      setError(err.message || "获取工作区失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadWorkspace();
  }, [workspaceId]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const notice = window.sessionStorage.getItem(`workspace_import_notice_${workspaceId}`);
    if (!notice) return;
    setImportNotice(notice);
    window.sessionStorage.removeItem(`workspace_import_notice_${workspaceId}`);
  }, [workspaceId]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    setIsDesktop(Boolean(window.teamAgentDesktop?.isDesktop));
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const stored = window.localStorage.getItem(`workspace_auto_follow_ai_${workspaceId}`);
    setAutoFollowAI(stored === "true");
  }, [workspaceId]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(`workspace_auto_follow_ai_${workspaceId}`, autoFollowAI ? "true" : "false");
  }, [autoFollowAI, workspaceId]);

  const selectedStage = useMemo(() => {
    if (!workspace?.stages?.length) return null;
    return workspace.stages.find((stage) => stage.stage_key === selectedKey) || workspace.stages[0];
  }, [selectedKey, workspace]);

  const currentStage = useMemo(() => {
    if (!workspace?.stages?.length) return null;
    return workspace.stages.find((stage) => stage.stage_key === workspace.current_stage) || workspace.stages[0];
  }, [workspace]);
  const journeySteps = useMemo(() => {
    if (!workspace?.stages?.length) return [] as JourneyStep[];
    return buildJourneySteps(workspace.stages, workspace.current_stage);
  }, [workspace]);
  const selectedJourneyStepId = selectedStage ? toJourneyStepId(selectedStage.stage_key) : null;
  const selectedJourneyStep = journeySteps.find((step) => step.id === selectedJourneyStepId) || journeySteps[0] || null;
  const currentJourneyStep = currentStage
    ? journeySteps.find((step) => step.stages.some((stage) => stage.stage_key === currentStage.stage_key)) || journeySteps[0] || null
    : journeySteps[0] || null;
  const selectedJourneyStages = selectedJourneyStep?.stages || [];
  const currentStageBlocksAuto = currentStage ? AUTO_STOP_STAGES.has(currentStage.stage_key) : false;

  useEffect(() => {
    setFeedback(selectedStage?.user_feedback || "");
  }, [selectedStage?.id, selectedStage?.user_feedback]);

  useEffect(() => {
    setRebindPath(workspace?.root_path || "");
  }, [workspace?.root_path]);

  useEffect(() => {
    const stageOptions = selectedStage?.recommendation?.options || [];
    if (!stageOptions.length) {
      setSelectedOptionTitle(null);
      return;
    }
    setSelectedOptionTitle((current) => {
      const persisted = selectedStage?.recommendation?.selected_option;
      if (persisted && stageOptions.some((option) => option.title === persisted)) return persisted;
      if (current && stageOptions.some((option) => option.title === current)) return current;
      return stageOptions.find((option) => option.recommended)?.title || stageOptions[0].title;
    });
  }, [selectedStage?.id, selectedStage?.recommendation?.options, selectedStage?.recommendation?.selected_option]);

  const selectStageOption = async (stage: WorkspaceStage, optionTitle: string) => {
    if (!stage.recommendation || selectingOption) return;
    const nextOption = (stage.recommendation.options || []).find((option) => option.title === optionTitle);
    if (!nextOption) return;

    const nextRecommendation: Recommendation = {
      ...stage.recommendation,
      selected_option: optionTitle,
    };

    setSelectingOption(true);
    setSelectedOptionTitle(optionTitle);
    setWorkspace((current) => {
      if (!current) return current;
      return {
        ...current,
        stages: current.stages.map((item) => item.id === stage.id
          ? {
              ...item,
              recommendation: nextRecommendation,
              content: nextOption.content || item.content,
            }
          : item),
      };
    });

    try {
      const res = await fetch(`/api/workspaces/${workspaceId}/stages/${stage.stage_key}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          recommendation: nextRecommendation,
          content: nextOption.content || stage.content,
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "切换方案失败");
      }
      const updatedStage = await res.json();
      setWorkspace((current) => {
        if (!current) return current;
        return {
          ...current,
          stages: current.stages.map((item) => item.id === updatedStage.id ? updatedStage : item),
        };
      });
    } catch (err: any) {
      setError(formatActionError("切换方向失败", err));
      await loadWorkspace();
    } finally {
      setSelectingOption(false);
    }
  };

  const approveStage = async (stage: WorkspaceStage) => {
    if (saving) return;
    setSaving(true);
    setError("");
    try {
      const res = await fetch(
        `/api/workspaces/${workspaceId}/stages/${stage.stage_key}/approve`,
        { method: "POST" }
      );
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "确认失败");
      }
      const data = await res.json();
      setWorkspace(data);
      setSelectedKey(data.current_stage);
    } catch (err: any) {
      setError(formatActionError("确认当前结果失败", err));
    } finally {
      setSaving(false);
    }
  };

  const approveSelectedStage = async () => {
    if (!selectedStage) return;
    await approveStage(selectedStage);
  };

  const requestRevision = async (event: FormEvent) => {
    event.preventDefault();
    if (!selectedStage || !feedback.trim() || saving) return;
    setSaving(true);
    setError("");
    try {
      const res = await fetch(
        `/api/workspaces/${workspaceId}/stages/${selectedStage.stage_key}/request-revision`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ feedback: feedback.trim() }),
        }
      );
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "提交修改意见失败");
      }
      setAutoStatus(`已提交「${selectedStage.title}」的修改意见，正在基于你的反馈重新生成...`);
      const updatedStageRes = await fetch(
        `/api/workspaces/${workspaceId}/stages/${selectedStage.stage_key}/generate`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ instruction: feedback.trim() }),
        }
      );
      if (!updatedStageRes.ok) {
        const data = await updatedStageRes.json().catch(() => ({}));
        throw new Error(data.detail || "提交意见后重新生成失败");
      }
      const updatedStage = await updatedStageRes.json();
      setWorkspace((current) => {
        if (!current) return current;
        return {
          ...current,
          stages: current.stages.map((item) => item.id === updatedStage.id ? updatedStage : item),
        };
      });
      setSelectedKey(updatedStage.stage_key);
    } catch (err: any) {
      setError(formatActionError("提交修改意见失败", err));
    } finally {
      setSaving(false);
    }
  };

  const generateStage = async (stage: WorkspaceStage) => {
    if (generating) return;
    setGenerating(true);
    setError("");
    try {
      const res = await fetch(
        `/api/workspaces/${workspaceId}/stages/${stage.stage_key}/generate`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ instruction: feedback.trim() || stage.user_feedback || null }),
        }
      );
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "生成推荐失败");
      }
      const updatedStage = await res.json();
      setWorkspace((current) => {
        if (!current) return current;
        return {
          ...current,
          stages: current.stages.map((item) => item.id === updatedStage.id ? updatedStage : item),
        };
      });
      setSelectedKey(updatedStage.stage_key);
    } catch (err: any) {
      setError(formatActionError("刷新推荐失败", err));
    } finally {
      setGenerating(false);
    }
  };

  const generateSelectedStage = async () => {
    if (!selectedStage) return;
    await generateStage(selectedStage);
  };

  useEffect(() => {
    if (!autoFollowAI || !workspace || !currentStage) return;
    if (loading || generating || saving || selectingOption || generatingPrototype || generatingDesigns) return;

    if (selectedKey !== currentStage.stage_key) {
      setSelectedKey(currentStage.stage_key);
    }

    if (AUTO_STOP_STAGES.has(currentStage.stage_key)) {
      setAutoFollowAI(false);
      setAutoStatus(`已在「${currentStage.title}」停止自动推进，请你接管确认。`);
      return;
    }

    const recommendation = currentStage.recommendation;
    const hasGenerated = Boolean(recommendation?.source);
    const options = recommendation?.options || [];
    const selectedOption = recommendation?.selected_option || null;
    const recommendedOption =
      options.find((option) => option.recommended)?.title || options[0]?.title || null;

    if (!hasGenerated) {
      setAutoStatus(`正在为「${currentStage.title}」生成推荐方案...`);
      void generateStage(currentStage);
      return;
    }

    if (recommendedOption && selectedOption !== recommendedOption) {
      setAutoStatus(`正在为「${currentStage.title}」选中 AI 推荐方案...`);
      void selectStageOption(currentStage, recommendedOption);
      return;
    }

    if (currentStage.status !== "approved") {
      setAutoStatus(`正在确认「${currentStage.title}」并推进到下一阶段...`);
      void approveStage(currentStage);
      return;
    }

    setAutoStatus(`自动推进已处理到「${currentStage.title}」。`);
  }, [
    autoFollowAI,
    currentStage,
    generating,
    generatingDesigns,
    generatingPrototype,
    loading,
    saving,
    selectingOption,
    selectedKey,
    workspace,
  ]);

  useEffect(() => {
    if (!workspace || !currentStage) return;
    if (autoFollowAI) return;
    if (loading || generating || saving || selectingOption || generatingPrototype || generatingDesigns) return;
    if (currentStage.status !== "awaiting_confirmation") return;
    if (currentStage.recommendation?.source) return;

    setAutoStatus(`已进入「${currentStage.title}」，正在自动生成推荐方案...`);
    void generateStage(currentStage);
  }, [
    autoFollowAI,
    currentStage,
    generating,
    generatingDesigns,
    generatingPrototype,
    loading,
    saving,
    selectingOption,
    workspace,
  ]);

  const generatePrototype = async () => {
    if (generatingPrototype) return;
    setGeneratingPrototype(true);
    setError("");
    try {
      const res = await longRunningApiFetch(`/api/workspaces/${workspaceId}/prototype`, { method: "POST" });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(formatApiError("生成 HTML 原型失败", data));
      }
      const stage = await res.json();
      setWorkspace((current) => {
        if (!current) return current;
        return {
          ...current,
          current_stage: stage.stage_key,
          stages: current.stages.map((item) => item.id === stage.id ? stage : item),
        };
      });
      setSelectedKey(stage.stage_key);
    } catch (err: any) {
      setError(formatActionError("生成页面预览失败", err));
    } finally {
      setGeneratingPrototype(false);
    }
  };

  const generateDesigns = async () => {
    if (generatingDesigns) return;
    setGeneratingDesigns(true);
    setError("");
    try {
      const res = await longRunningApiFetch(`/api/workspaces/${workspaceId}/designs`, { method: "POST" });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(formatApiError("生成设计稿失败", data));
      }
      const stage = await res.json();
      setWorkspace((current) => {
        if (!current) return current;
        return {
          ...current,
          current_stage: stage.stage_key,
          stages: current.stages.map((item) => item.id === stage.id ? stage : item),
        };
      });
      setSelectedKey(stage.stage_key);
    } catch (err: any) {
      setError(formatActionError("生成页面设计失败", err));
    } finally {
      setGeneratingDesigns(false);
    }
  };

  const chooseRebindDirectory = async () => {
    try {
      const chosen = await window.teamAgentDesktop?.workspace?.chooseDirectory?.();
      if (chosen?.path) {
        setRebindPath(chosen.path);
      }
    } catch (err: any) {
      setError(err?.message || "选择目录失败");
    }
  };

  const rebindDirectory = async () => {
    if (!workspace || !rebindPath.trim() || rebinding) return;
    setRebinding(true);
    setError("");
    try {
      const res = await fetch(`/api/workspaces/${workspace.id}/rebind-local`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ root_path: rebindPath.trim() }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "重新绑定目录失败");
      }
      const data = await res.json();
      setWorkspace(data);
      setImportNotice("已重新绑定本地目录，当前工作区继续沿用原有阶段记录和 binding_id。");
    } catch (err: any) {
      setError(formatActionError("重新绑定目录失败", err));
    } finally {
      setRebinding(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-50 dark:bg-slate-950">
        <TopNav />
        <main className="flex h-screen items-center justify-center pt-14 text-slate-400">
          <Loader2 className="animate-spin" />
        </main>
      </div>
    );
  }

  if (!workspace || !selectedStage) {
    return (
      <div className="min-h-screen bg-slate-50 dark:bg-slate-950">
        <TopNav />
        <main className="mx-auto max-w-4xl px-6 pt-28">
          <Link href="/workspaces" className="inline-flex items-center gap-2 text-sm text-slate-500 hover:text-sky-600">
            <ArrowLeft size={16} />
            返回工作区
          </Link>
          <div className="mt-8 rounded-2xl border border-red-200 bg-red-50 p-5 text-red-600 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-300">
            {error || "工作区不存在"}
          </div>
        </main>
      </div>
    );
  }

  const journeyApproved = journeySteps.filter((step) => step.status === "approved").length;
  const journeyProgress = journeySteps.length ? Math.round((journeyApproved / journeySteps.length) * 100) : 0;
  const activeStage = selectedJourneyStep?.actionableStage || selectedStage;
  const StageIcon = STAGE_ICON[activeStage.stage_key];
  const focus = selectedStage.recommendation?.focus || [];
  const options = selectedStage.recommendation?.options || [];
  const activeOption = options.find((option) => option.title === selectedOptionTitle) || null;
  const activeContent = activeOption?.content || selectedStage.content;
  const hasGeneratedRecommendation = Boolean(selectedStage.recommendation?.source);
  const artifacts = selectedJourneyStages.flatMap((stage) => stage.recommendation?.artifacts || []);
  const stageAgentMeta = STAGE_AGENT_META[activeStage.stage_key];
  const stageTasks = selectedStage.recommendation?.task_items || [];
  const actionCopy = getJourneyActionCopy(selectedJourneyStep?.id || null);
  const prototypeArtifact = artifacts.find((artifact) =>
    artifact.type === "prototype_html" && artifact.status === "ready" && artifact.url
  );
  const developmentPreviewArtifact = artifacts.find((artifact) =>
    artifact.type === "development_preview" && artifact.status === "ready" && artifact.url
  );
  const developmentReportArtifact = artifacts.find((artifact) =>
    artifact.type === "development_report" && artifact.status === "ready" && artifact.url
  );
  const acceptanceReportArtifact = artifacts.find((artifact) =>
    artifact.type === "acceptance_report" && artifact.status === "ready" && artifact.url
  );
  const desktopDesign = artifacts.find((artifact) =>
    artifact.type === "desktop_design" && artifact.status === "ready" && artifact.url
  );
  const mobileDesign = artifacts.find((artifact) =>
    artifact.type === "mobile_design" && artifact.status === "ready" && artifact.url
  );
  const prototypeUrl = prototypeArtifact?.url ? withAuthToken(prototypeArtifact.url) : "";
  const developmentPreviewUrl = developmentPreviewArtifact?.url ? withAuthToken(developmentPreviewArtifact.url) : "";
  const developmentReportUrl = developmentReportArtifact?.url ? withAuthToken(developmentReportArtifact.url) : "";
  const acceptanceReportUrl = acceptanceReportArtifact?.url ? withAuthToken(acceptanceReportArtifact.url) : "";
  const desktopDesignUrl = desktopDesign?.url ? withAuthToken(desktopDesign.url) : "";
  const mobileDesignUrl = mobileDesign?.url ? withAuthToken(mobileDesign.url) : "";
  const artifactHeadline = getArtifactHeadline(selectedJourneyStep?.id || null);
  const optionImpactCopy = getOptionImpactCopy(selectedJourneyStep?.id || null, selectedOptionTitle);
  const stageRuntimeLabel = autoFollowAI
    ? "系统将自动生成推荐、选中推荐方向并推进。"
    : "当前为手动接管模式，你可以生成、切换方向、确认或提修改意见。";
  const primaryArtifact =
    selectedJourneyStep?.id === "pages"
      ? (prototypeUrl
          ? { kind: "iframe" as const, title: "页面预览", subtitle: "当前最新的可预览页面，用来确认结构、内容层级和基础交互。", url: prototypeUrl }
          : desktopDesignUrl
            ? { kind: artifactPreviewKind(desktopDesign), title: "首页预览", subtitle: "当前已生成的主页面预览，用来确认页面风格和信息层级。", url: desktopDesignUrl }
            : null)
      : selectedJourneyStep?.id === "polish" || selectedJourneyStep?.id === "delivery"
        ? (developmentPreviewUrl
            ? { kind: "iframe" as const, title: selectedJourneyStep?.id === "delivery" ? "完整预览" : "最新页面预览", subtitle: "当前阶段最值得直接查看的前端成果。", url: developmentPreviewUrl }
            : null)
        : null;
  const relatedArtifactLinks = [
    desktopDesignUrl ? { label: "桌面页面预览", url: desktopDesignUrl } : null,
    mobileDesignUrl ? { label: "移动端页面预览", url: mobileDesignUrl } : null,
    prototypeUrl && primaryArtifact?.url !== prototypeUrl ? { label: "页面预览", url: prototypeUrl } : null,
    developmentPreviewUrl && primaryArtifact?.url !== developmentPreviewUrl ? { label: "完整页面预览", url: developmentPreviewUrl } : null,
    developmentReportUrl ? { label: "开发记录", url: developmentReportUrl } : null,
    acceptanceReportUrl ? { label: "检查记录", url: acceptanceReportUrl } : null,
  ].filter(Boolean) as { label: string; url: string }[];
  const artifactSummary = activeContent || "这一部分还在生成中。稍后这里会展示推荐方案、页面预览或完整结果摘要。";
  const artifactList = artifacts.filter((artifact) => artifact.type || artifact.label);
  const parsedArtifact = parseArtifactContent(artifactSummary);
  const recommendationSourceLabel = selectedStage.recommendation?.source === "fallback"
    ? "模板兜底"
    : "模型/系统生成";

  return (
    <div className="min-h-screen bg-[radial-gradient(circle_at_top_left,_rgba(125,211,252,0.18),_transparent_28%),linear-gradient(180deg,_#f8fbff_0%,_#eef4f8_100%)] dark:bg-slate-950">
      <TopNav />
      <main className="mx-auto max-w-7xl px-6 pb-16 pt-24">
        <Link
          href="/workspaces"
          className="mb-6 inline-flex items-center gap-2 text-sm font-medium text-slate-500 transition hover:text-sky-600"
        >
          <ArrowLeft size={16} />
          返回工作区
        </Link>

        <section className="mb-6 overflow-hidden rounded-[28px] border border-slate-200/80 bg-white/95 p-6 shadow-[0_24px_70px_rgba(15,23,42,0.08)] backdrop-blur dark:border-slate-800 dark:bg-slate-900/95">
          <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
            <div>
              <div className="mb-2 inline-flex items-center gap-2 rounded-full bg-sky-50 px-3 py-1 text-xs font-medium text-sky-700 dark:bg-sky-500/15 dark:text-sky-300">
                <Sparkles size={14} />
                创建工作台
              </div>
              <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-slate-100">
                {workspace.name}
              </h1>
              <p className="mt-2 max-w-3xl text-sm text-slate-500 dark:text-slate-400">
                {workspace.description || "这个工作区还没有补充详细需求。"}
              </p>
              <div className="mt-4 flex flex-wrap gap-2">
                <span className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs font-medium text-slate-600 dark:border-slate-700 dark:bg-slate-950/60 dark:text-slate-300">
                  当前进展：{selectedJourneyStep?.title || selectedStage.title}
                </span>
                <span className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs font-medium text-slate-600 dark:border-slate-700 dark:bg-slate-950/60 dark:text-slate-300">
                  {autoFollowAI ? "AI 自动推进中" : "人工接管中"}
                </span>
                <span className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs font-medium text-slate-600 dark:border-slate-700 dark:bg-slate-950/60 dark:text-slate-300">
                  {workspace.storage_mode === "local" ? "本地目录模式" : "服务器目录模式"}
                </span>
              </div>
            </div>
            <div className="min-w-[220px]">
              <div className="mb-2 flex items-center justify-between text-xs text-slate-500 dark:text-slate-400">
                <span>当前进展</span>
                <span>{journeyApproved}/{journeySteps.length || 1}</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                <div className="h-full rounded-full bg-sky-500" style={{ width: `${journeyProgress}%` }} />
              </div>
              <div className="mt-3 rounded-full bg-slate-100 px-3 py-1 text-center text-xs font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                {workspace.target_platform}
              </div>
              <div className="mt-2 rounded-2xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-500 dark:border-slate-800 dark:bg-slate-950/60 dark:text-slate-400">
                <div>{workspace.storage_mode === "local" ? "本地目录模式" : "服务器目录模式"}</div>
                {workspace.binding_id && <div className="mt-1 break-all">Binding ID：{workspace.binding_id}</div>}
                {workspace.root_path && <div className="mt-1 break-all">{workspace.root_path}</div>}
                {workspace.binding_state === "healthy" && <div className="mt-1 text-emerald-600 dark:text-emerald-300">目录绑定正常</div>}
                {workspace.binding_state === "missing_directory" && <div className="mt-1 text-amber-700 dark:text-amber-300">本地目录不存在，请重新绑定目录或重新导入。</div>}
                {workspace.binding_state === "missing_manifest" && <div className="mt-1 text-amber-700 dark:text-amber-300">本地目录存在，但 .agent-workspace.json 丢失。</div>}
                {(workspace.binding_state === "missing_directory" || workspace.binding_state === "missing_manifest") && (
                  <div className="mt-3 space-y-2">
                    <input
                      value={rebindPath}
                      onChange={(event) => setRebindPath(event.target.value)}
                      placeholder="输入新的本地目录绝对路径"
                      className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs text-slate-900 outline-none transition focus:border-sky-400 focus:ring-2 focus:ring-sky-100 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:focus:ring-sky-500/20"
                    />
                    <div className="flex flex-col gap-2 sm:flex-row">
                      {isDesktop && (
                        <button
                          type="button"
                          onClick={chooseRebindDirectory}
                          className="inline-flex items-center justify-center rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-700 transition hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300 dark:hover:bg-slate-800"
                        >
                          选择目录
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={rebindDirectory}
                        disabled={!rebindPath.trim() || rebinding}
                        className="inline-flex items-center justify-center rounded-xl bg-sky-600 px-3 py-2 text-xs font-medium text-white transition hover:bg-sky-500 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {rebinding ? <Loader2 size={14} className="animate-spin" /> : "重新绑定目录"}
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        </section>

        {error && (
          <div className="mb-6 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-600 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-300">
            {error}
          </div>
        )}
        {importNotice && (
          <div className="mb-6 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-300">
            {importNotice}
          </div>
        )}

        <div className="grid gap-6 lg:grid-cols-[320px_1fr]">
          <aside className="self-start rounded-[28px] border border-slate-200/80 bg-white/95 p-3 shadow-[0_18px_50px_rgba(15,23,42,0.06)] backdrop-blur dark:border-slate-800 dark:bg-slate-900/95 lg:sticky lg:top-24">
            <div className="px-3 py-2 text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">
              进展
            </div>
            <div className="space-y-1">
              {journeySteps.map((step) => {
                const active = step.id === selectedJourneyStep?.id;
                const Icon = step.icon;
                return (
                  <button
                    key={step.id}
                    onClick={() => setSelectedKey(step.actionableStage.stage_key)}
                    className={`w-full rounded-2xl border px-3 py-3 text-left transition ${
                      active
                        ? "border-sky-200 bg-sky-50 text-sky-700 shadow-sm dark:border-sky-500/30 dark:bg-sky-500/15 dark:text-sky-300"
                        : "border-transparent text-slate-600 hover:border-slate-200 hover:bg-slate-50 dark:text-slate-300 dark:hover:border-slate-800 dark:hover:bg-slate-800/70"
                    }`}
                  >
                    <div className="flex items-start gap-3">
                      <span className={`flex size-9 items-center justify-center rounded-xl ${
                        active ? "bg-white dark:bg-slate-900" : "bg-slate-100 dark:bg-slate-800"
                      }`}>
                        <Icon size={16} />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center justify-between gap-2">
                          <span className="block text-sm font-medium">{step.title}</span>
                          {step.status === "approved" ? (
                            <CheckCircle2 size={16} className="shrink-0 text-emerald-500" />
                          ) : step.status === "awaiting_confirmation" ? (
                            <Clock3 size={16} className="shrink-0 text-sky-500" />
                          ) : step.status === "revision_requested" ? (
                            <RefreshCcw size={16} className="shrink-0 text-amber-500" />
                          ) : null}
                        </span>
                        <span className="mt-1 block text-xs opacity-70">{step.currentAction}</span>
                      </span>
                    </div>
                  </button>
                );
              })}
            </div>
          </aside>

          <section className="min-w-0 rounded-[28px] border border-slate-200/80 bg-white/95 p-6 shadow-[0_24px_70px_rgba(15,23,42,0.08)] backdrop-blur dark:border-slate-800 dark:bg-slate-900/95">
            <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
              <div className="flex items-start gap-3">
                <span className="flex size-11 items-center justify-center rounded-xl bg-sky-50 text-sky-700 dark:bg-sky-500/15 dark:text-sky-300">
                  <StageIcon size={20} />
                </span>
                <div>
                  <div className="mb-2 flex flex-wrap items-center gap-2">
                    <h2 className="text-xl font-semibold text-slate-900 dark:text-slate-100">
                      {selectedJourneyStep?.title || selectedStage.title}
                    </h2>
                    <span className={`rounded-full px-2.5 py-1 text-xs font-medium ${STATUS_CLASS[selectedStage.status]}`}>
                      {STATUS_LABEL[selectedStage.status]}
                    </span>
                  </div>
                  <p className="text-sm text-slate-500 dark:text-slate-400">
                    {selectedJourneyStep?.description || selectedStage.description}
                  </p>
                </div>
              </div>

              <div className="flex flex-col gap-2 sm:flex-row">
                {activeStage.stage_key === "prototype" && (
                  <>
                    <button
                      onClick={generateDesigns}
                      disabled={generatingDesigns}
                      className="inline-flex items-center justify-center gap-2 rounded-xl border border-emerald-200 bg-white px-4 py-2.5 text-sm font-medium text-emerald-700 shadow-sm transition hover:bg-emerald-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-emerald-500/30 dark:bg-slate-900 dark:text-emerald-300 dark:hover:bg-emerald-500/10"
                    >
                      {generatingDesigns ? <Loader2 size={16} className="animate-spin" /> : <Palette size={16} />}
                      生成页面设计
                    </button>
                    <button
                      onClick={generatePrototype}
                      disabled={generatingPrototype}
                      className="inline-flex items-center justify-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 shadow-sm transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300 dark:hover:bg-slate-800"
                    >
                      {generatingPrototype ? <Loader2 size={16} className="animate-spin" /> : <Eye size={16} />}
                      生成页面预览
                    </button>
                  </>
                )}
                <button
                  onClick={generateSelectedStage}
                  disabled={generating}
                  className="inline-flex items-center justify-center gap-2 rounded-xl border border-sky-200 bg-white px-4 py-2.5 text-sm font-medium text-sky-700 shadow-sm transition hover:bg-sky-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-sky-500/30 dark:bg-slate-900 dark:text-sky-300 dark:hover:bg-sky-500/10"
                >
                  {generating ? <Loader2 size={16} className="animate-spin" /> : <Wand2 size={16} />}
                  {actionCopy.generate}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setAutoFollowAI((current) => {
                      if (!current && currentStageBlocksAuto && currentStage) {
                        setAutoStatus(`当前处于「${currentStage.title}」，这个阶段需要你人工确认，不能开启自动推进。`);
                        return current;
                      }
                      const next = !current;
                      setAutoStatus(next ? "自动推进已开启，系统会默认按 AI 推荐继续。" : "已关闭自动推进，恢复手动接管。");
                      return next;
                    });
                  }}
                  className={`inline-flex items-center justify-center gap-2 rounded-xl border px-4 py-2.5 text-sm font-medium shadow-sm transition ${
                    autoFollowAI
                      ? "border-emerald-200 bg-emerald-50 text-emerald-700 hover:bg-emerald-100 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300"
                      : "border-slate-200 bg-white text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300 dark:hover:bg-slate-800"
                  }`}
                >
                  <PauseCircle size={16} />
                  {autoFollowAI ? "关闭自动推进" : (currentStageBlocksAuto ? "当前阶段需人工确认" : "按 AI 推荐自动推进")}
                </button>
                <button
                  onClick={approveSelectedStage}
                  disabled={saving || selectingOption || selectedStage.status === "approved" || !hasGeneratedRecommendation}
                  className="inline-flex items-center justify-center gap-2 rounded-xl bg-sky-600 px-4 py-2.5 text-sm font-medium text-white shadow-sm transition hover:bg-sky-500 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {saving ? <Loader2 size={16} className="animate-spin" /> : <Check size={16} />}
                  {actionCopy.approve}
                </button>
              </div>
            </div>

            {selectedJourneyStages.length > 1 && (
              <div className="mb-6 rounded-2xl border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950/60">
                <div className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-400">
                  当前正在处理
                </div>
                <div className="flex flex-wrap gap-2">
                  {selectedJourneyStages.map((stage) => {
                    const active = stage.stage_key === selectedStage.stage_key;
                    const Icon = STAGE_ICON[stage.stage_key];
                    return (
                      <button
                        key={stage.id}
                        type="button"
                        onClick={() => setSelectedKey(stage.stage_key)}
                        className={`inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs transition ${
                          active
                            ? "border-sky-300 bg-sky-50 text-sky-700 dark:border-sky-500/30 dark:bg-sky-500/10 dark:text-sky-300"
                            : "border-slate-200 bg-white text-slate-500 hover:border-slate-300 hover:text-slate-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
                        }`}
                      >
                        <Icon size={13} />
                        {getProgressAction(stage)}
                      </button>
                    );
                  })}
                </div>
              </div>
            )}

            {(autoFollowAI || autoStatus) && (
              <div className={`mb-6 rounded-2xl border px-4 py-3 text-sm ${
                autoFollowAI
                  ? "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-300"
                  : "border-slate-200 bg-slate-50 text-slate-600 dark:border-slate-800 dark:bg-slate-950/60 dark:text-slate-300"
              }`}>
                <div className="font-medium">
                  {autoFollowAI ? "自动推进中，可随时关闭接管。" : "自动推进已停止。"}
                </div>
                <div className="mt-1 text-xs opacity-80">
                  {autoStatus || "系统会默认生成推荐、选中 AI 推荐方案，并自动推进到下一阶段。"}
                </div>
              </div>
            )}

            <div className="mb-6 rounded-[24px] border border-slate-200 bg-[linear-gradient(180deg,_rgba(240,249,255,0.7),_rgba(248,250,252,0.9))] p-4 dark:border-slate-800 dark:bg-slate-950/60">
              <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-900 dark:text-slate-100">
                <ShieldCheck size={16} className="text-sky-500" />
                当前进展说明
              </div>
              <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                <div>
                  <div className="text-sm font-medium text-slate-900 dark:text-slate-100">
                    {selectedJourneyStep?.title || selectedStage.title}
                  </div>
                  <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                    {stageRuntimeLabel}
                  </div>
                </div>
                <div className="flex flex-wrap gap-2">
                  {stageAgentMeta.skills.map((skill) => (
                    <span
                      key={skill}
                      className="rounded-full border border-slate-200 bg-white px-2.5 py-1 text-xs text-slate-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
                    >
                      {skill}
                    </span>
                  ))}
                </div>
              </div>
            </div>

            <div className="grid gap-4 xl:grid-cols-[0.92fr_1.08fr]">
              <div className="rounded-[24px] border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-950/60">
                <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-900 dark:text-slate-100">
                  <Sparkles size={16} className="text-sky-500" />
                  推荐方案
                </div>
                <p className="text-sm leading-6 text-slate-600 dark:text-slate-300">
                  {selectedStage.recommendation?.summary || "系统正在补全一版默认方向，稍后会先把可看的内容放到右侧。"}
                </p>
                <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-3 text-sm text-slate-600 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300">
                  {activeOption
                    ? `当前已选方案：${activeOption.title}。${activeOption.description}`
                    : (selectedStage.recommendation?.recommended_action || "系统会先按当前推荐继续生成。")}
                </div>
                {selectedStage.recommendation?.source && (
                  <div className="mt-3 inline-flex max-w-full items-center gap-1 rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-medium text-slate-500 dark:bg-slate-800 dark:text-slate-400">
                    来源：{recommendationSourceLabel}
                    {selectedStage.recommendation.model ? ` · ${selectedStage.recommendation.model}` : ""}
                  </div>
                )}
                {focus.length > 0 && (
                  <div className="mt-4 flex flex-wrap gap-2">
                    {focus.map((item) => (
                      <span
                        key={item}
                        className="rounded-full bg-sky-50 px-2.5 py-1 text-xs font-medium text-sky-700 dark:bg-sky-500/15 dark:text-sky-300"
                      >
                        {item}
                      </span>
                    ))}
                  </div>
                )}
              </div>

              <div className="rounded-[24px] border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-950/60">
                <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-900 dark:text-slate-100">
                  <Palette size={16} className="text-sky-500" />
                  可调整方向
                </div>
                <div className="mb-4 rounded-xl border border-sky-100 bg-sky-50/80 px-3 py-2 text-xs leading-5 text-sky-700 dark:border-sky-500/20 dark:bg-sky-500/10 dark:text-sky-300">
                  {optionImpactCopy}
                </div>
                {hasGeneratedRecommendation && options.length > 0 ? (
                  <div className="space-y-2">
                    {options.map((option) => (
                      <button
                        key={option.title}
                        type="button"
                        onClick={() => selectStageOption(selectedStage, option.title)}
                        disabled={selectingOption}
                        aria-pressed={selectedOptionTitle === option.title}
                        className={`w-full rounded-xl border p-3 text-left transition ${
                          selectedOptionTitle === option.title
                            ? "border-sky-300 bg-sky-50 shadow-sm shadow-sky-100/70 dark:border-sky-400/40 dark:bg-sky-500/10"
                            : option.recommended
                              ? "border-sky-200 bg-sky-50/70 dark:border-sky-500/30 dark:bg-sky-500/10"
                              : "border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50 dark:border-slate-800 dark:bg-slate-900 dark:hover:border-slate-700 dark:hover:bg-slate-800/80"
                        }`}
                      >
                        <div className="mb-1 flex items-center justify-between gap-2">
                          <span className="text-sm font-medium text-slate-900 dark:text-slate-100">{option.title}</span>
                          <div className="flex items-center gap-2">
                            {selectedOptionTitle === option.title && (
                              <span className="rounded-full bg-slate-900 px-2 py-0.5 text-[10px] font-medium text-white dark:bg-slate-100 dark:text-slate-900">
                                已选
                              </span>
                            )}
                            {option.recommended && (
                              <span className="rounded-full bg-sky-600 px-2 py-0.5 text-[10px] font-medium text-white">推荐</span>
                            )}
                          </div>
                        </div>
                        <p className="text-xs leading-5 text-slate-500 dark:text-slate-400">{option.description}</p>
                      </button>
                    ))}
                  </div>
                ) : (
                  <div className="rounded-xl bg-white p-4 text-sm text-slate-500 dark:bg-slate-900 dark:text-slate-400">
                    先生成推荐方案，系统才会给出可切换的方向。
                  </div>
                )}
              </div>
            </div>

            <div className="mt-4 rounded-[24px] border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-950/60">
              <div className="mb-1 flex items-center gap-2 text-sm font-semibold text-slate-900 dark:text-slate-100">
                <MessageSquare size={16} className="text-sky-500" />
                当前最新成果
              </div>
              <p className="mb-4 text-sm text-slate-500 dark:text-slate-400">
                {artifactHeadline}
              </p>
              {primaryArtifact && (
                <div className="mb-4 overflow-hidden rounded-[22px] border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
                  <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3 dark:border-slate-800">
                    <div>
                      <div className="text-sm font-semibold text-slate-900 dark:text-slate-100">{primaryArtifact.title}</div>
                      <div className="text-xs text-slate-400">{primaryArtifact.subtitle}</div>
                    </div>
                    <a
                      href={primaryArtifact.url}
                      target="_blank"
                      rel="noreferrer"
                      className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-600 transition hover:border-sky-200 hover:text-sky-700 dark:border-slate-700 dark:text-slate-300"
                    >
                      新窗口打开
                    </a>
                  </div>
                  {primaryArtifact.kind === "iframe" ? (
                    <iframe
                      title={primaryArtifact.title}
                      src={primaryArtifact.url}
                      className="h-[520px] w-full bg-white"
                    />
                  ) : (
                    <img src={primaryArtifact.url} alt={primaryArtifact.title} className="w-full bg-white" />
                  )}
                </div>
              )}
              {!primaryArtifact && hasGeneratedRecommendation && (
                <div className="mb-4 rounded-[22px] border border-dashed border-slate-300 bg-slate-50 px-4 py-10 text-center dark:border-slate-700 dark:bg-slate-900">
                  <div className="mx-auto max-w-xl">
                    <div className="text-sm font-semibold text-slate-900 dark:text-slate-100">可预览结果还在生成中</div>
                    <p className="mt-2 text-sm leading-6 text-slate-500 dark:text-slate-400">
                      当前已经有推荐方向，但可直接打开的页面还在生成。你可以先看下面的内容摘要，或者继续推进下一版。
                    </p>
                  </div>
                </div>
              )}
              <div className="grid gap-4 xl:grid-cols-[minmax(0,1.2fr)_320px]">
                <div className="min-h-[188px] rounded-[22px] border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-900">
                  {parsedArtifact.lead.length > 0 && (
                    <div className="space-y-2">
                      {parsedArtifact.lead.map((paragraph) => (
                        <p
                          key={paragraph}
                          className="text-sm leading-7 text-slate-700 dark:text-slate-300"
                        >
                          {paragraph}
                        </p>
                      ))}
                    </div>
                  )}
                  {parsedArtifact.sections.length > 0 && (
                    <div className="mt-4 grid gap-3 md:grid-cols-2">
                      {parsedArtifact.sections.map((section) => (
                        <article
                          key={`${section.title}-${section.body}`}
                          className="rounded-2xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-950/70"
                        >
                          <div className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                            {section.title}
                          </div>
                          <p className="mt-2 text-sm leading-6 text-slate-600 dark:text-slate-300">
                            {section.body}
                          </p>
                        </article>
                      ))}
                    </div>
                  )}
                  {parsedArtifact.bullets.length > 0 && (
                    <div className="mt-4 space-y-2">
                      {parsedArtifact.bullets.map((item, index) => (
                        <div
                          key={`${index}-${item}`}
                          className="flex items-start gap-3 rounded-2xl border border-slate-200 bg-white px-4 py-3 dark:border-slate-800 dark:bg-slate-950/70"
                        >
                          <span className="mt-0.5 flex size-6 items-center justify-center rounded-full bg-sky-100 text-[11px] font-semibold text-sky-700 dark:bg-sky-500/15 dark:text-sky-300">
                            {index + 1}
                          </span>
                          <p className="text-sm leading-6 text-slate-700 dark:text-slate-300">
                            {item}
                          </p>
                        </div>
                      ))}
                    </div>
                  )}
                  {!parsedArtifact.lead.length && !parsedArtifact.sections.length && !parsedArtifact.bullets.length && (
                    <p className="text-sm leading-7 text-slate-700 dark:text-slate-300">
                      {artifactSummary}
                    </p>
                  )}
                </div>
                <div className="rounded-[22px] border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-900">
                  <div className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">确认后会发生什么</div>
                  <div className="mt-3 space-y-3 text-sm text-slate-600 dark:text-slate-300">
                    {activeOption && (
                      <div className="rounded-xl bg-white px-3 py-3 dark:bg-slate-950/70">
                        当前锁定方向：<span className="font-semibold text-slate-900 dark:text-slate-100">{activeOption.title}</span>
                      </div>
                    )}
                    {focus.length > 0 && (
                      <div className="rounded-xl bg-white px-3 py-3 dark:bg-slate-950/70">
                        <div className="mb-2 text-xs font-semibold uppercase tracking-[0.14em] text-slate-400">本阶段重点</div>
                        <div className="flex flex-wrap gap-2">
                          {focus.map((item) => (
                            <span
                              key={item}
                              className="rounded-full bg-sky-50 px-2.5 py-1 text-xs font-medium text-sky-700 dark:bg-sky-500/10 dark:text-sky-300"
                            >
                              {item}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                    <div className="rounded-xl bg-white px-3 py-3 dark:bg-slate-950/70">
                      系统会把当前已选方向写入本阶段结果，并推进到下一步继续生成。
                    </div>
                    <div className="rounded-xl bg-white px-3 py-3 dark:bg-slate-950/70">
                      如果你开着自动推进，下一阶段会自动生成推荐并优先选择 AI 推荐方案。
                    </div>
                    <div className="rounded-xl bg-white px-3 py-3 dark:bg-slate-950/70">
                      如果当前产物不对，先切方向或提交修改意见，再确认，不然错误会被带到后续阶段。
                    </div>
                  </div>
                </div>
              </div>
              {selectedStage.approved_at && (
                <div className="mt-3 text-xs text-slate-400">
                  已确认于 {formatDate(selectedStage.approved_at)}
                </div>
              )}
              {!hasGeneratedRecommendation && (
                <div className="mt-3 text-xs text-slate-400">
                  先生成推荐方案，再切换方向或确认进入下一步。
                </div>
              )}
              {(selectedStage.stage_key === "development" || selectedStage.stage_key === "acceptance") && (
                <div className="mt-4 flex flex-wrap gap-2">
                  {developmentReportUrl && (
                    <a
                      href={developmentReportUrl}
                      target="_blank"
                      rel="noreferrer"
                      className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 transition hover:border-sky-200 hover:text-sky-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
                    >
                      打开开发记录
                    </a>
                  )}
                  {acceptanceReportUrl && (
                    <a
                      href={acceptanceReportUrl}
                      target="_blank"
                      rel="noreferrer"
                      className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 transition hover:border-sky-200 hover:text-sky-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
                    >
                      打开检查记录
                    </a>
                  )}
                </div>
              )}
              {artifactList.length > 0 && (
                <div className="mt-5 space-y-2">
                  <div className="text-xs font-semibold text-slate-400">已生成内容</div>
                  {artifactList.map((artifact: ArtifactRecord) => (
                    <div
                      key={`${artifact.type}-${artifact.label}`}
                      className="flex items-center justify-between gap-3 rounded-xl border border-slate-200 bg-slate-50 px-3 py-3 text-xs dark:border-slate-800 dark:bg-slate-900"
                    >
                      <div className="min-w-0">
                        <div className="truncate text-sm font-medium text-slate-700 dark:text-slate-200">{artifact.label || artifact.type}</div>
                        <div className="mt-1 text-[11px] text-slate-400">{artifact.type}</div>
                        {artifact.url && (
                          <a
                            href={withAuthToken(artifact.url)}
                            target="_blank"
                            rel="noreferrer"
                            className="mt-1 inline-block text-[11px] text-sky-700 hover:underline dark:text-sky-300"
                          >
                            打开产物
                          </a>
                        )}
                      </div>
                      <span className={`rounded-full px-2.5 py-1 ${getArtifactStatusClass(artifact.status)}`}>
                        {getArtifactStatusLabel(artifact.status)}
                      </span>
                    </div>
                  ))}
                </div>
              )}
              {relatedArtifactLinks.length > 0 && (
                <div className="mt-5 space-y-2">
                  <div className="text-xs font-semibold text-slate-400">更多结果</div>
                  <div className="flex flex-wrap gap-2">
                    {relatedArtifactLinks.map((artifact) => (
                      <a
                        key={`${artifact.label}-${artifact.url}`}
                        href={artifact.url}
                        target="_blank"
                        rel="noreferrer"
                        className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 transition hover:border-sky-200 hover:text-sky-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
                      >
                        {artifact.label}
                      </a>
                    ))}
                  </div>
                </div>
              )}
              {stageTasks.length > 0 && (
                <div className="mt-5 space-y-2">
                  <div className="text-xs font-semibold text-slate-400">系统动作</div>
                  {stageTasks.map((task) => (
                    <div
                      key={task.id}
                      className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs dark:border-slate-800 dark:bg-slate-900"
                    >
                      <div className="flex items-center justify-between gap-3">
                        <div className="font-medium text-slate-700 dark:text-slate-200">{task.title}</div>
                        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-slate-500 dark:bg-slate-800 dark:text-slate-400">
                          {task.status}
                        </span>
                      </div>
                      {(task.assigned_agent || task.result_summary) && (
                        <div className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">
                          {task.assigned_agent ? `执行角色：${task.assigned_agent}` : ""}
                          {task.assigned_agent && task.result_summary ? " · " : ""}
                          {task.result_summary || ""}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>

            <form onSubmit={requestRevision} className="mt-6 rounded-2xl border border-slate-200 bg-slate-50 p-5 dark:border-slate-800 dark:bg-slate-950/60">
              <label className="mb-2 flex items-center gap-2 text-sm font-semibold text-slate-900 dark:text-slate-100">
                <RefreshCcw size={16} className="text-amber-500" />
                {actionCopy.revisionTitle}
              </label>
              <textarea
                value={feedback}
                onChange={(event) => setFeedback(event.target.value)}
                rows={4}
                placeholder={actionCopy.revisionPlaceholder}
                className="w-full resize-none rounded-xl border border-slate-200 bg-white px-3 py-3 text-sm text-slate-900 outline-none transition focus:border-sky-400 focus:ring-2 focus:ring-sky-100 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:focus:ring-sky-500/20"
              />
              <div className="mt-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <p className="text-xs text-slate-400">
                  首页只负责创建新需求；当前阶段的调整意见直接在工作区里处理。
                </p>
                <button
                  type="submit"
                  disabled={saving || !feedback.trim()}
                  className="inline-flex items-center justify-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 transition hover:border-amber-200 hover:text-amber-700 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
                >
                  {saving ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
                  提交修改意见
                </button>
              </div>
            </form>
          </section>
        </div>
      </main>
    </div>
  );
}
