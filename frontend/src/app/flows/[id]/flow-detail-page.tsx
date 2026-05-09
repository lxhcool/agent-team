"use client";

import Link from "next/link";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "next/navigation";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import {
  ArrowLeft,
  Brain,
  Check,
  CheckCircle2,
  Copy,
  FileDown,
  FileText,
  FolderKanban,
  GitBranch,
  Layers3,
  Loader2,
  Paperclip,
  Send,
  Sparkles,
  User,
  Wifi,
  Wrench,
} from "lucide-react";

import { TopNav } from "../../components/topnav";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useAvailableModels } from "@/hooks/use-available-models";
import { cn } from "@/lib/utils";

type StageKey =
  | "requirements"
  | "product"
  | "ui_direction"
  | "prototype"
  | "technical"
  | "development"
  | "acceptance"
  | "deployment";

type StageStatus =
  | "draft"
  | "awaiting_confirmation"
  | "approved"
  | "revision_requested"
  | "skipped";

type RecommendationArtifact = {
  type?: string;
  status?: string;
  label?: string;
  artifact_id?: string;
  url?: string;
  mime_type?: string;
  created_at?: string;
};

type Recommendation = {
  summary?: string;
  recommended_action?: string;
  focus?: string[];
  artifacts?: RecommendationArtifact[];
  stage_runtime?: {
    ready_to_finalize?: boolean;
    readiness_blockers?: string[];
    readiness_message_id?: string | null;
    evaluated_at?: string | null;
  };
};

type FlowStage = {
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
  updated_at?: string | null;
};

type Flow = {
  id: string;
  name: string;
  description: string | null;
  target_platform: string;
  current_stage: StageKey;
  stage_total: number;
  stage_approved: number;
  stages: FlowStage[];
};

type StageMessage = {
  id: string;
  stage_id: string;
  role: "user" | "assistant";
  kind: "chat" | "conclusion";
  content: string;
  artifact_id: string | null;
  artifact_url: string | null;
  created_at: string | null;
};

type StageChatPayload = {
  stage: FlowStage;
  messages: StageMessage[];
};

type StageStreamState = {
  messageId: string;
  content: string;
  reasoning: string;
  kind: "chat" | "conclusion";
};

type StageStreamCompletePayload = {
  stage: FlowStage;
  message: StageMessage;
  reasoning?: string;
  model?: string | null;
  provider?: string | null;
};

type AssistantRuntimeSettings = {
  model: string;
  reasoning_effort: "default" | "low" | "medium" | "high";
  enable_web_search: boolean;
  enable_stage_skills: boolean;
};

type AssistantRuntimeSettingsPayload = {
  settings: {
    model?: string;
    provider?: string;
    reasoning_effort?: "default" | "low" | "medium" | "high";
    enable_web_search: boolean;
    enable_stage_skills: boolean;
  };
};

const MAIN_STAGE_ORDER: StageKey[] = [
  "requirements",
  "product",
  "ui_direction",
  "technical",
  "deployment",
];

const STAGE_META: Record<
  StageKey,
  {
    label: string;
    short: string;
    description: string;
    deliverable: string;
    icon: React.ComponentType<{ size?: number; className?: string }>;
  }
> = {
  requirements: {
    label: "需求确认",
    short: "01",
    description: "先对齐这到底是个什么产品，再确认主要用户、核心用途和边界。",
    deliverable: "需求确认文档",
    icon: Sparkles,
  },
  product: {
    label: "方案设计",
    short: "02",
    description: "先整理功能模块和模块关系，再落到页面结构和主要流程。",
    deliverable: "方案设计文档",
    icon: Layers3,
  },
  ui_direction: {
    label: "细节确认",
    short: "03",
    description: "锁定角色权限、状态流转、异常处理、数据口径和关键边界。",
    deliverable: "细节确认文档",
    icon: GitBranch,
  },
  prototype: {
    label: "补充材料",
    short: "03+",
    description: "按需补充页面草图、参考图或其他辅助材料。",
    deliverable: "补充材料",
    icon: FileText,
  },
  technical: {
    label: "开发方案",
    short: "04",
    description: "整理开发可接手的实现方案，包括模块拆分、接口数据和依赖风险。",
    deliverable: "开发方案文档",
    icon: GitBranch,
  },
  development: {
    label: "实现准备",
    short: "05",
    description: "整理给本地 IDE 接手的说明和拆分建议。",
    deliverable: "实现准备文档",
    icon: FileText,
  },
  acceptance: {
    label: "验收口径",
    short: "06",
    description: "明确怎么判断当前版本算完成。",
    deliverable: "验收口径文档",
    icon: CheckCircle2,
  },
  deployment: {
    label: "交付清单",
    short: "05",
    description: "整理全部已确认文档，支持单独下载和整体打包下载。",
    deliverable: "交付清单",
    icon: FolderKanban,
  },
};

const STATUS_LABEL: Record<StageStatus, string> = {
  draft: "待开始",
  awaiting_confirmation: "待确认",
  approved: "已确认",
  revision_requested: "需调整",
  skipped: "已跳过",
};

const STATUS_BADGE_VARIANT: Record<StageStatus, "outline" | "info" | "success" | "warning"> = {
  draft: "outline",
  awaiting_confirmation: "info",
  approved: "success",
  revision_requested: "warning",
  skipped: "outline",
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

function withAuthToken(url: string | null) {
  if (!url || typeof window === "undefined") return url || "";
  const token = localStorage.getItem("agent_team_token");
  if (!token) return url;
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}token=${encodeURIComponent(token)}`;
}

function buildArtifactDownloadUrl(artifactId: string | null | undefined, fallbackUrl: string | null | undefined) {
  if (artifactId) {
    return withAuthToken(`/api/artifacts/${artifactId}/download`);
  }
  return withAuthToken(fallbackUrl || "");
}

function buildAllArtifactsDownloadUrl(flowId: string) {
  return withAuthToken(`/api/flows/${flowId}/artifacts/download-all`);
}

function buildStreamApiUrl(path: string) {
  const backendPort = process.env.NEXT_PUBLIC_BACKEND_PORT || "8200";
  return `http://127.0.0.1:${backendPort}${path}`;
}

function buildFallbackMessages(stage: FlowStage): StageMessage[] {
  const summary = stage.content?.trim() || stage.recommendation?.summary?.trim();
  if (!summary) return [];
  const artifact = (stage.recommendation?.artifacts || []).find((item) => item.url);
  return [
    {
      id: `fallback-${stage.id}`,
      stage_id: stage.id,
      role: "assistant",
      kind: artifact ? "conclusion" : "chat",
      content: summary,
      artifact_id: artifact?.artifact_id || null,
      artifact_url: artifact?.url || null,
      created_at: stage.approved_at,
    },
  ];
}

function buildUserDraftMessage(stage: FlowStage, draft: string): StageMessage {
  return {
    id: `temp-user-${stage.id}-${Date.now()}`,
    stage_id: stage.id,
    role: "user",
    kind: "chat",
    content: draft,
    artifact_id: null,
    artifact_url: null,
    created_at: new Date().toISOString(),
  };
}

