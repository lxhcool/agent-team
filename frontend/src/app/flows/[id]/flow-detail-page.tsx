"use client";

import Link from "next/link";
import Image from "next/image";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "next/navigation";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import {
  ArrowLeft,
  ArrowUp,
  Brain,
  Check,
  CheckCircle2,
  ClipboardList,
  Copy,
  Eclipse,
  FileDown,
  FileText,
  FolderCode,
  FolderKanban,
  Layers3,
  Loader2,
  PackageCheck,
  Paperclip,
  Plus,
  ReceiptText,
  Send,
  Sparkles,
  User,
  Wifi,
  Wrench,
} from "lucide-react";

import { TopNav } from "../../components/topnav";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
    description: "只确认这是什么产品：基本使用或呈现方式怎么成立、哪些前提会影响后续骨架。",
    deliverable: "需求确认文档",
    icon: PackageCheck,
  },
  product: {
    label: "方案设计",
    short: "02",
    description: "把方案骨架搭起来：功能模块怎么分、模块怎么协作、页面结构怎么组织、主要流程怎么走。",
    deliverable: "方案设计文档",
    icon: Eclipse,
  },
  ui_direction: {
    label: "细节确认",
    short: "03",
    description: "把业务规则说透：角色权限、状态流转、异常处理、数据口径和关键边界怎么定。",
    deliverable: "细节确认文档",
    icon: ReceiptText,
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
    description: "整理开发可直接接手的方案：实现路径、模块拆分、接口数据组织、依赖和风险。",
    deliverable: "开发方案文档",
    icon: FolderCode,
  },
  development: {
    label: "交付准备",
    short: "05",
    description: "整理进入继续落地前需要的交付准备说明。",
    deliverable: "交付准备文档",
    icon: FileText,
  },
  acceptance: {
    label: "交付检查",
    short: "06",
    description: "明确怎么判断当前交付物已经足够继续流转。",
    deliverable: "交付检查文档",
    icon: CheckCircle2,
  },
  deployment: {
    label: "最终交付",
    short: "05",
    description: "整理一份最终总结结论，并同步形成最后的交付文档，支持单独下载或整体打包下载。",
    deliverable: "最终交付文档",
    icon: ClipboardList,
  },
};

const STATUS_LABEL: Record<StageStatus, string> = {
  draft: "待开始",
  awaiting_confirmation: "待确认",
  approved: "已确认",
  revision_requested: "需调整",
  skipped: "已跳过",
};

