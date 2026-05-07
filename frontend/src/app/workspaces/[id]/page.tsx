"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  ArrowLeft,
  ArrowUpRight,
  Bot,
  CheckCircle2,
  ChevronDown,
  Code2,
  FolderKanban,
  GitBranch,
  ImagePlus,
  Layers3,
  Loader2,
  MessageCircle,
  Paperclip,
  Rocket,
  Send,
  Sparkles,
  X,
} from "lucide-react";

import { TopNav } from "../../components/topnav";
import { useAvailableModels } from "@/hooks/use-available-models";

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
type WorkspaceChatMessage = {
  id: string;
  role: "user" | "assistant";
  title?: string;
  content: string;
  tone?: "muted" | "status";
};

type StageArtifactView = {
  kind: "iframe" | "image";
  title: string;
  subtitle: string;
  url: string;
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
  planning_sessions?: {
    id: string;
    workspace_id: string;
    title: string;
    status: string;
    mode: string;
    input_text: string;
    summary: string | null;
    created_at: string | null;
    updated_at: string | null;
  }[];
};

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

function getStageProgressLabel(stage: WorkspaceStage) {
  switch (stage.stage_key) {
    case "requirements":
      return "正在理解需求";
    case "product":
      return "正在整理页面结构";
    case "ui_direction":
      return "正在确定视觉方向";
    case "prototype":
      return "正在生成页面结果";
    case "technical":
      return "正在补充技术方案";
    case "development":
      return "正在开发成品";
    case "acceptance":
      return "正在检查和修正";
    case "deployment":
      return "正在整理最终交付";
  }
}

function stageHasDeliverable(stage: WorkspaceStage) {
  return Boolean(
    stage.content
    || stage.recommendation?.summary
    || stage.recommendation?.project_path
    || (stage.recommendation?.artifacts || []).some((artifact) => artifact.status === "ready" && artifact.url)
  );
}

function stageHasVisualDeliverable(stage: WorkspaceStage) {
  return (stage.recommendation?.artifacts || []).some(
    (artifact) =>
      artifact.status === "ready"
      && Boolean(artifact.url)
      && (
        artifact.type === "prototype_html"
        || artifact.type === "development_preview"
        || artifact.type === "desktop_design"
        || artifact.type === "mobile_design"
      )
  );
}

function pickPreferredStageKey(
  stages: WorkspaceStage[],
  currentStageKey: StageKey,
  existingSelectedKey?: StageKey | null
) {
  if (existingSelectedKey) {
    const existingStage = stages.find((stage) => stage.stage_key === existingSelectedKey);
    if (existingStage && (stageHasVisualDeliverable(existingStage) || stageHasDeliverable(existingStage))) {
      return existingSelectedKey;
    }
  }

  const visualPriority: StageKey[] = ["development", "acceptance", "prototype", "ui_direction"];
  for (const key of visualPriority) {
    const matched = stages.find((stage) => stage.stage_key === key && stageHasVisualDeliverable(stage));
    if (matched) return matched.stage_key;
  }

  const contentPriority: StageKey[] = ["product", "requirements", "technical", "development", "acceptance", "deployment"];
  for (const key of contentPriority) {
    const matched = stages.find((stage) => stage.stage_key === key && stageHasDeliverable(stage));
    if (matched) return matched.stage_key;
  }

  const previewPriority: StageKey[] = ["prototype", "development", "acceptance", "ui_direction", "product", "requirements", "technical", "deployment"];
  for (const key of previewPriority) {
    const matched = stages.find((stage) => stage.stage_key === key && stageHasDeliverable(stage));
    if (matched) return matched.stage_key;
  }

  return currentStageKey;
}

function buildStageArtifactMessages(stage: WorkspaceStage): WorkspaceChatMessage[] {
  const readyArtifacts = (stage.recommendation?.artifacts || []).filter(
    (artifact) => artifact.status === "ready" && artifact.url
  );

  return readyArtifacts.map((artifact, index) => ({
    id: `${stage.id}-artifact-${artifact.type || index}`,
    role: "assistant" as const,
    title: stage.title,
    content: (() => {
      switch (artifact.type) {
        case "desktop_design":
          return "已生成桌面端设计稿。";
        case "mobile_design":
          return "已生成移动端设计稿。";
        case "prototype_html":
          return "已生成 HTML 页面预览。";
        case "development_preview":
          return "已生成可运行预览。";
        case "development_report":
          return "已整理开发记录。";
        case "acceptance_report":
          return "已整理验收结果。";
        default:
          return `已生成${artifact.label || "阶段成果"}。`;
      }
    })(),
    tone: "status",
  }));
}