function buildStreamingAssistantState(stage: FlowStage, kind: StageStreamState["kind"] = "chat"): StageStreamState {
  return {
    messageId: `temp-assistant-${stage.id}-${Date.now()}`,
    content: "",
    reasoning: "",
    kind,
  };
}

function appendStageMessage(messages: StageMessage[], message: StageMessage) {
  if (messages.some((item) => item.id === message.id)) {
    return messages;
  }
  return [...messages, message];
}

function buildPartialAssistantMessage(
  stage: FlowStage,
  stream: StageStreamState,
  content: string,
): StageMessage {
  return {
    id: `${stream.messageId}-partial`,
    stage_id: stage.id,
    role: "assistant",
    kind: stream.kind,
    content,
    artifact_id: null,
    artifact_url: null,
    created_at: new Date().toISOString(),
  };
}

function parseSseChunk(chunk: string) {
  const lines = chunk.split("\n");
  let event = "message";
  const data: string[] = [];
  for (const line of lines) {
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
      continue;
    }
    if (line.startsWith("data:")) {
      data.push(line.slice(5).replace(/^ /, ""));
    }
  }
  return { event, data: data.join("\n") };
}

function normalizeSseBuffer(value: string) {
  return value.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
}

function ThinkingDots() {
  return (
    <span className="inline-flex items-center gap-1">
      {[0, 1, 2].map((index) => (
        <span
          key={index}
          className="size-1.5 rounded-full bg-current opacity-70 animate-pulse"
          style={{ animationDelay: `${index * 180}ms` }}
        />
      ))}
    </span>
  );
}

const STREAM_RETRY_LIMIT = 2;
const STREAM_RETRY_DELAY_MS = 800;
const activeBootstrapRequests = new Set<string>();
const DEFAULT_ASSISTANT_SETTINGS: AssistantRuntimeSettings = {
  model: "",
  reasoning_effort: "default",
  enable_web_search: false,
  enable_stage_skills: false,
};
const REASONING_OPTIONS: Array<{
  value: AssistantRuntimeSettings["reasoning_effort"];
  label: string;
}> = [
  { value: "default", label: "默认" },
  { value: "low", label: "低" },
  { value: "medium", label: "中" },
  { value: "high", label: "高" },
];

function buildBootstrapRequestKey(flowId: string, stageKey: StageKey) {
  return `${flowId}:${stageKey}`;
}

function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function buildRuntimeSettingsPayload(
  settings: AssistantRuntimeSettings,
  providerByModelId: Map<string, string>,
): AssistantRuntimeSettingsPayload {
  const provider = providerByModelId.get(settings.model);
  return {
    settings: {
      ...(settings.model ? { model: settings.model } : {}),
      ...(provider ? { provider } : {}),
      reasoning_effort: settings.reasoning_effort,
      enable_web_search: settings.enable_web_search,
      enable_stage_skills: settings.enable_stage_skills,
    },
  };
}

function isRetriableStreamError(error: unknown) {
  const message =
    error instanceof Error
      ? error.message
      : typeof error === "string"
        ? error
        : "";
  if (!message) return false;
  return [
    "流式响应提前结束",
    "socket hang up",
    "ECONNRESET",
    "Failed to fetch",
    "fetch failed",
    "NetworkError",
  ].some((keyword) => message.includes(keyword));
}