const STATUS_BADGE_VARIANT: Record<StageStatus, "secondary" | "info" | "success" | "warning"> = {
  draft: "secondary",
  awaiting_confirmation: "info",
  approved: "success",
  revision_requested: "warning",
  skipped: "secondary",
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

function artifactBelongsToStage(stage: FlowStage, artifact: RecommendationArtifact) {
  const type = (artifact.type || "").trim().toLowerCase();
  if (!type) return true;
  return type.startsWith(stage.stage_key);
}

function artifactTimestampValue(artifact: RecommendationArtifact) {
  const raw = artifact.created_at ? new Date(artifact.created_at).getTime() : 0;
  return Number.isFinite(raw) ? raw : 0;
}

function dedupeStageArtifacts(artifacts: RecommendationArtifact[]) {
  const latestByType = new Map<string, RecommendationArtifact>();
  for (const artifact of artifacts) {
    const key =
      (artifact.type || "").trim().toLowerCase() ||
      (artifact.label || "").trim().toLowerCase() ||
      (artifact.artifact_id || "").trim().toLowerCase();
    if (!key) continue;
    const existing = latestByType.get(key);
    if (!existing || artifactTimestampValue(artifact) >= artifactTimestampValue(existing)) {
      latestByType.set(key, artifact);
    }
  }
  return Array.from(latestByType.values()).sort(
    (left, right) => artifactTimestampValue(right) - artifactTimestampValue(left),
  );
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


function mergeReasoningPreview(current: string, incoming: string) {
  const merged = `${current || ""}${incoming || ""}`.replace(/\s+/g, " ").trim();
  if (!merged) return "";
  return merged.length > 140 ? `…${merged.slice(-140)}` : merged;
}

function splitStreamingChunk(content: string, maxSize = 28) {
  if (!content) return [];
  return content.match(new RegExp(`[\\s\\S]{1,${maxSize}}`, "g")) || [];
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
  const [error, setError] = useState("");
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null);
  const [streamByStage, setStreamByStage] = useState<Record<string, StageStreamState | null>>({});
  const [assistantSettings, setAssistantSettings] = useState<AssistantRuntimeSettings>(DEFAULT_ASSISTANT_SETTINGS);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const loadedStageKeysRef = useRef<Set<string>>(new Set());
  const bootstrappingStageKeysRef = useRef<Set<string>>(new Set());
  const bootstrapFailedStageKeysRef = useRef<Set<string>>(new Set());
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
  const stageReadinessBlockers = hasStageConclusion
    ? []
    : selectedStage?.recommendation?.stage_runtime?.readiness_blockers || [];
  const isFinalStage = selectedStage?.stage_key === "deployment";

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
    if (!force && loadedStageKeysRef.current.has(stageKey)) return null;
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
      return data;
    } catch (err: any) {
      setError(err.message || "获取阶段对话失败");
      return null;
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

    const appendContentChunk = async (chunk: string, resetPreview = false) => {
      const parts = chunk.length > 96 ? splitStreamingChunk(chunk, 28) : [chunk];
      for (let index = 0; index < parts.length; index += 1) {
        const part = parts[index];
        setStreamByStage((current) => {
          const active = current[stageKey];
          if (!active) return current;
          const shouldReset = resetPreview && index === 0;
          return {
            ...current,
            [stageKey]: {
              ...active,
              content: shouldReset ? part : active.content + part,
              reasoning: "",
            },
          };
        });
        if (parts.length > 1 && index < parts.length - 1) {
          await sleep(12);
        }
      }
    };

    let requestAcceptedAtLeastOnce = false;
    try {
      let lastError: unknown = null;

      for (let attempt = 0; attempt <= STREAM_RETRY_LIMIT; attempt += 1) {
        let requestAccepted = false;
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
          requestAccepted = true;
          requestAcceptedAtLeastOnce = true;

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
                  await appendContentChunk(parsed.data, didRestartThisAttempt);
                  didRestartThisAttempt = false;
                } else if (parsed.event === "reasoning") {
                  setStreamByStage((current) => {
                    const active = current[stageKey];
                    if (!active) return current;
                    return {
                      ...current,
                      [stageKey]: {
                        ...active,
                        reasoning: mergeReasoningPreview(active.reasoning, parsed.data),
                      },
                    };
                  });
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
              await appendContentChunk(parsed.data, didRestartThisAttempt);
              didRestartThisAttempt = false;
            } else if (parsed.event === "reasoning") {
              setStreamByStage((current) => {
                const active = current[stageKey];
                if (!active) return current;
                return {
                  ...current,
                  [stageKey]: {
                    ...active,
                    reasoning: mergeReasoningPreview(active.reasoning, parsed.data),
                  },
                };
              });
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
          if (userMessage && requestAccepted && attempt >= STREAM_RETRY_LIMIT) {
            setStreamByStage((current) => ({ ...current, [stageKey]: null }));
            try {
              const refreshed = await loadStageMessages(stageKey, true);
              const hasAssistantReply = Boolean(
                refreshed?.messages?.some((message) => message.role === "assistant" && message.content.trim()),
              );
              if (hasAssistantReply) {
                return;
              }
            } catch {
              // Fall through to the existing local rollback path below.
            }
          }
          if (!isRetriableStreamError(err) || attempt >= STREAM_RETRY_LIMIT) {
            throw err;
          }
          await sleep(STREAM_RETRY_DELAY_MS);
        }
      }
      throw lastError instanceof Error ? lastError : new Error("发送消息失败");
    } catch (err) {
      if (userMessage && !requestAcceptedAtLeastOnce) {
        setMessagesByStage((current) => ({
          ...current,
          [stageKey]: (current[stageKey] || []).filter((message) => message.id !== userMessage.id),
        }));
        setDraftByStage((current) => ({ ...current, [stageKey]: draft || "" }));
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
      bootstrapFailedStageKeysRef.current.has(stageKey) ||
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
          bootstrapFailedStageKeysRef.current.delete(stageKey);
          await loadStageMessages(stageKey, true);
          return;
        }
        bootstrapFailedStageKeysRef.current.add(stageKey);
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
    if (!selectedStage) return;
    const stageKey = selectedStage.stage_key;
    const stageMessages = messagesByStage[stageKey] || [];
    if (stageMessages.some((item) => item.role === "assistant" && item.content.trim())) {
      bootstrapFailedStageKeysRef.current.delete(stageKey);
    }
  }, [messagesByStage, selectedStage]);

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
    const draft = (draftByStage[selectedStage.stage_key] || "").trim();
    if (!draft) return;

    setSending(true);
    setError("");
    const stageKey = selectedStage.stage_key;
    try {
      if (selectedStage.status === "approved") {
        const revisionRes = await fetch(`/api/flows/${flowId}/stages/${stageKey}/request-revision`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ feedback: draft }),
        });
        if (!revisionRes.ok) {
          const data = await revisionRes.json().catch(() => ({}));
          throw new Error(data.detail || "发起调整失败");
        }
        const revisionFlow = (await revisionRes.json()) as Flow;
        setFlow(revisionFlow);
        setSelectedKey(stageKey);
        loadedStageKeysRef.current.delete(stageKey);
      }
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
      setError(isFinalStage ? "请先完成前置阶段，再完成当前流程" : "请先完成前置阶段，再进入下一阶段");
      return;
    }
    if (!hasStageConclusion) {
      setError(stageReadinessBlockers[0] || "当前阶段结论还没生成出来，先继续补充当前内容");
      return;
    }
    setApproving(true);
    setError("");
    const stageKey = selectedStage.stage_key;
    try {
      const res = await fetch(`/api/flows/${flowId}/stages/${stageKey}/approve`, {
        method: "POST",
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || (isFinalStage ? "完成当前流程失败" : "进入下一阶段失败"));
      }
      const data = (await res.json()) as Flow;
      setFlow(data);
      setSelectedKey(data.current_stage);
      if (data.current_stage) {
        loadedStageKeysRef.current.delete(data.current_stage);
      }
    } catch (err: any) {
      setError(err.message || (isFinalStage ? "完成当前流程失败" : "进入下一阶段失败"));
    } finally {
      setApproving(false);
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
      <div className="min-h-screen dark:from-slate-950 dark:via-slate-950 dark:to-indigo-950/20" style={{ backgroundColor: '#f9f9ff' }}>
        <TopNav />
        <main className="flex h-screen items-center justify-center pt-14 text-slate-400">
          <Loader2 className="animate-spin" />
        </main>
      </div>
    );
  }

  if (!flow || !selectedStage || !currentMeta) {
    return (
      <div className="min-h-screen dark:from-slate-950 dark:via-slate-950 dark:to-indigo-950/20" style={{ backgroundColor: '#f9f9ff' }}>
        <TopNav />
        <main className="mx-auto max-w-[1600px] px-6 pt-28">
          <Link href="/" className="inline-flex items-center gap-2 text-sm text-slate-500 hover:text-indigo-600 dark:hover:text-indigo-400">
            <ArrowLeft size={16} />
            返回首页
          </Link>
          <div className="mt-8 rounded-2xl bg-red-50/80 dark:bg-red-500/10 backdrop-blur-sm ring-1 ring-red-200/60 dark:ring-red-500/20 p-5 text-red-600 dark:text-red-300">
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
      artifacts: dedupeStageArtifacts(
        (stage.recommendation?.artifacts || []).filter(
          (artifact) => (artifact.url || artifact.artifact_id) && artifactBelongsToStage(stage, artifact),
        ),
      ),
    }))
    .filter((group) => group.stage.status === "approved" && group.artifacts.length > 0)
    .sort((a, b) => a.stage.order - b.stage.order);

  return (
    <div className="flex h-screen flex-col text-slate-950 dark:text-slate-50" style={{ backgroundColor: '#f9f9ff' }}>
      <TopNav />
      <main className="flex min-h-0 flex-1 flex-col pt-14">
        {/* Page header — floating, not a rigid bar */}
        <div className="mx-auto w-full max-w-[1600px] px-4 py-6">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <Link
                href="/flows"
                className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-white/70 dark:bg-slate-800/50 text-slate-500 dark:text-slate-400 ring-1 ring-slate-200/60 dark:ring-slate-700/40 backdrop-blur-sm hover:ring-indigo-300 dark:hover:ring-indigo-500/40 hover:text-indigo-600 dark:hover:text-indigo-400 transition-all"
              >
                <ArrowLeft size={16} />
              </Link>
              <div className="min-w-0">
                <h1 className="flex items-center gap-2 text-lg font-semibold tracking-tight text-slate-900 dark:text-slate-100">
                  <span className="line-clamp-2 leading-snug break-words">{flow.name}</span>
                  <span className="shrink-0 inline-flex items-center rounded-full bg-indigo-50 dark:bg-indigo-500/10 px-2 py-0.5 text-[11px] font-medium text-indigo-600 dark:text-indigo-400">
                    {currentMeta?.label}
                  </span>
                </h1>
              </div>
            </div>
            {/* Progress */}
            <div className="hidden sm:flex w-[240px] shrink-0 items-center gap-2 rounded-lg bg-white/60 dark:bg-slate-800/60 backdrop-blur-sm px-3 py-1.5 ring-1 ring-slate-200/60 dark:ring-slate-700/40">
              <div className="flex-1 h-1.5 overflow-hidden rounded-full bg-slate-200/80 dark:bg-slate-700/60">
                <div className="h-full rounded-full bg-gradient-to-r from-indigo-500 to-violet-500 transition-all" style={{ width: `${stageProgress}%` }} />
              </div>
              <span className="text-[11px] font-semibold text-indigo-600 dark:text-indigo-400">{stageProgress}%</span>
            </div>
          </div>
        </div>

        {error && (
          <div className="border-b border-red-200/60 dark:border-red-500/20 bg-red-50/80 dark:bg-red-500/10 backdrop-blur-sm px-4 py-1.5 text-xs text-red-700 dark:text-red-300">
            {error}
          </div>
        )}

        {/* Three-column workspace — Floating panels */}
        <div className="mx-auto w-full max-w-[1600px] flex min-h-0 flex-1 gap-4 px-4 pb-4">
          {/* Left column — stage list */}
          <aside className="w-[240px] shrink-0 flex flex-col items-center">
            {/* Header + Stage list */}
            <div className="w-[240px] rounded-lg bg-white dark:bg-slate-900 shadow-[0_1px_3px_rgba(0,0,0,0.06),0_1px_2px_rgba(0,0,0,0.04)]">
              <div className="px-4 pt-5 pb-4 text-base font-semibold leading-none">
                <h2 className="text-[#333] dark:text-slate-200">流程阶段</h2>
              </div>
              {/* Stage list */}
              <div className="flex flex-col gap-1 overflow-y-auto px-2 pb-2">
              {visibleStages.map((stage, index) => {
                const meta = STAGE_META[stage.stage_key];
                const active = stage.stage_key === selectedStage.stage_key;
                const approved = stage.status === "approved";
                return (
                  <button
                    type="button"
                    key={stage.id}
                    onClick={() => setSelectedKey(stage.stage_key)}
                    className={cn(
                      "relative flex w-full items-center gap-2.5 px-2.5 py-2 rounded-lg text-left text-sm transition-all",
                      active
                        ? "font-medium text-indigo-600 dark:text-indigo-400 bg-indigo-50/90 dark:bg-indigo-500/10"
                        : "text-slate-600 dark:text-slate-400 hover:bg-slate-100/60 dark:hover:bg-slate-800/40",
                    )}
                  >
                    <div
                      className={cn(
                        "flex size-7 shrink-0 items-center justify-center rounded-lg",
                        approved
                          ? "bg-emerald-50 text-emerald-600 dark:bg-emerald-500/10 dark:text-emerald-400"
                          : active
                            ? "bg-white text-indigo-600 shadow-[0_1px_2px_rgba(0,0,0,0.04)] dark:bg-slate-800 dark:text-indigo-400"
                            : "bg-slate-100 text-slate-400 dark:bg-slate-800 dark:text-slate-500",
                      )}
                    >
                      {approved ? <Check size={14} /> : <meta.icon size={14} />}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5">
                        <span className={cn("truncate text-[13px]", active && "font-semibold")}>{meta.label}</span>
                      </div>
                      <div className="mt-0.5">
                        <span className="truncate text-[12px] text-slate-400 dark:text-slate-500">{meta.deliverable}</span>
                      </div>
                    </div>
                    <Badge
                      variant={active ? "secondary" : STATUS_BADGE_VARIANT[stage.status]}
                      className={cn(
                        "px-1.5 py-px text-[10px] leading-4 shrink-0",
                        active && "bg-white dark:bg-slate-800",
                      )}
                    >
                      {STATUS_LABEL[stage.status]}
                    </Badge>
                  </button>
                );
              })}
            </div>
            </div>

            {/* Current stage info */}
            <div className="current-stage-card w-[240px] mt-4 rounded-lg shadow-[0_1px_3px_rgba(0,0,0,0.06),0_1px_2px_rgba(0,0,0,0.04)] px-4 pt-5 pb-4" style={{ background: 'linear-gradient(90deg, #fff2ec 0, #f0f1f6 42%, #eef2ff 100%)' }}>
              <div className="flex items-center justify-between mb-2.5">
                <span className="text-base font-semibold leading-none text-[#333] dark:text-slate-200">当前阶段</span>
                <Badge variant="secondary" className="px-1.5 py-px text-[10px] leading-4 bg-white dark:bg-slate-800 shadow-[0_1px_2px_rgba(0,0,0,0.04)]">
                  {currentMeta.label}
                </Badge>
              </div>
              <div className="mt-3 text-xs leading-4 text-slate-400 dark:text-slate-500">{currentMeta.description}</div>
              <div className="mt-4 rounded bg-slate-100 dark:bg-slate-800 px-2.5 py-1.5 text-[11px] leading-4 text-slate-500 dark:text-slate-400">
                {isBlockedByUpstream
                  ? "当前阶段被前置阶段卡住，先完成上游调整。"
                  : hasStageConclusion
                    ? isFinalStage
                      ? "最终交付已经生成，可以直接完成当前流程或继续补充。"
                      : "当前阶段结论已经生成，可以直接进入下一阶段或继续补充。"
                    : stageReadyToFinalize
                    ? isFinalStage
                      ? "当前信息已足够生成最终交付文档。"
                      : "当前信息已足够生成阶段文档。"
                    : "当前还在对话推进中，建议继续补充关键点。"}
              </div>
            </div>
          </aside>

          {/* Center column — chat */}
          <section className="flex min-w-0 flex-1 flex-col rounded-lg bg-white dark:bg-slate-900 shadow-[0_1px_3px_rgba(0,0,0,0.06),0_1px_2px_rgba(0,0,0,0.04)] overflow-hidden">
            {/* Chat header */}
            <div className="px-4 py-3 border-b border-slate-200/50 dark:border-slate-700/30 bg-white/40 dark:bg-slate-800/30">
              <div className="flex items-center gap-2">
                {currentMeta && <currentMeta.icon size={16} className="text-indigo-500 dark:text-indigo-400" />}
                <span className="text-sm font-semibold text-slate-800 dark:text-slate-200">{currentMeta.label}</span>
                <span className="text-xs text-slate-400 dark:text-slate-500">· {currentMeta.deliverable}</span>
              </div>
            </div>

            {/* Messages */}
            <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
              {messagesLoading && currentStageMessages.length === 0 && (
                <div className="flex items-center gap-2 text-xs text-slate-400">
                  <Loader2 size={14} className="animate-spin" />
                  正在进入当前阶段...
                </div>
              )}

              {displayMessages.map((message) => {
                const isUser = message.role === "user";
                const isConclusion = message.kind === "conclusion";
                const isStreamingAssistant = !isUser && liveStreamState?.messageId === message.id;
                const copied = copiedMessageId === message.id;
                return (
                  <div key={message.id} className={`flex gap-2.5 ${isUser ? "justify-end" : "justify-start"}`}>
                    {!isUser && (
                      <div className="flex size-7 shrink-0 items-center justify-center rounded-full overflow-hidden mt-0.5">
                        <Image src="/logo.png" alt="Logo" width={28} height={28} />
                      </div>
                    )}
                    <article className="max-w-[80%] min-w-0 group">
                      <div className={`mb-1 flex items-center gap-2 text-[11px] text-slate-400 ${isUser ? "justify-end" : ""}`}>
                        <span className="font-medium">{isUser ? "你" : ""}</span>
                      </div>
                      <div
                        className={`rounded-xl px-3.5 py-2.5 text-[13px] leading-6 ${
                          isUser
                            ? "bg-gradient-to-r from-indigo-600 to-violet-600 text-white"
                            : isConclusion
                              ? "ring-1 ring-amber-300/50 dark:ring-amber-500/20 bg-amber-50/80 dark:bg-amber-900/15 backdrop-blur-sm text-slate-800 dark:text-amber-100"
                              : "ring-1 ring-indigo-200/60 dark:ring-indigo-500/20 bg-indigo-50/50 dark:bg-indigo-900/10 backdrop-blur-sm text-slate-800 dark:text-indigo-100"
                        }`}
                      >
                        {isConclusion && (
                          <div className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold text-amber-600 dark:text-amber-400">
                            <FileText size={12} />
                            <span>阶段结论</span>
                          </div>
                        )}
                        {!isUser && !isConclusion && (
                          <div className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold text-indigo-600 dark:text-indigo-400">
                            <Sparkles size={12} />
                            <span>阶段助手</span>
                          </div>
                        )}
                        {isUser ? (
                          <div className="whitespace-pre-wrap">{message.content}</div>
                        ) : (
                          <div className="chat-md overflow-x-auto">
                            <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
                              {message.content}
                            </ReactMarkdown>
                          </div>
                        )}
                        {isStreamingAssistant && (sending || approving) && (
                          <div className="mt-2 space-y-1.5 text-[11px] opacity-60">
                            <div className="flex items-center gap-1.5">
                              <Loader2 size={11} className="animate-spin" />
                              <span>{liveStreamState?.reasoning ? "正在思考中" : "正在思考"}</span>
                              <ThinkingDots />
                            </div>
                            {liveStreamState?.reasoning ? (
                              <div className="rounded-lg bg-black/5 px-2 py-1 text-[11px] leading-5 text-slate-500 dark:bg-white/5 dark:text-slate-400">
                                {liveStreamState.reasoning}
                              </div>
                            ) : null}
                          </div>
                        )}
                      </div>
                      {/* Assistant action bar — appears on hover */}
                      {!isUser && !isStreamingAssistant && message.content.trim() && (
                        <div className="mt-1 flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                          <button
                            type="button"
                            onClick={() => void copyMessage(message)}
                            className="inline-flex items-center gap-1 rounded-lg px-1.5 py-0.5 text-[11px] text-slate-400 hover:text-slate-600 hover:bg-slate-100 dark:text-slate-500 dark:hover:text-slate-300 dark:hover:bg-white/5 transition-colors"
                          >
                            {copied ? <Check size={12} /> : <Copy size={12} />}
                            {copied ? "已复制" : "复制"}
                          </button>
                          {message.artifact_url && (
                            <a
                              href={buildArtifactDownloadUrl(message.artifact_id, message.artifact_url)}
                              download
                              className="inline-flex items-center gap-1 rounded-lg px-1.5 py-0.5 text-[11px] text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 dark:text-slate-500 dark:hover:text-indigo-400 dark:hover:bg-indigo-500/10 transition-colors"
                            >
                              <FileDown size={12} />
                              下载
                            </a>
                          )}
                        </div>
                      )}
                    </article>
                    {isUser && (
                      <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-gradient-to-r from-indigo-600 to-violet-600 text-white mt-0.5">
                        <User size={13} />
                      </div>
                    )}
                  </div>
                );
              })}

              {!messagesLoading && currentStageMessages.length === 0 && (
                <div className="flex flex-col items-center justify-center py-12 text-slate-400">
                  <Sparkles size={24} strokeWidth={1.5} className="mb-2 text-slate-300 dark:text-slate-600" />
                  <span className="text-sm">当前阶段还没有输出内容</span>
                </div>
              )}

              <div ref={messagesEndRef} />
            </div>

            {/* Input area — Unified card */}
            <form onSubmit={submitMessage}>
              <div className="m-4 rounded-xl bg-white dark:bg-slate-900 shadow-[0_2px_8px_rgba(0,0,0,0.08)] ring-1 ring-slate-200/70 dark:ring-slate-700/30 overflow-hidden">
                {/* Textarea — main area */}
                <textarea
                  value={draft}
                  onChange={(event) =>
                    setDraftByStage((current) => ({
                      ...current,
                      [selectedStage.stage_key]: event.target.value,
                    }))
                  }
                  rows={4}
                  placeholder="继续补充、纠偏，或者告诉系统当前理解已经差不多了..."
                  className="w-full resize-none bg-transparent px-4 py-3.5 text-sm leading-relaxed text-slate-700 dark:text-slate-200 placeholder:text-slate-400 dark:placeholder:text-slate-500 outline-none min-h-[80px] max-h-[200px]"
                />

                {/* Bottom toolbar */}
                <div className="flex items-center gap-2 border-t border-slate-100 dark:border-slate-700/50 px-3 py-2.5 bg-slate-50/50 dark:bg-slate-800/30">
                  {/* Left side — tools */}
                  <div className="flex items-center gap-1.5">
                    <button type="button" className="inline-flex size-8 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-700/60 dark:hover:text-slate-300 transition-colors">
                      <Plus size={16} />
                    </button>

                    <Select
                      value={assistantSettings.model}
                      onValueChange={(value) =>
                        setAssistantSettings((current) => ({ ...current, model: value || "" }))
                      }
                    >
                      <SelectTrigger size="sm" className="h-8 w-auto max-w-[160px] text-xs border-slate-200/60 bg-white dark:bg-slate-800 ring-1 ring-slate-200/60 dark:ring-slate-700/40 text-slate-600 dark:text-slate-300 focus-visible:ring-indigo-300 gap-1">
                        <Sparkles size={12} className="shrink-0 text-slate-400" />
                        <SelectValue placeholder={modelsLoading ? "加载中..." : "选择模型"} style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} />
                      </SelectTrigger>
                      <SelectContent className="min-w-[220px]">
                        {models.map((item) => (
                          <SelectItem key={item.model_id} value={item.model_id}>
                            {item.provider_display} / {item.model_name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>

                    <button
                      type="button"
                      onClick={() =>
                        setAssistantSettings((current) => ({
                          ...current,
                          enable_web_search: !current.enable_web_search,
                        }))
                      }
                      className={cn(
                        "inline-flex h-8 w-8 items-center justify-center rounded-lg transition-colors",
                        assistantSettings.enable_web_search
                          ? "bg-indigo-100 text-indigo-600 dark:bg-indigo-500/15 dark:text-indigo-400"
                          : "text-slate-400 hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-700/60 dark:hover:text-slate-300",
                      )}
                      title="联网搜索"
                    >
                      <Wifi size={16} />
                    </button>

                    <button
                      type="button"
                      onClick={() =>
                        setAssistantSettings((current) => ({
                          ...current,
                          enable_stage_skills: !current.enable_stage_skills,
                        }))
                      }
                      className={cn(
                        "inline-flex h-8 w-8 items-center justify-center rounded-lg transition-colors",
                        assistantSettings.enable_stage_skills
                          ? "bg-violet-100 text-violet-600 dark:bg-violet-500/15 dark:text-violet-400"
                          : "text-slate-400 hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-700/60 dark:hover:text-slate-300",
                      )}
                      title="阶段增强"
                    >
                      <Wrench size={16} />
                    </button>
                  </div>

                  {/* Right side — actions */}
                  <div className="ml-auto flex items-center gap-1.5">
                      <Button
                      type="submit"
                      size="sm"
                      disabled={sending || approving || isBlockedByUpstream || !draft.trim()}
                      className="h-8 gap-1.5 rounded-lg bg-gradient-to-r from-indigo-500 to-violet-500 px-3 text-xs text-white shadow-sm shadow-indigo-500/25 hover:from-indigo-600 hover:to-violet-600 hover:shadow-md"
                    >
                      {sending ? <Loader2 size={13} className="animate-spin" /> : <ArrowUp size={13} />}
                      发送
                    </Button>
                    {!selectedStage.status?.includes("approved") && (
                      <Button
                        variant="outline"
                        size="sm"
                        type="button"
                        onClick={approveStage}
                        disabled={approving || sending || isBlockedByUpstream || selectedStage.status === "approved" || !hasStageConclusion}
                        className="h-8 gap-1.5 rounded-lg text-xs"
                        title={
                          hasStageConclusion
                            ? isFinalStage
                              ? "确认最终交付并完成当前流程"
                              : "确认当前阶段并进入下一阶段"
                            : isFinalStage
                              ? "最终交付生成后才能完成当前流程"
                              : "当前阶段结论生成后才能进入下一阶段"
                        }
                      >
                        {approving ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
                        {isFinalStage ? "完成当前流程" : "进入下一阶段"}
                      </Button>
                    )}
                  </div>
                </div>
              </div>
              </form>
          </section>

          {/* Right column — artifacts */}
          <aside className="w-[300px] shrink-0 self-start rounded-lg bg-white dark:bg-slate-900 shadow-[0_1px_3px_rgba(0,0,0,0.06),0_1px_2px_rgba(0,0,0,0.04)] bg-no-repeat flex flex-col" style={{ backgroundImage: "url('/model-bg.png')", backgroundPosition: 'center 0px', backgroundSize: 'contain', minHeight: '245px' }}>
            <div className="px-4 pt-5 pb-4 flex items-center justify-between">
              <h3 className="text-base font-semibold leading-none text-[#333] dark:text-slate-200">产物</h3>
              {artifactGroups.length > 0 && (
                <a
                  href={buildAllArtifactsDownloadUrl(flowId)}
                  download
                  className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-50 px-2.5 py-1.5 text-[11px] font-medium text-indigo-600 transition-all hover:bg-indigo-100 hover:shadow-sm dark:bg-indigo-500/10 dark:text-indigo-400 dark:hover:bg-indigo-500/20"
                >
                  <FileDown size={13} />
                  下载全部
                </a>
              )}
            </div>
            <div className="flex-1 overflow-y-auto px-4 py-3">
              {artifactGroups.length > 0 ? (
                <div className="space-y-3">
                  {artifactGroups.map((group) => (
                    <div key={group.stage.id}>
                      {/* Stage header — de-emphasized */}
                      <div className="mb-1.5 flex items-center gap-1.5 text-[11px] text-slate-400 dark:text-slate-500">
                        <span>{group.stage.title}</span>
                        <span className="text-slate-300 dark:text-slate-600">·</span>
                        <Badge
                          variant={STATUS_BADGE_VARIANT[group.stage.status]}
                          className="px-1 py-px text-[10px] leading-4 bg-transparent shadow-none"
                        >
                          {STATUS_LABEL[group.stage.status]}
                        </Badge>
                      </div>

                      {/* Artifact cards — download-focused */}
                      <div className="space-y-1.5">
                        {group.artifacts.map((artifact, index) => (
                          <a
                            key={`${group.stage.id}-${index}-${artifact.url || artifact.artifact_id || artifact.label}`}
                            href={buildArtifactDownloadUrl(artifact.artifact_id, artifact.url)}
                            download
                            className="group flex items-center gap-2.5 rounded-lg border border-slate-200/70 dark:border-slate-700/40 bg-gradient-to-r from-white to-slate-50/50 dark:from-slate-800 dark:to-slate-800/50 px-3 py-2.5 transition-all hover:border-indigo-300 hover:shadow-sm hover:shadow-indigo-500/5 dark:hover:border-indigo-500/30 dark:hover:shadow-indigo-500/5"
                          >
                            <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-indigo-50 text-indigo-500 dark:bg-indigo-500/10 dark:text-indigo-400 transition-colors group-hover:bg-indigo-100 dark:group-hover:bg-indigo-500/20">
                              <FileText size={14} />
                            </div>
                            <div className="min-w-0 flex-1">
                              <div className="truncate text-[13px] font-medium text-slate-700 dark:text-slate-200">{artifact.label || artifact.type || "附件"}</div>
                              {artifact.created_at ? (
                                <div className="text-[11px] text-slate-400 dark:text-slate-500">{formatDate(artifact.created_at)}</div>
                              ) : null}
                            </div>
                            <div className="flex size-7 shrink-0 items-center justify-center rounded-md bg-indigo-50 text-indigo-500 dark:bg-indigo-500/15 dark:text-indigo-400 opacity-80 transition-all group-hover:bg-indigo-500 group-hover:text-white dark:group-hover:bg-indigo-500 group-hover:opacity-100">
                              <FileDown size={13} />
                            </div>
                          </a>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="flex-1 flex flex-col items-center justify-center gap-3 text-slate-400 dark:text-slate-500 py-8">
                  <img src="/artifacts-empty.svg" alt="暂无产物" width={120} height={120} className="opacity-70 dark:opacity-40" />
                </div>
              )}
            </div>
          </aside>
        </div>
      </main>
    </div>
  );
}