function buildWorkspaceChatMessages(
  workspace: Workspace,
  stages: WorkspaceStage[],
  currentStageKey: StageKey,
  currentActivity?: string | null
): WorkspaceChatMessage[] {
  const messages: WorkspaceChatMessage[] = [];

  messages.push({
    id: "workspace-request",
    role: "user",
    content: workspace.description || workspace.name,
  });

  messages.push({
    id: "workspace-start",
    role: "assistant",
    content: "收到。我会直接开始，左侧给你看进展，右侧直接展示成果。",
    tone: "status",
  });

  for (const stage of stages) {
    const isCurrent = stage.stage_key === currentStageKey;

    if (stage.user_feedback?.trim()) {
      messages.push({
        id: `${stage.id}-feedback`,
        role: "user",
        content: stage.user_feedback.trim(),
      });
    }

    const artifactMessages = buildStageArtifactMessages(stage);
    if (artifactMessages.length > 0) {
      messages.push(...artifactMessages);
      continue;
    }

    if (stage.status === "approved" && (stage.recommendation?.selected_option || stage.content || stage.recommendation?.summary)) {
      messages.push({
        id: `${stage.id}-done`,
        role: "assistant",
        title: stage.title,
        content: `已完成「${stage.title}」。`,
        tone: "status",
      });
      continue;
    }

    if (isCurrent) {
      messages.push({
        id: `${stage.id}-pending`,
        role: "assistant",
        title: stage.title,
        content: currentActivity || `${getStageProgressLabel(stage)}。`,
        tone: "status",
      });
    }
  }

  return messages;
}