export default function FlowDetailPage() {
  const params = useParams<{ id: string }>();
  const flowId = params.id;
  const { models, defaultModel, loading: modelsLoading } = useAvailableModels();
  const [flow, setFlow] = useState<Flow | null>(null);
  const [selectedKey, setSelectedKey] = useState<StageKey | null>(null);
  const [messagesByStage, setMessagesByStage] = useState<Record<string, StageMessage[]>>({});
  const [draftByStage, setDraftByStage] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [approving, setApproving] = useState(false);
  const [revising, setRevising] = useState(false);
  const [error, setError] = useState("");
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null);
  const [streamByStage, setStreamByStage] = useState<Record<string, StageStreamState | null>>({});
  const [assistantSettings, setAssistantSettings] = useState<AssistantRuntimeSettings>(DEFAULT_ASSISTANT_SETTINGS);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const loadedStageKeysRef = useRef<Set<string>>(new Set());
  const bootstrappingStageKeysRef = useRef<Set<string>>(new Set());
  const providerByModelId = useMemo(
    () => new Map(models.map((item) => [item.model_id, item.provider])),
    [models],
  );

  useEffect(() => {
    if (!models.length) return;
    setAssistantSettings((current) => {
      if (current.model && providerByModelId.has(current.model)) {
        return current;
      }
      const fallbackModel = defaultModel && providerByModelId.has(defaultModel) ? defaultModel : models[0]?.model_id || "";
      if (!fallbackModel || fallbackModel === current.model) {
        return current;
      }
      return { ...current, model: fallbackModel };
    });
  }, [defaultModel, models, providerByModelId]);

  const visibleStages = useMemo(() => {
    if (!flow?.stages?.length) return [];
    const stageMap = new Map(flow.stages.map((stage) => [stage.stage_key, stage]));
    return MAIN_STAGE_ORDER.map((key) => stageMap.get(key)).filter(Boolean) as FlowStage[];
  }, [flow]);

  const selectedStage = useMemo(() => {
    if (!visibleStages.length) return null;
    return visibleStages.find((stage) => stage.stage_key === selectedKey) || visibleStages[0];
  }, [selectedKey, visibleStages]);

  const currentMeta = selectedStage ? STAGE_META[selectedStage.stage_key] : null;
  const liveStreamState = selectedStage ? streamByStage[selectedStage.stage_key] || null : null;
  const currentStageStoredMessages = selectedStage ? messagesByStage[selectedStage.stage_key] : undefined;
  const shouldUseFallbackMessages = Boolean(
    selectedStage &&
      currentStageStoredMessages === undefined &&
      !loadedStageKeysRef.current.has(selectedStage.stage_key) &&
      !bootstrappingStageKeysRef.current.has(selectedStage.stage_key) &&
      !liveStreamState,
  );
  const currentStageMessages = selectedStage
    ? (currentStageStoredMessages !== undefined
        ? currentStageStoredMessages
        : shouldUseFallbackMessages
          ? buildFallbackMessages(selectedStage)
          : [])
    : [];
  const lastUserMessageIndex = currentStageMessages.reduce(
    (latest, message, index) => (message.role === "user" ? index : latest),
    -1,
  );
  const lastConclusionIndex = currentStageMessages.reduce(
    (latest, message, index) => (message.kind === "conclusion" ? index : latest),
    -1,
  );
  const hasStageConclusion =
    selectedStage?.status !== "revision_requested" &&
    lastConclusionIndex !== -1 &&
    lastConclusionIndex > lastUserMessageIndex;
  const stageReadyToFinalize = Boolean(selectedStage?.recommendation?.stage_runtime?.ready_to_finalize);
  const stageReadinessBlockers = selectedStage?.recommendation?.stage_runtime?.readiness_blockers || [];

  const stageProgress = visibleStages.length
    ? Math.round((visibleStages.filter((stage) => stage.status === "approved").length / visibleStages.length) * 100)
    : 0;

  const currentStep = selectedStage
    ? visibleStages.findIndex((stage) => stage.stage_key === selectedStage.stage_key) + 1
    : 1;
  const isBlockedByUpstream = selectedStage
    ? visibleStages.some(
        (stage) =>
          stage.order < selectedStage.order &&
          stage.status !== "approved" &&
          stage.status !== "skipped",
      )
    : false;

  const loadFlow = async () => {
    setLoading(true);
    setError("");
    try {
      const res = await fetch(`/api/flows/${flowId}`);
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "获取流程失败");
      }
      const data = (await res.json()) as Flow;
      setFlow(data);
      setSelectedKey((current) => current || data.current_stage);
    } catch (err: any) {
      setError(err.message || "获取流程失败");
    } finally {
      setLoading(false);
    }
  };

  const updateStageInFlow = (updatedStage: FlowStage) => {
    setFlow((current) => {
      if (!current) return current;
      const stages = current.stages.map((stage) => (stage.id === updatedStage.id ? updatedStage : stage));
      const approvedCount = stages.filter((stage) => stage.status === "approved").length;
      return {
        ...current,
        stages,
        stage_approved: approvedCount,
      };
    });
  };

  const loadStageMessages = async (stageKey: StageKey, force = false) => {
    if (!force && loadedStageKeysRef.current.has(stageKey)) return;
    setMessagesLoading(true);
    setError("");
    try {
      const res = await fetch(`/api/flows/${flowId}/stages/${stageKey}/messages`);
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "获取阶段对话失败");
      }
      const data = (await res.json()) as StageChatPayload;
      loadedStageKeysRef.current.add(stageKey);
      updateStageInFlow(data.stage);
      setMessagesByStage((current) => ({ ...current, [stageKey]: data.messages }));
    } catch (err: any) {
      setError(err.message || "获取阶段对话失败");
    } finally {
      setMessagesLoading(false);
    }
  };

  const consumeStageStream = async (
    stageKey: StageKey,
    endpoint: string,
    draft?: string,
    streamKind: StageStreamState["kind"] = "chat",
    runtimeSettings: AssistantRuntimeSettings = assistantSettings,
  ) => {
    const stage = visibleStages.find((item) => item.stage_key === stageKey);
    if (!stage) return;

    const userMessage = draft ? buildUserDraftMessage(stage, draft) : null;
    const streamingState = buildStreamingAssistantState(stage, streamKind);

    if (userMessage) {
      setMessagesByStage((current) => ({
        ...current,
        [stageKey]: [...(current[stageKey] || []), userMessage],
      }));
      setDraftByStage((current) => ({ ...current, [stageKey]: "" }));
    }

    setStreamByStage((current) => ({
      ...current,
      [stageKey]: streamingState,
    }));

    let latestStreamContent = "";
    try {
      let lastError: unknown = null;

      for (let attempt = 0; attempt <= STREAM_RETRY_LIMIT; attempt += 1) {
        try {
          let didRestartThisAttempt = attempt > 0;
          setStreamByStage((current) => ({
            ...current,
            [stageKey]: {
              ...(current[stageKey] || buildStreamingAssistantState(stage, streamKind)),
              reasoning:
                attempt > 0 ? `连接中断，正在重试 ${attempt}/${STREAM_RETRY_LIMIT}...` : "",
              kind: streamKind,
            },
          }));

          const token = typeof window !== "undefined" ? localStorage.getItem("agent_team_token") : null;
          const headers = new Headers({ "Content-Type": "application/json" });
          if (token) {
            headers.set("Authorization", `Bearer ${token}`);
          }

          const res = await fetch(buildStreamApiUrl(endpoint), {
            method: "POST",
            headers,
            body: JSON.stringify(
              draft
                ? {
                    content: draft,
                    ...buildRuntimeSettingsPayload(runtimeSettings, providerByModelId),
                  }
                : buildRuntimeSettingsPayload(runtimeSettings, providerByModelId),
            ),
          });
          if (!res.ok) {
            const data = await res.json().catch(() => ({}));
            throw new Error(data.detail || "发送消息失败");
          }
          if (!res.body) {
            throw new Error("当前响应不支持流式读取");
          }

          const reader = res.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";
          let sawComplete = false;

          while (true) {
            const { value, done } = await reader.read();
            buffer = normalizeSseBuffer(buffer + decoder.decode(value || new Uint8Array(), { stream: true }));
            if (done) {
              buffer = normalizeSseBuffer(buffer + decoder.decode());
            }

            let separatorIndex = buffer.indexOf("\n\n");
            while (separatorIndex >= 0) {
              const rawEvent = buffer.slice(0, separatorIndex);
              buffer = buffer.slice(separatorIndex + 2);
              if (rawEvent.trim()) {
                const parsed = parseSseChunk(rawEvent);
                if (parsed.event === "content") {
                  setStreamByStage((current) => {
                    const active = current[stageKey];
                    if (!active) return current;
                    return {
                      ...current,
                      [stageKey]: {
                        ...active,
                        content: didRestartThisAttempt ? parsed.data : active.content + parsed.data,
                        reasoning: "",
                      },
                    };
                  });
                  latestStreamContent = didRestartThisAttempt ? parsed.data : latestStreamContent + parsed.data;
                  didRestartThisAttempt = false;
                } else if (parsed.event === "complete") {
                  sawComplete = true;
                  const payload = JSON.parse(parsed.data) as StageStreamCompletePayload;
                  loadedStageKeysRef.current.add(stageKey);
                  updateStageInFlow(payload.stage);
                  setMessagesByStage((current) => ({
                    ...current,
                    [stageKey]: appendStageMessage(current[stageKey] || [], payload.message),
                  }));
                  setStreamByStage((current) => ({ ...current, [stageKey]: null }));
                } else if (parsed.event === "error") {
                  throw new Error(parsed.data || "发送消息失败");
                }
              }
              separatorIndex = buffer.indexOf("\n\n");
            }

            if (done) {
              break;
            }
          }

          const trailingEvent = buffer.trim();
          if (trailingEvent) {
            const parsed = parseSseChunk(trailingEvent);
            if (parsed.event === "content") {
              setStreamByStage((current) => {
                const active = current[stageKey];
                if (!active) return current;
                return {
                  ...current,
                  [stageKey]: {
                    ...active,
                    content: didRestartThisAttempt ? parsed.data : active.content + parsed.data,
                    reasoning: "",
                  },
                };
              });
              latestStreamContent = didRestartThisAttempt ? parsed.data : latestStreamContent + parsed.data;
              didRestartThisAttempt = false;
            } else if (parsed.event === "complete") {
              sawComplete = true;
              const payload = JSON.parse(parsed.data) as StageStreamCompletePayload;
              loadedStageKeysRef.current.add(stageKey);
              updateStageInFlow(payload.stage);
              setMessagesByStage((current) => ({
                ...current,
                [stageKey]: appendStageMessage(current[stageKey] || [], payload.message),
              }));
              setStreamByStage((current) => ({ ...current, [stageKey]: null }));
            } else if (parsed.event === "error") {
              throw new Error(parsed.data || "发送消息失败");
            }
          }
          if (!sawComplete) {
            throw new Error("流式响应提前结束，请重试");
          }
          return;
        } catch (err) {
          lastError = err;
          if (!isRetriableStreamError(err) || attempt >= STREAM_RETRY_LIMIT) {
            throw err;
          }
          await sleep(STREAM_RETRY_DELAY_MS);
        }
      }
      throw lastError instanceof Error ? lastError : new Error("发送消息失败");
    } catch (err) {
      if (userMessage) {
        setMessagesByStage((current) => ({
          ...current,
          [stageKey]: (current[stageKey] || []).filter((message) => message.id !== userMessage.id),
        }));
        setDraftByStage((current) => ({ ...current, [stageKey]: draft || "" }));
      }
      const partialContent = latestStreamContent.trim();
      if (partialContent) {
        setMessagesByStage((current) => ({
          ...current,
          [stageKey]: appendStageMessage(
            current[stageKey] || [],
            buildPartialAssistantMessage(stage, streamingState, partialContent),
          ),
        }));
      }
      setStreamByStage((current) => ({ ...current, [stageKey]: null }));
      throw err;
    }
  };

  useEffect(() => {
    void loadFlow();
  }, [flowId]);

  useEffect(() => {
    if (!selectedStage) return;
    void loadStageMessages(selectedStage.stage_key);
  }, [selectedStage?.stage_key]);

  useEffect(() => {
    if (!selectedStage || messagesLoading || sending) return;
    const stageKey = selectedStage.stage_key;
    if (!loadedStageKeysRef.current.has(stageKey)) {
      return;
    }
    const bootstrapRequestKey = buildBootstrapRequestKey(flowId, stageKey);
    const stageMessages = messagesByStage[stageKey] || [];
    const hasAssistant = stageMessages.some((item) => item.role === "assistant" && item.content.trim());
    const isCurrentStage = flow?.current_stage === stageKey;
    if (
      hasAssistant ||
      !isCurrentStage ||
      bootstrappingStageKeysRef.current.has(stageKey) ||
      activeBootstrapRequests.has(bootstrapRequestKey) ||
      liveStreamState
    ) {
      return;
    }

    bootstrappingStageKeysRef.current.add(stageKey);
    activeBootstrapRequests.add(bootstrapRequestKey);
    setSending(true);
    setError("");
    void consumeStageStream(
      stageKey,
      `/api/flows/${flowId}/stages/${stageKey}/bootstrap-stream`,
      undefined,
      "chat",
      assistantSettings,
    )
      .catch(async (err: any) => {
        if (err?.message === "当前阶段已经有生成内容") {
          await loadStageMessages(stageKey, true);
          return;
        }
        setError(err.message || "阶段启动失败");
      })
      .finally(() => {
        bootstrappingStageKeysRef.current.delete(stageKey);
        activeBootstrapRequests.delete(bootstrapRequestKey);
        setSending(false);
      });
  }, [
    selectedStage?.stage_key,
    selectedStage?.id,
    currentStageStoredMessages?.length,
    flow?.current_stage,
    messagesLoading,
    sending,
    liveStreamState?.messageId,
    flowId,
    assistantSettings,
  ]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [selectedStage?.stage_key, currentStageMessages.length, liveStreamState?.content, messagesLoading]);

  const submitMessage = async (event: FormEvent) => {
    event.preventDefault();
    if (!selectedStage || sending) return;
    if (isBlockedByUpstream) {
      setError("请先完成前置阶段，再继续当前阶段");
      return;
    }
    if (selectedStage.status === "approved") {
      setError("当前阶段已确认，请先发起调整，再继续补充");
      return;
    }
    const draft = (draftByStage[selectedStage.stage_key] || "").trim();
    if (!draft) return;

    setSending(true);
    setError("");
    const stageKey = selectedStage.stage_key;
    try {
      await consumeStageStream(
        stageKey,
        `/api/flows/${flowId}/stages/${stageKey}/messages/stream`,
        draft,
        "chat",
        assistantSettings,
      );
    } catch (err: any) {
      setError(err.message || "发送消息失败");
    } finally {
      setSending(false);
    }
  };

  const approveStage = async () => {
    if (!selectedStage || approving) return;
    if (isBlockedByUpstream) {
      setError("请先完成前置阶段，再确认当前阶段");
      return;
    }
    if (!hasStageConclusion && !stageReadyToFinalize) {
      setError(stageReadinessBlockers[0] || "当前阶段还没收完整，先把这轮关键点确认掉");
      return;
    }
    setApproving(true);
    setError("");
    const stageKey = selectedStage.stage_key;
    try {
      if (!hasStageConclusion) {
        await consumeStageStream(
          stageKey,
          `/api/flows/${flowId}/stages/${stageKey}/finalize-stream`,
          undefined,
          "conclusion",
          assistantSettings,
        );
        return;
      }

      const res = await fetch(`/api/flows/${flowId}/stages/${stageKey}/approve`, {
        method: "POST",
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "确认当前阶段失败");
      }
      const data = (await res.json()) as Flow;
      setFlow(data);
      setSelectedKey(data.current_stage);
      if (data.current_stage) {
        loadedStageKeysRef.current.delete(data.current_stage);
      }
    } catch (err: any) {
      setError(err.message || (hasStageConclusion ? "确认当前阶段失败" : "生成阶段结论失败"));
    } finally {
      setApproving(false);
    }
  };

  const requestRevision = async () => {
    if (!selectedStage || revising) return;
    if (isBlockedByUpstream) {
      setError("当前阶段受前置阶段调整影响，请先回到前置阶段处理");
      return;
    }
    const feedback = (draftByStage[selectedStage.stage_key] || "").trim();
    if (!feedback) {
      setError("请先写清要调整的内容，再发起调整");
      return;
    }

    setRevising(true);
    setError("");
    const stageKey = selectedStage.stage_key;
    try {
      const res = await fetch(`/api/flows/${flowId}/stages/${stageKey}/request-revision`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ feedback }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "发起调整失败");
      }
      const data = (await res.json()) as Flow;
      setFlow(data);
      setSelectedKey(stageKey);
      setDraftByStage((current) => ({ ...current, [stageKey]: "" }));
      loadedStageKeysRef.current.delete(stageKey);
      await loadStageMessages(stageKey, true);
    } catch (err: any) {
      setError(err.message || "发起调整失败");
    } finally {
      setRevising(false);
    }
  };

  const copyMessage = async (message: StageMessage) => {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopiedMessageId(message.id);
      window.setTimeout(() => {
        setCopiedMessageId((current) => (current === message.id ? null : current));
      }, 1500);
    } catch {
      setError("复制失败");
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-white dark:bg-slate-950">
        <TopNav />
        <main className="flex h-screen items-center justify-center pt-14 text-slate-400">
          <Loader2 className="animate-spin" />
        </main>
      </div>
    );
  }

  if (!flow || !selectedStage || !currentMeta) {
    return (
      <div className="min-h-screen bg-white dark:bg-slate-950">
        <TopNav />
        <main className="mx-auto max-w-4xl px-6 pt-28">
          <Link href="/" className="inline-flex items-center gap-2 text-sm text-slate-500 hover:text-sky-600">
            <ArrowLeft size={16} />
            返回首页
          </Link>
          <div className="mt-8 rounded-2xl border border-red-200 bg-red-50 p-5 text-red-600 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-300">
            {error || "流程不存在"}
          </div>
        </main>
      </div>
    );
  }

  const focus = selectedStage.recommendation?.focus || [];
  const draft = draftByStage[selectedStage.stage_key] || "";
  const displayMessages = liveStreamState
    ? [
        ...currentStageMessages,
        {
          id: liveStreamState.messageId,
          stage_id: selectedStage.id,
          role: "assistant" as const,
          kind: liveStreamState.kind,
          content: liveStreamState.content,
          artifact_id: null,
          artifact_url: null,
          created_at: new Date().toISOString(),
        },
      ]
    : currentStageMessages;
  const artifactGroups = visibleStages
    .map((stage) => ({
      stage,
      artifacts: (stage.recommendation?.artifacts || []).filter(
        (artifact) => artifact.url || artifact.artifact_id,
      ),
    }))
    .filter(
      (group) =>
        group.artifacts.length > 0 || group.stage.stage_key === selectedStage.stage_key,
    )
    .sort((a, b) => {
      if (a.stage.stage_key === selectedStage.stage_key) return -1;
      if (b.stage.stage_key === selectedStage.stage_key) return 1;
      return a.stage.order - b.stage.order;
    });
  const currentStageArtifacts =
    artifactGroups.find((group) => group.stage.stage_key === selectedStage.stage_key)?.artifacts ||
    [];
  const totalArtifacts = artifactGroups.reduce((sum, group) => sum + group.artifacts.length, 0);
  const latestArtifactUpdatedAt = artifactGroups
    .flatMap((group) => group.artifacts)
    .map((artifact) => artifact.created_at)
    .filter(Boolean)
    .sort()
    .at(-1);

  return (
    <div className="min-h-screen bg-slate-100 text-slate-950 dark:bg-slate-950 dark:text-slate-50">
      <TopNav />
      <main className="px-4 pb-6 pt-20 lg:px-6 xl:px-8">
        <div className="mb-4 flex items-center justify-between gap-4">
          <Link
            href="/flows"
            className={cn(
              buttonVariants({ variant: "outline" }),
              "h-auto rounded-xl border-slate-200 bg-white px-3.5 py-2 text-sm text-slate-700 hover:border-sky-200 hover:text-sky-700 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-200",
            )}
          >
            <ArrowLeft size={16} />
            返回流程列表
          </Link>
          <div className="hidden items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs text-slate-500 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-400 md:flex">
            <span>工作区</span>
            <span className="text-slate-300 dark:text-slate-600">/</span>
            <span>{currentMeta.label}</span>
            <span className="text-slate-300 dark:text-slate-600">/</span>
            <span>{currentMeta.deliverable}</span>
          </div>
        </div>

        {error && (
          <div className="mb-4 rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-600 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-300">
            {error}
          </div>
        )}

        <section className="mb-4 rounded-[28px] border border-slate-200 bg-white px-5 py-5 shadow-[0_10px_35px_rgba(15,23,42,0.05)] dark:border-slate-800 dark:bg-slate-900">
          <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
            <div className="min-w-0">
              <div className="mb-2 flex flex-wrap items-center gap-2 text-[11px] font-medium uppercase tracking-[0.14em] text-slate-400 dark:text-slate-500">
                <span>Flow Workspace</span>
                <span className="text-slate-300 dark:text-slate-600">·</span>
                <span>{flow.target_platform}</span>
              </div>
              <h1 className="text-2xl font-semibold tracking-tight text-slate-950 dark:text-slate-50">
                {flow.name}
              </h1>
              <p className="mt-2 max-w-4xl text-sm leading-6 text-slate-500 dark:text-slate-400">
                {flow.description || "这条流程还没有补充详细背景。"}
              </p>
            </div>
            <div className="grid gap-3 sm:grid-cols-3 xl:min-w-[420px]">
              <div className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 dark:border-slate-800 dark:bg-slate-950/60">
                <div className="text-[11px] font-medium uppercase tracking-[0.12em] text-slate-400 dark:text-slate-500">
                  进度
                </div>
                <div className="mt-2 text-xl font-semibold text-slate-950 dark:text-slate-50">
                  {stageProgress}%
                </div>
                <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800">
                  <div className="h-full rounded-full bg-sky-500 transition-all" style={{ width: `${stageProgress}%` }} />
                </div>
              </div>
              <div className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 dark:border-slate-800 dark:bg-slate-950/60">
                <div className="text-[11px] font-medium uppercase tracking-[0.12em] text-slate-400 dark:text-slate-500">
                  已确认阶段
                </div>
                <div className="mt-2 text-xl font-semibold text-slate-950 dark:text-slate-50">
                  {flow.stage_approved}/{visibleStages.length}
                </div>
                <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                  当前位于 {currentMeta.label}
                </div>
              </div>
              <div className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 dark:border-slate-800 dark:bg-slate-950/60">
                <div className="text-[11px] font-medium uppercase tracking-[0.12em] text-slate-400 dark:text-slate-500">
                  产物总数
                </div>
                <div className="mt-2 text-xl font-semibold text-slate-950 dark:text-slate-50">
                  {totalArtifacts}
                </div>
                <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                  {latestArtifactUpdatedAt ? `最近更新 ${formatDate(latestArtifactUpdatedAt)}` : "还没有生成正式产物"}
                </div>
              </div>
            </div>
          </div>
        </section>

        <div className="grid gap-4 xl:grid-cols-[280px_minmax(0,1fr)_360px]">
          <aside className="xl:sticky xl:top-20 xl:h-[calc(100vh-6rem)]">
            <div className="flex h-full flex-col gap-4">
              <Card className="rounded-[26px] border-slate-200 bg-white shadow-[0_10px_30px_rgba(15,23,42,0.04)] dark:border-slate-800 dark:bg-slate-900">
                <CardHeader className="px-4 pb-0 pt-4">
                  <div className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                    流程导航
                  </div>
                  <div className="text-xs leading-5 text-slate-500 dark:text-slate-400">
                    左侧看阶段推进，中间处理当前阶段，右侧查看全流程产物。
                  </div>
                </CardHeader>
                <CardContent className="p-3 pt-3">
                  <div className="space-y-1.5">
                    {visibleStages.map((stage, index) => {
                      const meta = STAGE_META[stage.stage_key];
                      const active = stage.stage_key === selectedStage.stage_key;
                      const approved = stage.status === "approved";
                      const Icon = meta.icon;
                      return (
                        <Button
                          variant="ghost"
                          key={stage.id}
                          onClick={() => setSelectedKey(stage.stage_key)}
                          className={cn(
                            "group relative h-auto w-full justify-start rounded-2xl border px-3 py-3 text-left whitespace-normal transition",
                            active
                              ? "border-sky-200 bg-sky-50 text-slate-950 shadow-sm dark:border-sky-500/20 dark:bg-sky-500/10 dark:text-slate-50"
                              : "border-transparent bg-white text-slate-700 hover:border-slate-200 hover:bg-slate-50 dark:bg-slate-900 dark:text-slate-200 dark:hover:border-slate-700 dark:hover:bg-slate-800/70",
                          )}
                        >
                          <div className="flex items-start gap-3">
                            <div
                              className={cn(
                                "mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-xl border text-xs font-semibold",
                                approved
                                  ? "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-400/20 dark:bg-emerald-500/10 dark:text-emerald-300"
                                  : active
                                    ? "border-sky-200 bg-white text-sky-700 dark:border-sky-400/20 dark:bg-slate-950 dark:text-sky-300"
                                    : "border-slate-200 bg-slate-50 text-slate-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-400",
                              )}
                            >
                              {approved ? <Check size={15} /> : String(index + 1).padStart(2, "0")}
                            </div>
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center justify-between gap-2">
                                <div className="truncate text-sm font-semibold">{meta.label}</div>
                                <Badge
                                  variant={STATUS_BADGE_VARIANT[stage.status]}
                                  className="px-2 py-0.5 text-[10px]"
                                >
                                  {STATUS_LABEL[stage.status]}
                                </Badge>
                              </div>
                              <div className="mt-1 text-xs leading-5 text-slate-500 dark:text-slate-400">
                                {meta.deliverable}
                              </div>
                              <div className="mt-2 flex items-center gap-2 text-[11px] text-slate-400 dark:text-slate-500">
                                <Icon size={13} className={active ? "text-sky-500" : ""} />
                                <span className="truncate">{meta.description}</span>
                              </div>
                            </div>
                          </div>
                        </Button>
                      );
                    })}
                  </div>
                </CardContent>
              </Card>

              <Card className="rounded-[26px] border-slate-200 bg-white shadow-[0_10px_30px_rgba(15,23,42,0.04)] dark:border-slate-800 dark:bg-slate-900">
                <CardContent className="p-4">
                  <div className="mb-3 flex items-center justify-between">
                    <div className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                      当前阶段
                    </div>
                    <Badge variant={STATUS_BADGE_VARIANT[selectedStage.status]} className="px-2.5 py-1 text-[11px]">
                      {STATUS_LABEL[selectedStage.status]}
                    </Badge>
                  </div>
                  <div className="text-sm font-medium text-slate-900 dark:text-slate-100">
                    {currentMeta.label}
                  </div>
                  <div className="mt-1 text-xs leading-5 text-slate-500 dark:text-slate-400">
                    {currentMeta.description}
                  </div>
                  <div className="mt-4 rounded-2xl border border-slate-200 bg-slate-50 px-3 py-3 text-xs leading-5 text-slate-500 dark:border-slate-800 dark:bg-slate-950/60 dark:text-slate-400">
                    {isBlockedByUpstream
                      ? "当前阶段被前置阶段卡住，先完成上游调整。"
                      : stageReadyToFinalize
                        ? "这一阶段已经具备收口条件。"
                        : "当前还在对话推进中，建议继续补充关键点。"}
                  </div>
                  {!stageReadyToFinalize && stageReadinessBlockers.length > 0 ? (
                    <div className="mt-3 space-y-2">
                      {stageReadinessBlockers.slice(0, 2).map((blocker, index) => (
                        <div
                          key={`${selectedStage.id}-blocker-${index}`}
                          className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-700 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-200"
                        >
                          {blocker}
                        </div>
                      ))}
                    </div>
                  ) : null}
                </CardContent>
              </Card>
            </div>
          </aside>

          <Card className="min-w-0 rounded-[28px] border-slate-200 bg-white shadow-[0_18px_50px_rgba(15,23,42,0.06)] dark:border-slate-800 dark:bg-slate-900 xl:h-[calc(100vh-6rem)]">
            <div className="flex h-full flex-col">
              <div className="border-b border-slate-200 px-5 py-5 dark:border-slate-800">
                <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400 dark:text-slate-500">
                      <span>Stage {String(currentStep).padStart(2, "0")}</span>
                      <span className="text-slate-300 dark:text-slate-600">·</span>
                      <span>{currentMeta.deliverable}</span>
                    </div>
                    <h2 className="mt-2 text-2xl font-semibold tracking-tight text-slate-950 dark:text-slate-50">
                      {currentMeta.label}
                    </h2>
                    <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-500 dark:text-slate-400">
                      {currentMeta.description}
                    </p>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant={STATUS_BADGE_VARIANT[selectedStage.status]} className="px-3 py-1">
                      {STATUS_LABEL[selectedStage.status]}
                    </Badge>
                    <Badge
                      variant="outline"
                      className="border-transparent bg-slate-100 px-3 py-1 text-slate-600 dark:bg-slate-800 dark:text-slate-300"
                    >
                      {flow.target_platform}
                    </Badge>
                    <Badge
                      variant="outline"
                      className="border-transparent bg-slate-100 px-3 py-1 text-slate-600 dark:bg-slate-800 dark:text-slate-300"
                    >
                      对话 {currentStageMessages.length}
                    </Badge>
                  </div>
                </div>
                {focus.length > 0 && (
                  <div className="mt-4 flex flex-wrap gap-2">
                    {focus.map((item, index) => (
                      <Badge
                        key={`${selectedStage.id}-${index}-${item}`}
                        variant="outline"
                        className="border-transparent bg-slate-100 px-2.5 py-1 text-slate-600 dark:bg-slate-800 dark:text-slate-300"
                      >
                        {item}
                      </Badge>
                    ))}
                  </div>
                )}
              </div>

              <div className="flex-1 space-y-4 overflow-y-auto px-5 py-5">
                {messagesLoading && currentStageMessages.length === 0 && (
                  <div className="flex items-center gap-2 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-500 dark:border-slate-800 dark:bg-slate-950/60 dark:text-slate-400">
                    <Loader2 size={16} className="animate-spin" />
                    正在进入当前阶段...
                  </div>
                )}

                {displayMessages.map((message) => {
                  const isUser = message.role === "user";
                  const isConclusion = message.kind === "conclusion";
                  const isStreamingAssistant = !isUser && liveStreamState?.messageId === message.id;
                  return (
                    <div key={message.id} className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
                      <article className={`max-w-[85%] ${isUser ? "order-2" : "order-1"}`}>
                        <div className={`mb-2 flex items-center gap-2 text-xs text-slate-400 ${isUser ? "justify-end" : "justify-start"}`}>
                          {!isUser && (
                            <span className="flex size-8 items-center justify-center rounded-2xl bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900">
                              {isConclusion ? <FileText size={14} /> : <Sparkles size={14} />}
                            </span>
                          )}
                          <span>{isUser ? "你" : isConclusion ? "阶段结论" : "阶段助手"}</span>
                          {message.created_at && <span>{formatDate(message.created_at)}</span>}
                          {isUser && (
                            <span className="flex size-8 items-center justify-center rounded-full bg-sky-600 text-white">
                              <User size={14} />
                            </span>
                          )}
                        </div>
                        <div
                          className={`rounded-[24px] px-4 py-3.5 text-sm leading-7 shadow-sm ${
                            isUser
                              ? "bg-sky-600 text-white"
                              : isConclusion
                                ? "border border-emerald-200 bg-emerald-50 text-emerald-950 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-100"
                                : "border border-slate-200 bg-white text-slate-900 dark:border-slate-800 dark:bg-slate-950/70 dark:text-slate-100"
                          }`}
                        >
                          <div className="mb-2 flex justify-end">
                            <Button
                              variant="outline"
                              size="xs"
                              type="button"
                              onClick={() => void copyMessage(message)}
                              disabled={isStreamingAssistant && !message.content.trim()}
                              className="h-auto rounded-full border-current/10 px-2.5 py-1 text-[11px] opacity-70 hover:opacity-100"
                            >
                              <Copy size={11} />
                              {copiedMessageId === message.id ? "已复制" : "复制"}
                            </Button>
                          </div>
                          {isUser ? (
                            <div className="whitespace-pre-wrap text-white">{message.content}</div>
                          ) : (
                            <div className="chat-md overflow-x-auto">
                              <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
                                {message.content}
                              </ReactMarkdown>
                            </div>
                          )}
                          {isStreamingAssistant && (sending || approving) && (
                            <div className="mt-3 flex items-center gap-2 text-xs opacity-70">
                              <Loader2 size={12} className="animate-spin" />
                              <span>正在思考</span>
                              <ThinkingDots />
                            </div>
                          )}
                          {message.artifact_url && (
                            <a
                              href={buildArtifactDownloadUrl(message.artifact_id, message.artifact_url)}
                              download
                              className="mt-3 inline-flex items-center gap-2 rounded-full border border-current/15 px-3 py-1.5 text-xs font-medium"
                            >
                              <FileDown size={12} />
                              下载 Markdown
                            </a>
                          )}
                        </div>
                      </article>
                    </div>
                  );
                })}

                {!messagesLoading && currentStageMessages.length === 0 && (
                  <div className="rounded-[24px] border border-dashed border-slate-200 bg-slate-50 px-4 py-6 text-sm text-slate-400 dark:border-slate-800 dark:bg-slate-950/60 dark:text-slate-500">
                    当前阶段还没有输出内容。
                  </div>
                )}

                <div ref={messagesEndRef} />
              </div>

              <form onSubmit={submitMessage} className="border-t border-slate-200 px-5 py-4 dark:border-slate-800">
                <div className="rounded-[24px] border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950/70">
                  <div className="mb-3 flex items-center justify-between gap-3">
                    <div className="flex items-center gap-2 text-sm font-semibold text-slate-900 dark:text-slate-100">
                      <Send size={16} className="text-sky-500" />
                      继续当前阶段
                    </div>
                    <div className="text-xs text-slate-400">
                      你可以继续补充、纠偏，或在这一阶段已经完整时直接确认进入下一阶段
                    </div>
                  </div>

                  <div className="mb-3 flex flex-wrap items-center gap-2 rounded-2xl border border-slate-200 bg-white px-3 py-2 dark:border-slate-800 dark:bg-slate-900">
                    <div className="inline-flex items-center gap-2 rounded-full bg-slate-100 px-2.5 py-1 text-xs text-slate-500 dark:bg-slate-800 dark:text-slate-300">
                      <Brain size={12} />
                      当前会话设置
                    </div>

                    <Select
                      value={assistantSettings.model}
                      onValueChange={(value) =>
                        setAssistantSettings((current) => ({ ...current, model: value || "" }))
                      }
                    >
                      <SelectTrigger size="sm" className="min-w-[220px] border-slate-200 bg-slate-50 dark:border-slate-700 dark:bg-slate-950">
                        <SelectValue placeholder={modelsLoading ? "加载模型中..." : "选择模型"} />
                      </SelectTrigger>
                      <SelectContent>
                        {models.map((item) => (
                          <SelectItem key={item.model_id} value={item.model_id}>
                            {item.provider_display} / {item.model_name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>

                    <Select
                      value={assistantSettings.reasoning_effort}
                      onValueChange={(value) =>
                        setAssistantSettings((current) => ({
                          ...current,
                          reasoning_effort: value as AssistantRuntimeSettings["reasoning_effort"],
                        }))
                      }
                    >
                      <SelectTrigger size="sm" className="min-w-[128px] border-slate-200 bg-slate-50 dark:border-slate-700 dark:bg-slate-950">
                        <SelectValue placeholder="思考强度" />
                      </SelectTrigger>
                      <SelectContent>
                        {REASONING_OPTIONS.map((item) => (
                          <SelectItem key={item.value} value={item.value}>
                            思考强度 / {item.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>

                    <Button
                      type="button"
                      variant={assistantSettings.enable_web_search ? "default" : "outline"}
                      onClick={() =>
                        setAssistantSettings((current) => ({
                          ...current,
                          enable_web_search: !current.enable_web_search,
                        }))
                      }
                      className={cn(
                        "h-7 rounded-full px-3 text-xs",
                        assistantSettings.enable_web_search
                          ? "bg-sky-600 text-white hover:bg-sky-500"
                          : "border-slate-200 bg-slate-50 text-slate-600 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-300",
                      )}
                    >
                      <Wifi size={12} />
                      联网增强
                    </Button>

                    <Button
                      type="button"
                      variant={assistantSettings.enable_stage_skills ? "default" : "outline"}
                      onClick={() =>
                        setAssistantSettings((current) => ({
                          ...current,
                          enable_stage_skills: !current.enable_stage_skills,
                        }))
                      }
                      className={cn(
                        "h-7 rounded-full px-3 text-xs",
                        assistantSettings.enable_stage_skills
                          ? "bg-slate-900 text-white hover:bg-slate-800 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-slate-200"
                          : "border-slate-200 bg-slate-50 text-slate-600 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-300",
                      )}
                    >
                      <Wrench size={12} />
                      阶段增强
                    </Button>
                  </div>

                  <textarea
                    value={draft}
                    onChange={(event) =>
                      setDraftByStage((current) => ({
                        ...current,
                        [selectedStage.stage_key]: event.target.value,
                      }))
                    }
                    rows={4}
                    placeholder="继续补充、纠偏，或者直接告诉系统这一阶段已经可以收住。"
                    className="w-full resize-none rounded-[20px] border border-slate-200 bg-white px-4 py-3 text-sm leading-7 text-slate-900 outline-none transition focus:border-sky-400 focus:ring-2 focus:ring-sky-100 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:focus:ring-sky-500/20"
                  />

                  <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
                    <div className="text-xs leading-6 text-slate-400">
                      {isBlockedByUpstream
                        ? "当前阶段已被上游调整影响，需先回到前置阶段重新确认，之后再处理这里。"
                        : selectedStage.status === "approved"
                        ? "当前阶段已确认；如果发现还有遗漏，请先发起调整，系统会把当前阶段和后续阶段标成需重审。"
                        : stageReadyToFinalize
                        ? "这一阶段已经可以收口了；如果没有新的补充，可以直接生成阶段结论。"
                        : "当前阶段会持续对话；只有确认后才进入下一阶段。"}
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Button
                        variant="outline"
                        type="submit"
                        disabled={sending || approving || revising || isBlockedByUpstream || selectedStage.status === "approved" || !draft.trim()}
                        className="h-auto rounded-xl border-slate-200 bg-white px-4 py-2.5 text-sm text-slate-700 hover:border-sky-200 hover:text-sky-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
                      >
                        {sending ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
                        发送
                      </Button>
                      {selectedStage.status === "approved" && (
                        <Button
                          type="button"
                          onClick={requestRevision}
                          disabled={revising || sending || approving || isBlockedByUpstream || !draft.trim()}
                          className="h-auto rounded-xl bg-amber-600 px-4 py-2.5 text-sm text-white hover:bg-amber-500"
                        >
                          {revising ? <Loader2 size={16} className="animate-spin" /> : <GitBranch size={16} />}
                          发起调整
                        </Button>
                      )}
                        <Button
                          type="button"
                          onClick={approveStage}
                        disabled={approving || sending || revising || isBlockedByUpstream || selectedStage.status === "approved"}
                        className="h-auto rounded-xl bg-sky-600 px-4 py-2.5 text-sm text-white hover:bg-sky-500"
                      >
                        {approving ? <Loader2 size={16} className="animate-spin" /> : <Check size={16} />}
                        {hasStageConclusion ? "确认进入下一阶段" : stageReadyToFinalize ? "生成阶段结论" : "继续确认"}
                      </Button>
                    </div>
                  </div>
                </div>
              </form>
            </div>
          </Card>

          <aside className="xl:sticky xl:top-20 xl:h-[calc(100vh-6rem)]">
            <div className="flex h-full flex-col gap-4">
              <Card className="rounded-[26px] border-slate-200 bg-white shadow-[0_10px_30px_rgba(15,23,42,0.04)] dark:border-slate-800 dark:bg-slate-900">
                <CardHeader className="px-4 pb-0 pt-4">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                        产物区
                      </div>
                      <div className="text-xs leading-5 text-slate-500 dark:text-slate-400">
                        右侧展示全流程产物，并突出当前阶段。
                      </div>
                    </div>
                    <a
                      href={buildAllArtifactsDownloadUrl(flowId)}
                      download
                      className="inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs font-medium text-slate-700 hover:border-sky-200 hover:text-sky-700 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-200"
                    >
                      <FileDown size={12} />
                      下载全部
                    </a>
                  </div>
                </CardHeader>
                <CardContent className="p-4 pt-4">
                  <div className="rounded-2xl border border-sky-200 bg-sky-50 px-4 py-3 dark:border-sky-500/20 dark:bg-sky-500/10">
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <div className="text-xs font-medium uppercase tracking-[0.12em] text-sky-600 dark:text-sky-300">
                          当前阶段产物
                        </div>
                        <div className="mt-1 text-sm font-semibold text-slate-900 dark:text-slate-50">
                          {currentMeta.label}
                        </div>
                      </div>
                      <div className="rounded-xl bg-white px-3 py-2 text-sm font-semibold text-sky-700 shadow-sm dark:bg-slate-950 dark:text-sky-300">
                        {currentStageArtifacts.length}
                      </div>
                    </div>
                    <div className="mt-2 text-xs leading-5 text-slate-600 dark:text-slate-300">
                      {currentStageArtifacts.length > 0
                        ? `当前阶段已有 ${currentStageArtifacts.length} 份可下载产物。`
                        : "当前阶段还没有正式产物，完成阶段结论后会出现在这里。"}
                    </div>
                  </div>
                </CardContent>
              </Card>

              <Card className="min-h-0 flex-1 rounded-[26px] border-slate-200 bg-white shadow-[0_10px_30px_rgba(15,23,42,0.04)] dark:border-slate-800 dark:bg-slate-900">
                <CardHeader className="px-4 pb-0 pt-4">
                  <div className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                    全流程产物
                  </div>
                  <div className="text-xs leading-5 text-slate-500 dark:text-slate-400">
                    当前阶段置顶，其他阶段按流程顺序展示。
                  </div>
                </CardHeader>
                <CardContent className="min-h-0 p-3 pt-3">
                  <div className="space-y-3 xl:max-h-[calc(100vh-19rem)] xl:overflow-y-auto xl:pr-1">
                    {artifactGroups.length === 0 ? (
                      <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50 px-4 py-8 text-center text-sm text-slate-400 dark:border-slate-800 dark:bg-slate-950/60 dark:text-slate-500">
                        还没有可归档的阶段产物。
                      </div>
                    ) : (
                      artifactGroups.map((group) => {
                        const isCurrentGroup = group.stage.stage_key === selectedStage.stage_key;
                        return (
                          <div
                            key={group.stage.id}
                            className={cn(
                              "rounded-2xl border px-3 py-3",
                              isCurrentGroup
                                ? "border-sky-200 bg-sky-50 dark:border-sky-500/20 dark:bg-sky-500/10"
                                : "border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-950/60",
                            )}
                          >
                            <div className="mb-3 flex items-center justify-between gap-3">
                              <div className="min-w-0">
                                <div className="truncate text-sm font-semibold text-slate-900 dark:text-slate-100">
                                  {group.stage.title}
                                </div>
                                <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                                  {STAGE_META[group.stage.stage_key].deliverable}
                                </div>
                              </div>
                              <Badge
                                variant={STATUS_BADGE_VARIANT[group.stage.status]}
                                className="px-2.5 py-1 text-[10px]"
                              >
                                {STATUS_LABEL[group.stage.status]}
                              </Badge>
                            </div>

                            {group.artifacts.length === 0 ? (
                              <div className="rounded-xl border border-dashed border-slate-200 px-3 py-3 text-xs text-slate-400 dark:border-slate-700 dark:text-slate-500">
                                当前阶段还没有正式产物。
                              </div>
                            ) : (
                              <div className="space-y-2">
                                {group.artifacts.map((artifact, index) => (
                                  <div
                                    key={`${group.stage.id}-${index}-${artifact.url || artifact.artifact_id || artifact.label}`}
                                    className="rounded-xl border border-slate-200 bg-white px-3 py-3 dark:border-slate-800 dark:bg-slate-900"
                                  >
                                    <div className="flex items-start justify-between gap-3">
                                      <div className="min-w-0">
                                        <div className="truncate text-sm font-medium text-slate-900 dark:text-slate-100">
                                          {artifact.label || artifact.type || "阶段附件"}
                                        </div>
                                        <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                                          {artifact.created_at ? `更新于 ${formatDate(artifact.created_at)}` : "已生成"}
                                        </div>
                                      </div>
                                      {artifact.url ? (
                                        <a
                                          href={buildArtifactDownloadUrl(artifact.artifact_id, artifact.url)}
                                          download
                                          className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:border-sky-200 hover:text-sky-700 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-200"
                                        >
                                          <Paperclip size={12} />
                                          下载
                                        </a>
                                      ) : null}
                                    </div>
                                  </div>
                                ))}
                              </div>
                            )}
                          </div>
                        );
                      })
                    )}
                  </div>
                </CardContent>
              </Card>
            </div>
          </aside>
        </div>
      </main>
    </div>
  );
}