export default function WorkspaceDetailPage() {
  const params = useParams<{ id: string }>();
  const workspaceId = params.id;
  const autoActionRef = useRef<string | null>(null);
  const chatScrollRef = useRef<HTMLDivElement | null>(null);
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
  const [error, setError] = useState("");
  const [importNotice, setImportNotice] = useState("");
  const [isDesktop, setIsDesktop] = useState(false);
  const [rebinding, setRebinding] = useState(false);
  const [rebindPath, setRebindPath] = useState("");
  const [streamingMessageId, setStreamingMessageId] = useState<string | null>(null);
  const [streamingText, setStreamingText] = useState("");
  const [selectedModel, setSelectedModel] = useState<string>("");
  const [showModelMenu, setShowModelMenu] = useState(false);
  const [uploadedFiles, setUploadedFiles] = useState<File[]>([]);
  const [imagePreviews, setImagePreviews] = useState<{ name: string; url: string }[]>([]);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const { models: availableModels, defaultModel } = useAvailableModels();

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
      setSelectedKey((current) => pickPreferredStageKey(data.stages, data.current_stage, current));
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

  const selectedStage = useMemo(() => {
    if (!workspace?.stages?.length) return null;
    return workspace.stages.find((stage) => stage.stage_key === selectedKey) || workspace.stages[0];
  }, [selectedKey, workspace]);

  const currentStage = useMemo(() => {
    if (!workspace?.stages?.length) return null;
    return workspace.stages.find((stage) => stage.stage_key === workspace.current_stage) || workspace.stages[0];
  }, [workspace]);

  useEffect(() => {
    if (!workspace?.stages?.length) return;
    setSelectedKey((current) => pickPreferredStageKey(workspace.stages, workspace.current_stage, current));
  }, [workspace?.current_stage, workspace?.stages]);

  const journeySteps = useMemo(() => {
    if (!workspace?.stages?.length) return [] as JourneyStep[];
    return buildJourneySteps(workspace.stages, workspace.current_stage);
  }, [workspace]);
  const selectedJourneyStepId = selectedStage ? toJourneyStepId(selectedStage.stage_key) : null;
  const selectedJourneyStep = journeySteps.find((step) => step.id === selectedJourneyStepId) || journeySteps[0] || null;

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
      setSelectedKey((current) => pickPreferredStageKey(data.stages, data.current_stage, current));
    } catch (err: any) {
      setError(formatActionError("确认当前结果失败", err));
    } finally {
      setSaving(false);
    }
  };

  const requestRevision = async (instruction: string) => {
    if (!selectedStage || !instruction.trim() || saving) return;
    const trimmedInstruction = instruction.trim();
    setSaving(true);
    setError("");
    setWorkspace((current) => {
      if (!current) return current;
      return {
        ...current,
        stages: current.stages.map((item) =>
          item.id === selectedStage.id
            ? { ...item, user_feedback: trimmedInstruction, status: "revision_requested" }
            : item
        ),
      };
    });
    try {
      const res = await fetch(
        `/api/workspaces/${workspaceId}/stages/${selectedStage.stage_key}/request-revision`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ feedback: trimmedInstruction }),
        }
      );
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "提交修改意见失败");
      }
      autoActionRef.current = null;
      await generateStage(selectedStage, trimmedInstruction);
    } catch (err: any) {
      setError(formatActionError("提交修改意见失败", err));
    } finally {
      setSaving(false);
    }
  };

  const generateStage = async (stage: WorkspaceStage, instruction?: string | null) => {
    if (generating) return;
    setGenerating(true);
    setError("");
    try {
      const res = await fetch(
        `/api/workspaces/${workspaceId}/stages/${stage.stage_key}/generate`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ instruction: instruction?.trim() || feedback.trim() || stage.user_feedback || null }),
        }
      );
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "生成推荐失败");
      }
      const updatedStage = await res.json();
      autoActionRef.current = null;
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
      autoActionRef.current = null;
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

  useEffect(() => {
    if (!workspace || !currentStage) return;
    if (loading || generating || saving || selectingOption || generatingPrototype || generatingDesigns) return;
    if (error) return;

    const currentArtifacts = currentStage.recommendation?.artifacts || [];
    const hasReadyDesign = currentArtifacts.some((artifact) =>
      (artifact.type === "desktop_design" || artifact.type === "mobile_design") && artifact.status === "ready" && artifact.url
    );
    const hasReadyPrototype = currentArtifacts.some((artifact) =>
      artifact.type === "prototype_html" && artifact.status === "ready" && artifact.url
    );
    const options = currentStage.recommendation?.options || [];
    const selectedOption = currentStage.recommendation?.selected_option || null;
    const recommendedOption =
      options.find((option) => option.recommended)?.title || options[0]?.title || null;

    let actionKey: string | null = null;
    let runner: (() => void) | null = null;

    if (currentStage.stage_key === "prototype") {
      if (!hasReadyDesign) {
        actionKey = `${currentStage.id}:designs`;
        runner = () => { void generateDesigns(); };
      } else if (!hasReadyPrototype) {
        actionKey = `${currentStage.id}:prototype`;
        runner = () => { void generatePrototype(); };
      } else if (currentStage.status !== "approved") {
        actionKey = `${currentStage.id}:approve`;
        runner = () => { void approveStage(currentStage); };
      }
    } else if (!currentStage.recommendation?.source) {
      actionKey = `${currentStage.id}:generate`;
      runner = () => { void generateStage(currentStage); };
    } else if (recommendedOption && selectedOption !== recommendedOption) {
      actionKey = `${currentStage.id}:select:${recommendedOption}`;
      runner = () => { void selectStageOption(currentStage, recommendedOption); };
    } else if (currentStage.status !== "approved") {
      actionKey = `${currentStage.id}:approve`;
      runner = () => { void approveStage(currentStage); };
    }

    if (!actionKey || !runner) return;
    if (autoActionRef.current === actionKey) return;

    autoActionRef.current = actionKey;
    runner();
  }, [
    currentStage,
    error,
    generating,
    generatingDesigns,
    generatingPrototype,
    loading,
    saving,
    selectingOption,
    workspace,
  ]);

  const currentActivity = generatingDesigns
    ? "正在生成设计稿。"
    : generatingPrototype
      ? "正在生成 HTML 页面预览。"
      : generating
        ? `${getStageProgressLabel(currentStage || selectedStage || workspace?.stages?.[0] || {
            stage_key: "requirements",
          } as WorkspaceStage)}。`
        : saving
          ? "正在根据你的补充继续调整。"
          : null;
  const chatMessages = workspace
    ? buildWorkspaceChatMessages(workspace, workspace.stages, workspace.current_stage, currentActivity)
    : [];
  const streamingTarget = useMemo(() => {
    for (let index = chatMessages.length - 1; index >= 0; index -= 1) {
      const message = chatMessages[index];
      if (message.role === "assistant") {
        return message;
      }
    }
    return null;
  }, [chatMessages]);

  useEffect(() => {
    if (!streamingTarget) {
      setStreamingMessageId(null);
      setStreamingText("");
      return;
    }

    const targetContent = streamingTarget.content;
    const nextBaseText =
      streamingMessageId === streamingTarget.id && targetContent.startsWith(streamingText)
        ? streamingText
        : "";

    setStreamingMessageId(streamingTarget.id);
    setStreamingText(nextBaseText);

    let cancelled = false;
    let timeoutId: number | null = null;
    let visibleLength = nextBaseText.length;

    const tick = () => {
      if (cancelled || visibleLength >= targetContent.length) return;
      visibleLength = Math.min(
        visibleLength + (visibleLength > 80 ? 3 : visibleLength > 24 ? 2 : 1),
        targetContent.length
      );
      setStreamingText(targetContent.slice(0, visibleLength));
      timeoutId = window.setTimeout(tick, visibleLength > 80 ? 10 : 22);
    };

    if (nextBaseText !== targetContent) {
      timeoutId = window.setTimeout(tick, 60);
    }

    return () => {
      cancelled = true;
      if (timeoutId !== null) {
        window.clearTimeout(timeoutId);
      }
    };
  }, [streamingMessageId, streamingTarget, streamingText]);

  useEffect(() => {
    const container = chatScrollRef.current;
    if (!container) return;
    container.scrollTo({ top: container.scrollHeight, behavior: "smooth" });
  }, [chatMessages.length, streamingText]);

  if (loading) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-indigo-50/30 dark:from-slate-950 dark:via-slate-950 dark:to-indigo-950/20">
        <TopNav />
        <main className="flex h-screen items-center justify-center pt-14 text-slate-400">
          <Loader2 className="animate-spin" />
        </main>
      </div>
    );
  }

  if (!workspace || !selectedStage) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-indigo-50/30 dark:from-slate-950 dark:via-slate-950 dark:to-indigo-950/20">
        <TopNav />
        <main className="mx-auto max-w-4xl px-6 pt-28">
          <Link href="/workspaces" className="inline-flex items-center gap-2 text-sm text-slate-500 hover:text-indigo-600 dark:hover:text-indigo-400 transition-colors">
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
  const options = selectedStage.recommendation?.options || [];
  const activeOption = options.find((option) => option.title === selectedOptionTitle) || null;
  const selectedStageArtifacts = selectedStage.recommendation?.artifacts || [];
  const readySelectedArtifacts = selectedStageArtifacts.filter((artifact) => artifact.status === "ready" && artifact.url);
  const prototypeArtifact = selectedStageArtifacts.find((artifact) =>
    artifact.type === "prototype_html" && artifact.status === "ready" && artifact.url
  );
  const developmentPreviewArtifact = selectedStageArtifacts.find((artifact) =>
    artifact.type === "development_preview" && artifact.status === "ready" && artifact.url
  );
  const desktopDesign = selectedStageArtifacts.find((artifact) =>
    artifact.type === "desktop_design" && artifact.status === "ready" && artifact.url
  );
  const mobileDesign = selectedStageArtifacts.find((artifact) =>
    artifact.type === "mobile_design" && artifact.status === "ready" && artifact.url
  );
  const prototypeUrl = prototypeArtifact?.url ? withAuthToken(prototypeArtifact.url) : "";
  const developmentPreviewUrl = developmentPreviewArtifact?.url ? withAuthToken(developmentPreviewArtifact.url) : "";
  const desktopDesignUrl = desktopDesign?.url ? withAuthToken(desktopDesign.url) : "";
  const mobileDesignUrl = mobileDesign?.url ? withAuthToken(mobileDesign.url) : "";
  const artifactHeadline = selectedStage.description || getArtifactHeadline(selectedJourneyStep?.id || null);
  const latestExecutionStage = [...workspace.stages].reverse().find((stage) =>
    Boolean(
      stage.recommendation?.execution_session_id
      || stage.recommendation?.project_path
      || stage.recommendation?.task_items?.length
    )
  ) || null;
  const latestExecution = latestExecutionStage?.recommendation || null;
  const executionDetailHref = latestExecution?.execution_session_id ? `/executions/${workspace.id}` : null;
  const primaryArtifact: StageArtifactView | null =
    developmentPreviewUrl
      ? { kind: "iframe", title: "可运行预览", subtitle: "当前阶段已经生成的可运行页面。", url: developmentPreviewUrl }
      : prototypeUrl
        ? { kind: "iframe", title: "HTML 原型", subtitle: "当前阶段生成的页面原型。", url: prototypeUrl }
        : desktopDesignUrl
          ? { kind: artifactPreviewKind(desktopDesign), title: "桌面设计稿", subtitle: "当前阶段生成的桌面端设计稿。", url: desktopDesignUrl }
          : mobileDesignUrl
            ? { kind: artifactPreviewKind(mobileDesign), title: "移动端设计稿", subtitle: "当前阶段生成的移动端设计稿。", url: mobileDesignUrl }
            : null;
  const latestPreviewLink = developmentPreviewUrl || prototypeUrl || desktopDesignUrl || mobileDesignUrl || "";
  const latestPreviewLabel = developmentPreviewUrl
    ? "打开最新页面预览"
    : prototypeUrl
      ? "打开页面预览"
      : desktopDesignUrl
        ? "打开桌面设计稿"
        : mobileDesignUrl
          ? "打开移动端设计稿"
          : "";
  const stageDocument = activeOption?.content || selectedStage.content || selectedStage.recommendation?.summary || "";
  const stageProjectPath = selectedStage.recommendation?.project_path || workspace.root_path || "";

  return (
    <div className="h-screen overflow-hidden bg-gradient-to-br from-slate-50 via-white to-indigo-50/30 dark:from-slate-950 dark:via-slate-950 dark:to-indigo-950/20">
      <TopNav />
      <main className="mx-auto flex h-[calc(100vh-3.5rem)] max-w-[1440px] flex-col px-4 pt-20 sm:px-6 lg:px-8">
        <div className="grid min-h-0 flex-1 gap-5 lg:grid-cols-[320px_minmax(0,1fr)]" style={{ gridTemplateRows: 'minmax(0, 1fr)' }}>
          {/* Left: Workspace info + Chat panel */}
          <section className="flex min-h-0 flex-col rounded-2xl bg-white/70 shadow-sm ring-1 ring-slate-200/60 backdrop-blur-sm transition-all dark:bg-slate-900/70 dark:ring-slate-700/40">
            {/* Workspace header */}
            <div className="shrink-0 border-b border-slate-200/80 px-4 py-4 dark:border-slate-800">
              <Link
                href="/workspaces"
                className="inline-flex items-center gap-1 text-xs font-medium text-slate-500 transition hover:text-indigo-600 dark:text-slate-400 dark:hover:text-indigo-400"
              >
                <ArrowLeft size={13} />
                返回工作区
              </Link>

              <div className="mt-3 flex items-start gap-2.5">
                <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-indigo-50 text-indigo-600 ring-1 ring-indigo-100 dark:bg-indigo-500/10 dark:text-indigo-400 dark:ring-indigo-500/20">
                  <FolderKanban size={15} />
                </div>
                <div className="min-w-0">
                  <h1 className="truncate text-base font-semibold tracking-tight text-slate-900 dark:text-slate-100">
                    {workspace.name}
                  </h1>
                  <p className="mt-0.5 text-[11px] leading-4 text-slate-500 line-clamp-2 dark:text-slate-400">
                    {workspace.description || "还没有补充任务目标。"}
                  </p>
                </div>
              </div>

              <div className="mt-3 h-1 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                <div
                  className="h-full rounded-full bg-indigo-500 transition-all duration-500"
                  style={{ width: `${journeyProgress}%` }}
                />
              </div>

              <div className="mt-3 flex flex-wrap gap-1.5">
                <span className="inline-flex items-center gap-1 rounded-full bg-indigo-50 px-2 py-0.5 text-[10px] font-medium text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300">
                  <Sparkles size={10} className="text-indigo-500" />
                  {currentActivity || `处理中：${currentStage?.title || selectedStage.title}`}
                </span>
                <span className="rounded-full border border-slate-200/80 bg-white px-2 py-0.5 text-[10px] font-medium text-slate-600 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300">
                  {journeyApproved}/{journeySteps.length || 1}
                </span>
                <span className="rounded-full border border-slate-200/80 bg-white px-2 py-0.5 text-[10px] font-medium text-slate-600 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300">
                  {workspace.storage_mode === "local" ? "本地" : "服务器"}
                </span>
              </div>

              {error && (
                <div className="mt-3 rounded-lg border border-red-200/80 bg-red-50/80 px-3 py-2 text-[11px] text-red-600 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-300">
                  {error}
                </div>
              )}
              {importNotice && (
                <div className="mt-3 rounded-lg border border-emerald-200/80 bg-emerald-50/80 px-3 py-2 text-[11px] text-emerald-700 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-300">
                  {importNotice}
                </div>
              )}
              {(workspace.binding_state === "missing_directory" || workspace.binding_state === "missing_manifest") && (
                <div className="mt-3 rounded-xl border border-amber-200/80 bg-amber-50/80 px-3 py-3 text-[11px] text-amber-800 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-300">
                  <div className="font-medium">
                    {workspace.binding_state === "missing_directory"
                      ? "本地目录不存在，请重新绑定。"
                      : "绑定文件丢失，请重新绑定。"}
                  </div>
                  <div className="mt-2 space-y-2">
                    <input
                      value={rebindPath}
                      onChange={(event) => setRebindPath(event.target.value)}
                      placeholder="输入新的本地目录绝对路径"
                      className="w-full rounded-lg border border-amber-200/80 bg-white/80 px-2.5 py-1.5 text-[10px] text-slate-900 outline-none ring-1 ring-transparent transition focus:border-amber-300 focus:ring-amber-100 dark:border-amber-500/30 dark:bg-slate-900 dark:text-slate-100 dark:focus:ring-amber-500/20"
                    />
                    <div className="flex gap-1.5">
                      {isDesktop && (
                        <button
                          type="button"
                          onClick={chooseRebindDirectory}
                          className="inline-flex items-center justify-center rounded-lg border border-amber-200/80 bg-white/80 px-2 py-1 text-[10px] font-medium text-amber-800 transition hover:bg-amber-50/80 dark:border-amber-500/30 dark:bg-slate-900 dark:text-amber-300 dark:hover:bg-slate-800"
                        >
                          选择
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={rebindDirectory}
                        disabled={!rebindPath.trim() || rebinding}
                        className="inline-flex items-center justify-center rounded-lg bg-indigo-600 px-2 py-1 text-[10px] font-medium text-white transition hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {rebinding ? <Loader2 size={11} className="animate-spin" /> : "绑定"}
                      </button>
                    </div>
                  </div>
                </div>
              )}
            </div>

            <div ref={chatScrollRef} className="flex-1 overflow-y-auto px-3 py-3">
              <div className="space-y-1">
                {chatMessages.map((message) => (
                  <div key={message.id} className="msg-appear">
                    <div
                      className={`min-w-0 flex-1 rounded-lg px-2.5 py-1.5 text-[12px] leading-5 ${
                        message.role === "user"
                          ? "bg-indigo-50/70 text-slate-900 dark:bg-indigo-500/10 dark:text-slate-100"
                          : message.tone === "status"
                            ? "bg-slate-50 text-slate-500 dark:bg-slate-800/60 dark:text-slate-400"
                            : "bg-slate-50 text-slate-700 dark:bg-slate-800/60 dark:text-slate-300"
                      }`}
                    >
                      {message.title && message.role === "assistant" && (
                        <div className="mb-px text-[12px] font-medium text-indigo-400/80 dark:text-indigo-400/60">
                          {message.title}
                        </div>
                      )}
                      <div className="whitespace-pre-wrap">
                        {message.id === streamingMessageId ? streamingText : message.content}
                        {message.id === streamingMessageId && streamingText.length < message.content.length && (
                          <span className="ml-0.5 inline-block h-3 w-1 animate-pulse rounded-full bg-indigo-500 align-[-1px]" />
                        )}
                      </div>
                    </div>
                  </div>
                ))}
                {(generating || generatingDesigns || generatingPrototype || saving) && (
                  <div className="msg-appear">
                    <div className="flex min-w-0 flex-1 items-center gap-1.5 rounded-lg bg-slate-50 px-2.5 py-1.5 text-[12px] text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">
                      <Loader2 size={12} className="animate-spin text-indigo-500" />
                      正在处理…
                    </div>
                  </div>
                )}
              </div>
            </div>

            {/* Bottom input: card style like homepage */}
            <div className="shrink-0 px-3 py-2">
              <div className="group relative rounded-2xl bg-white/70 dark:bg-slate-900/70 backdrop-blur-xl shadow-lg shadow-indigo-500/5 dark:shadow-none ring-1 ring-slate-200/80 dark:ring-slate-700/50 transition-all duration-300">
                <div className="px-3 pt-2.5 pb-1">
                  <textarea
                    ref={textareaRef}
                    rows={2}
                    value={feedback}
                    onChange={(e) => {
                      setFeedback(e.target.value);
                      const el = textareaRef.current;
                      if (el) {
                        el.style.height = "auto";
                        el.style.height = `${Math.min(el.scrollHeight, 120)}px`;
                      }
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey && feedback.trim() && !saving && !generating && !generatingDesigns && !generatingPrototype) {
                        e.preventDefault();
                        void requestRevision(feedback);
                        setFeedback("");
                        setUploadedFiles([]);
                        setImagePreviews([]);
                        if (textareaRef.current) textareaRef.current.style.height = "auto";
                      }
                    }}
                    placeholder="继续说需求，或直接说要改哪里"
                    disabled={saving || generating || generatingDesigns || generatingPrototype}
                    className="w-full resize-none bg-transparent text-xs leading-relaxed text-slate-900 outline-none placeholder:text-slate-400/70 disabled:opacity-50 dark:text-slate-100 dark:placeholder:text-slate-500/70"
                  />
                </div>
                <div className="flex items-center justify-between px-3 pb-2.5 pt-1">
                  <div className="flex items-center gap-1">
                    {/* File upload */}
                    <label
                      title="上传文件"
                      className="flex size-7 cursor-pointer items-center justify-center rounded-md text-slate-400 transition hover:bg-slate-100 hover:text-indigo-600 dark:text-slate-500 dark:hover:bg-slate-800 dark:hover:text-indigo-400"
                    >
                      <Paperclip size={13} />
                      <input
                        type="file"
                        multiple
                        className="hidden"
                        onChange={(e) => {
                          const files = Array.from(e.target.files || []);
                          if (files.length) {
                            setUploadedFiles((prev) => [...prev, ...files]);
                            files.forEach((f) => {
                              if (f.type.startsWith("image/")) {
                                setImagePreviews((prev) => [...prev, { name: f.name, url: URL.createObjectURL(f) }]);
                              }
                            });
                          }
                          e.target.value = "";
                        }}
                      />
                    </label>
                    {/* Image upload */}
                    <label
                      title="上传图片"
                      className="flex size-7 cursor-pointer items-center justify-center rounded-md text-slate-400 transition hover:bg-slate-100 hover:text-indigo-600 dark:text-slate-500 dark:hover:bg-slate-800 dark:hover:text-indigo-400"
                    >
                      <ImagePlus size={13} />
                      <input
                        type="file"
                        multiple
                        accept="image/*"
                        className="hidden"
                        onChange={(e) => {
                          const files = Array.from(e.target.files || []);
                          if (files.length) {
                            setUploadedFiles((prev) => [...prev, ...files]);
                            files.forEach((f) => {
                              if (f.type.startsWith("image/")) {
                                setImagePreviews((prev) => [...prev, { name: f.name, url: URL.createObjectURL(f) }]);
                              }
                            });
                          }
                          e.target.value = "";
                        }}
                      />
                    </label>

                    {/* Model picker compact */}
                    <div className="relative">
                      <button
                        onClick={() => setShowModelMenu((v) => !v)}
                        className="flex h-7 items-center gap-1 rounded-md px-1.5 text-[11px] font-medium text-slate-500 transition hover:bg-slate-100 hover:text-indigo-600 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-indigo-400"
                      >
                        <Bot size={12} />
                        <span className="max-w-[70px] truncate">
                          {selectedModel
                            ? selectedModel.split("/").pop()
                            : (defaultModel || "模型")}
                        </span>
                        <ChevronDown size={10} className="shrink-0" />
                      </button>
                      {showModelMenu && (
                        <>
                          <div className="fixed inset-0 z-40" onClick={() => setShowModelMenu(false)} />
                          <div className="absolute bottom-full left-0 z-50 mb-2 w-52 max-h-52 overflow-y-auto rounded-xl border border-slate-200/80 bg-white shadow-lg ring-1 ring-slate-100 dark:border-slate-700 dark:bg-slate-900 dark:ring-slate-800">
                            <div className="p-1.5">
                              <button
                                onClick={() => { setSelectedModel(""); setShowModelMenu(false); }}
                                className={`w-full rounded-lg px-2.5 py-1.5 text-left text-[11px] transition ${!selectedModel ? "bg-indigo-50 font-medium text-indigo-600 dark:bg-indigo-500/10 dark:text-indigo-400" : "text-slate-600 hover:bg-slate-50 dark:text-slate-300 dark:hover:bg-slate-800"}`}
                              >
                                默认模型
                              </button>
                              {availableModels.map((m) => (
                                <button
                                  key={m.model_id}
                                  onClick={() => { setSelectedModel(m.model_id); setShowModelMenu(false); }}
                                  className={`w-full rounded-lg px-2.5 py-1.5 text-left text-[11px] transition ${selectedModel === m.model_id ? "bg-indigo-50 font-medium text-indigo-600 dark:bg-indigo-500/10 dark:text-indigo-400" : "text-slate-600 hover:bg-slate-50 dark:text-slate-300 dark:hover:bg-slate-800"}`}
                                >
                                  <span className="font-medium">{m.model_name}</span>
                                  <span className="ml-1 text-[10px] text-slate-400">{m.provider_display}</span>
                                </button>
                              ))}
                            </div>
                          </div>
                        </>
                      )}
                    </div>
                  </div>

                  {/* Send */}
                  <button
                    onClick={() => {
                      if (!feedback.trim()) return;
                      void requestRevision(feedback);
                      setFeedback("");
                      setUploadedFiles([]);
                      setImagePreviews([]);
                      if (textareaRef.current) textareaRef.current.style.height = "auto";
                    }}
                    disabled={!feedback.trim() || saving || generating || generatingDesigns || generatingPrototype}
                    className="flex size-8 items-center justify-center rounded-lg bg-indigo-600 text-white transition hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-30"
                  >
                    {saving || generating ? <Loader2 size={13} className="animate-spin" /> : <Send size={13} />}
                  </button>
                </div>
              </div>
            </div>
          </section>

          {/* Right: Results panel */}
          <section className="flex min-h-0 min-w-0 flex-col rounded-2xl bg-white/70 shadow-sm ring-1 ring-slate-200/60 backdrop-blur-sm transition-all dark:bg-slate-900/70 dark:ring-slate-700/40">
            <div className="flex shrink-0 items-start gap-3 border-b border-slate-200/80 px-6 py-4 dark:border-slate-800">
              <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-indigo-50 text-indigo-600 ring-1 ring-indigo-100 dark:bg-indigo-500/10 dark:text-indigo-400 dark:ring-indigo-500/20">
                <CheckCircle2 size={15} />
              </div>
              <div className="flex-1">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <div className="text-sm font-semibold text-slate-900 dark:text-slate-100">当前结果</div>
                    <div className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                      {selectedStage.title} · {artifactHeadline}
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {latestPreviewLink && (
                      <a
                        href={latestPreviewLink}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1 rounded-lg border border-slate-200/80 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 ring-1 ring-transparent transition hover:border-indigo-200 hover:bg-indigo-50/50 hover:text-indigo-700 hover:ring-indigo-200/50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300 dark:hover:border-indigo-500/30 dark:hover:bg-indigo-500/10 dark:hover:text-indigo-300"
                      >
                        {latestPreviewLabel}
                        <ArrowUpRight size={11} />
                      </a>
                    )}
                    {executionDetailHref && (
                      <Link
                        href={executionDetailHref}
                        className="inline-flex items-center gap-1 rounded-lg border border-slate-200/80 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 ring-1 ring-transparent transition hover:border-indigo-200 hover:bg-indigo-50/50 hover:text-indigo-700 hover:ring-indigo-200/50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300 dark:hover:border-indigo-500/30 dark:hover:bg-indigo-500/10 dark:hover:text-indigo-300"
                      >
                        详细记录
                      </Link>
                    )}
                  </div>
                </div>
              </div>
            </div>

            <div className="flex flex-1 flex-col overflow-hidden p-6">
              {primaryArtifact ? (
                <div className="flex flex-1 flex-col gap-4 overflow-hidden">
                  <div className="flex-1 overflow-hidden rounded-2xl border border-slate-200/80 bg-white shadow-sm ring-1 ring-slate-100 dark:border-slate-800 dark:bg-slate-950 dark:ring-slate-800">
                    {primaryArtifact.kind === "iframe" ? (
                      <iframe
                        title={primaryArtifact.title}
                        src={primaryArtifact.url}
                        className="h-full w-full bg-white"
                      />
                    ) : (
                      <img src={primaryArtifact.url} alt={primaryArtifact.title} className="h-full w-full object-contain bg-white" />
                    )}
                  </div>
                  {readySelectedArtifacts.length > 1 && (
                    <div className="flex flex-wrap gap-2 shrink-0">
                      {readySelectedArtifacts.map((artifact) =>
                        artifact.url ? (
                          <a
                            key={`${artifact.type}-${artifact.label}`}
                            href={withAuthToken(artifact.url)}
                            target="_blank"
                            rel="noreferrer"
                            className="inline-flex items-center gap-1 rounded-lg border border-slate-200/80 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 ring-1 ring-transparent transition hover:border-indigo-200 hover:bg-indigo-50/50 hover:text-indigo-700 hover:ring-indigo-200/50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300 dark:hover:border-indigo-500/30 dark:hover:bg-indigo-500/10 dark:hover:text-indigo-300"
                          >
                            {artifact.label || artifact.type}
                            <ArrowUpRight size={10} />
                          </a>
                        ) : null
                      )}
                    </div>
                  )}
                </div>
              ) : stageDocument ? (
                <div className="flex flex-1 flex-col gap-4 overflow-y-auto">
                  <div className="rounded-2xl border border-slate-200/80 bg-slate-50/70 p-6 ring-1 ring-slate-100 dark:border-slate-800 dark:bg-slate-950/40 dark:ring-slate-800">
                    <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-900 dark:text-slate-100">
                      <span className="inline-block h-1.5 w-1.5 rounded-full bg-indigo-400" />
                      {selectedStage.title}文档
                    </div>
                    <div className="whitespace-pre-wrap text-sm leading-7 text-slate-700 dark:text-slate-300">
                      {stageDocument}
                    </div>
                  </div>
                  {stageProjectPath && (
                    <div className="rounded-2xl border border-slate-200/80 bg-white px-4 py-3 text-sm ring-1 ring-slate-100 dark:border-slate-800 dark:bg-slate-950/60 dark:ring-slate-800">
                      <div className="text-xs font-semibold uppercase tracking-[0.12em] text-slate-400">本地目录</div>
                      <div className="mt-2 break-all text-slate-700 dark:text-slate-300">{stageProjectPath}</div>
                    </div>
                  )}
                  {executionDetailHref && (
                    <Link
                      href={executionDetailHref}
                      className="inline-flex items-center gap-1 rounded-lg border border-slate-200/80 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 ring-1 ring-transparent transition hover:border-indigo-200 hover:bg-indigo-50/50 hover:text-indigo-700 hover:ring-indigo-200/50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300 dark:hover:border-indigo-500/30 dark:hover:bg-indigo-500/10 dark:hover:text-indigo-300"
                    >
                      查看详细执行记录
                    </Link>
                  )}
                </div>
              ) : (
                <div className="flex flex-1 flex-col items-center justify-center rounded-2xl border border-dashed border-slate-300/80 bg-slate-50/50 text-center ring-1 ring-slate-100 dark:border-slate-700 dark:bg-slate-950/40 dark:ring-slate-800">
                  <div className="flex size-14 items-center justify-center rounded-2xl bg-indigo-50 text-indigo-500 ring-1 ring-indigo-100 dark:bg-indigo-500/10 dark:text-indigo-400 dark:ring-indigo-500/20">
                    <Loader2 size={28} className="animate-spin" />
                  </div>
                  <div className="mt-5 text-lg font-semibold text-slate-900 dark:text-slate-100">正在生成成果</div>
                  <div className="mt-2 max-w-md text-sm leading-6 text-slate-500 dark:text-slate-400">
                    左侧显示进展，右侧会在结果产出后直接替换成页面、设计稿或文档。
                  </div>
                </div>
              )}
            </div>
          </section>
        </div>
      </main>
    </div>
  );
}
