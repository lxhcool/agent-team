"use client";

import Link from "next/link";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "next/navigation";
import {
  ArrowLeft,
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
} from "lucide-react";

import { TopNav } from "../../components/topnav";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
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

const MAIN_STAGE_ORDER: StageKey[] = [
  "requirements",
  "product",
  "ui_direction",
  "technical",
  "development",
  "acceptance",
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
    label: "需求澄清",
    short: "01",
    description: "先确认这次要做的到底是什么，以及还缺哪些关键信息。",
    deliverable: "需求澄清结论",
    icon: Sparkles,
  },
  product: {
    label: "范围定义",
    short: "02",
    description: "把这次做什么、不做什么、优先级怎么排收紧下来。",
    deliverable: "范围定义文档",
    icon: Layers3,
  },
  ui_direction: {
    label: "方案整理",
    short: "03",
    description: "整理页面、模块、流程和关键规则。",
    deliverable: "方案整理文档",
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
    label: "实现约束",
    short: "04",
    description: "明确实现边界、依赖、数据要求和风险。",
    deliverable: "实现约束文档",
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
    label: "交付总览",
    short: "07",
    description: "汇总全部阶段产物和下一步建议。",
    deliverable: "交付总览文档",
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
      data.push(line.slice(5).trimStart());
    }
  }
  return { event, data: data.join("\n") };
}

function normalizeSseBuffer(value: string) {
  return value.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
}

export default function FlowDetailPage() {
  const params = useParams<{ id: string }>();
  const flowId = params.id;
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
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const loadedStageKeysRef = useRef<Set<string>>(new Set());
  const bootstrappingStageKeysRef = useRef<Set<string>>(new Set());

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
  const currentStageMessages = selectedStage
    ? (messagesByStage[selectedStage.stage_key]?.length
        ? messagesByStage[selectedStage.stage_key]
        : buildFallbackMessages(selectedStage))
    : [];
  const hasStageConclusion = currentStageMessages.some((message) => message.kind === "conclusion");

  const stageProgress = visibleStages.length
    ? Math.round((visibleStages.filter((stage) => stage.status === "approved").length / visibleStages.length) * 100)
    : 0;

  const currentStep = selectedStage
    ? visibleStages.findIndex((stage) => stage.stage_key === selectedStage.stage_key) + 1
    : 1;

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

    try {
      const res = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: draft ? JSON.stringify({ content: draft }) : undefined,
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

      while (true) {
        const { value, done } = await reader.read();
        buffer = normalizeSseBuffer(buffer + decoder.decode(value || new Uint8Array(), { stream: !done }));

        let separatorIndex = buffer.indexOf("\n\n");
        while (separatorIndex >= 0) {
          const rawEvent = buffer.slice(0, separatorIndex).trim();
          buffer = buffer.slice(separatorIndex + 2);
          if (rawEvent) {
            const parsed = parseSseChunk(rawEvent);
            if (parsed.event === "content") {
              setStreamByStage((current) => {
                const active = current[stageKey];
                if (!active) return current;
                return {
                  ...current,
                  [stageKey]: {
                    ...active,
                    content: active.content + parsed.data,
                  },
                };
              });
            } else if (parsed.event === "reasoning") {
              setStreamByStage((current) => {
                const active = current[stageKey];
                if (!active) return current;
                return {
                  ...current,
                  [stageKey]: {
                    ...active,
                    reasoning: active.reasoning + parsed.data,
                  },
                };
              });
            } else if (parsed.event === "complete") {
              const payload = JSON.parse(parsed.data) as StageStreamCompletePayload;
              loadedStageKeysRef.current.add(stageKey);
              updateStageInFlow(payload.stage);
              setMessagesByStage((current) => ({
                ...current,
                [stageKey]: [...(current[stageKey] || []), payload.message],
              }));
              setStreamByStage((current) => ({ ...current, [stageKey]: null }));
              void loadStageMessages(stageKey, true);
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
                content: active.content + parsed.data,
              },
            };
          });
        } else if (parsed.event === "reasoning") {
          setStreamByStage((current) => {
            const active = current[stageKey];
            if (!active) return current;
            return {
              ...current,
              [stageKey]: {
                ...active,
                reasoning: active.reasoning + parsed.data,
              },
            };
          });
        } else if (parsed.event === "complete") {
          const payload = JSON.parse(parsed.data) as StageStreamCompletePayload;
          loadedStageKeysRef.current.add(stageKey);
          updateStageInFlow(payload.stage);
          setMessagesByStage((current) => ({
            ...current,
            [stageKey]: [...(current[stageKey] || []), payload.message],
          }));
          setStreamByStage((current) => ({ ...current, [stageKey]: null }));
          void loadStageMessages(stageKey, true);
        } else if (parsed.event === "error") {
          throw new Error(parsed.data || "发送消息失败");
        }
      }
    } catch (err) {
      if (userMessage) {
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
    const stageMessages = messagesByStage[stageKey] || [];
    const hasAssistant = stageMessages.some((item) => item.role === "assistant" && item.content.trim());
    const isCurrentStage = flow?.current_stage === stageKey;
    if (hasAssistant || !isCurrentStage || bootstrappingStageKeysRef.current.has(stageKey) || liveStreamState) {
      return;
    }

    bootstrappingStageKeysRef.current.add(stageKey);
    setSending(true);
    setError("");
    void consumeStageStream(stageKey, `/api/flows/${flowId}/stages/${stageKey}/bootstrap-stream`)
      .catch((err: any) => {
        setError(err.message || "阶段启动失败");
      })
      .finally(() => {
        bootstrappingStageKeysRef.current.delete(stageKey);
        setSending(false);
      });
  }, [selectedStage, messagesByStage, flow?.current_stage, messagesLoading, sending, liveStreamState, flowId]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [selectedStage?.stage_key, currentStageMessages.length, liveStreamState?.content, liveStreamState?.reasoning, messagesLoading]);

  const submitMessage = async (event: FormEvent) => {
    event.preventDefault();
    if (!selectedStage || sending) return;
    const draft = (draftByStage[selectedStage.stage_key] || "").trim();
    if (!draft) return;

    setSending(true);
    setError("");
    const stageKey = selectedStage.stage_key;
    try {
      await consumeStageStream(stageKey, `/api/flows/${flowId}/stages/${stageKey}/messages/stream`, draft);
    } catch (err: any) {
      setError(err.message || "发送消息失败");
    } finally {
      setSending(false);
    }
  };

  const approveStage = async () => {
    if (!selectedStage || approving) return;
    setApproving(true);
    setError("");
    const stageKey = selectedStage.stage_key;
    try {
      if (!hasStageConclusion) {
        await consumeStageStream(stageKey, `/api/flows/${flowId}/stages/${stageKey}/finalize-stream`, undefined, "conclusion");
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

  return (
    <div className="min-h-screen bg-[linear-gradient(117deg,#fcfaff,#e7f1ff)] dark:bg-slate-950">
      <TopNav />
      <main className="mx-auto max-w-[1480px] px-5 pb-10 pt-24 lg:px-8">
        <div className="mb-5 flex items-center justify-between gap-4">
          <Link
            href="/flows"
            className={cn(
              buttonVariants({ variant: "outline" }),
              "h-auto rounded-full bg-white px-4 py-2.5 text-sm text-slate-700 hover:border-violet-200 hover:text-violet-700 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-200",
            )}
          >
            <ArrowLeft size={16} />
            返回流程列表
          </Link>
          <div className="hidden items-center gap-3 text-sm text-slate-500 md:flex">
            <span>{currentMeta.label}</span>
            <span className="text-slate-300">/</span>
            <span>{currentMeta.deliverable}</span>
          </div>
        </div>

        {error && (
          <div className="mb-5 rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-600 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-300">
            {error}
          </div>
        )}

        <div className="grid gap-5 xl:grid-cols-[280px_minmax(0,1fr)]">
          <aside className="space-y-4">
            <Card className="rounded-[30px] border-0 bg-white text-slate-950 shadow-none dark:bg-slate-900 dark:text-slate-50">
              <CardHeader className="p-4 pb-0">
                <div className="text-base font-semibold leading-6">
                  {flow.name}
                </div>
                <div className="mt-1 overflow-hidden text-xs leading-5 text-slate-500 [display:-webkit-box] [-webkit-box-orient:vertical] [-webkit-line-clamp:2] dark:text-slate-400">
                  {flow.description || "这条流程还没有补充详细背景。"}
                </div>
              </CardHeader>
              <CardContent className="p-4 pt-4">
                <div className="mb-1 flex items-center justify-between text-[11px] font-medium text-slate-500 dark:text-slate-400">
                  <span>总体进度</span>
                  <span>{stageProgress}%</span>
                </div>
                <div className="h-1.5 overflow-hidden rounded-full bg-slate-100 dark:bg-white/10">
                  <div className="h-full rounded-full bg-violet-500 transition-all" style={{ width: `${stageProgress}%` }} />
                </div>
              </CardContent>
            </Card>

            <Card className="rounded-[30px] border-slate-100 bg-white p-3 shadow-none dark:border-slate-800 dark:bg-slate-900">
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
                      className={`group relative h-auto w-full justify-start rounded-[22px] border px-3 py-3 text-left whitespace-normal transition ${
                        active
                          ? "border-violet-200 bg-violet-50 text-violet-950 shadow-sm dark:border-violet-400/25 dark:bg-violet-500/10 dark:text-violet-50"
                          : "border-transparent bg-white text-slate-700 hover:border-slate-200 hover:bg-slate-50 dark:bg-slate-900 dark:text-slate-200 dark:hover:border-slate-700 dark:hover:bg-slate-800/70"
                      }`}
                    >
                      <div className="flex items-center gap-3">
                        <div
                          className={`flex size-9 shrink-0 items-center justify-center rounded-full border text-xs font-semibold transition ${
                            approved
                              ? "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-400/20 dark:bg-emerald-500/10 dark:text-emerald-300"
                              : active
                                ? "border-violet-200 bg-white text-violet-700 dark:border-violet-400/20 dark:bg-slate-950 dark:text-violet-300"
                                : "border-slate-200 bg-slate-50 text-slate-500 group-hover:border-violet-100 group-hover:text-violet-600 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-400"
                          }`}
                        >
                          {approved ? <Check size={15} /> : String(index + 1).padStart(2, "0")}
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center justify-between gap-2">
                            <div className="truncate text-sm font-semibold">{meta.label}</div>
                            <Icon
                              size={15}
                              className={active ? "shrink-0 text-violet-500" : "shrink-0 text-slate-300 group-hover:text-violet-300"}
                            />
                          </div>
                          <div className="mt-0.5 truncate text-xs text-slate-400 dark:text-slate-500">
                            {meta.deliverable}
                          </div>
                        </div>
                      </div>
                    </Button>
                  );
                })}
              </div>
            </Card>
          </aside>

          <Card className="rounded-[30px] border-slate-100 bg-white shadow-[0_18px_60px_rgba(15,23,42,0.06)] backdrop-blur dark:border-slate-800 dark:bg-slate-900/92">
            <div className="border-b border-slate-200/80 px-5 py-5 dark:border-slate-800">
              <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
                <div>
                  <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-400">
                    <span>阶段 {String(currentStep).padStart(2, "0")}</span>
                    <span className="text-slate-300">·</span>
                    <span>{currentMeta.deliverable}</span>
                  </div>
                  <h1 className="mt-2 text-2xl font-semibold tracking-tight text-slate-950 dark:text-slate-50">
                    {currentMeta.label}
                  </h1>
                  <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-500 dark:text-slate-400">
                    {currentMeta.description}
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant={STATUS_BADGE_VARIANT[selectedStage.status]} className="px-3 py-1">
                    {STATUS_LABEL[selectedStage.status]}
                  </Badge>
                  <Badge variant="outline" className="border-transparent bg-slate-100 px-3 py-1 text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                    {flow.target_platform}
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

            <div className="flex min-h-[780px] flex-col">
              <div className="flex-1 space-y-4 overflow-y-auto px-5 py-5">
                {messagesLoading && currentStageMessages.length === 0 && (
                  <div className="flex items-center gap-2 text-sm text-slate-400">
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
                      <article className={`max-w-[82%] ${isUser ? "order-2" : "order-1"}`}>
                        <div className={`mb-2 flex items-center gap-2 text-xs text-slate-400 ${isUser ? "justify-end" : "justify-start"}`}>
                          {!isUser && (
                            <span className="flex size-8 items-center justify-center rounded-full bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900">
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
                          className={`rounded-[24px] px-4 py-3.5 text-sm leading-7 ${
                            isUser
                              ? "bg-sky-600 text-white"
                              : isConclusion
                                ? "border border-emerald-200 bg-emerald-50 text-emerald-950 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-100"
                                : "border border-slate-200 bg-slate-50 text-slate-900 dark:border-slate-800 dark:bg-slate-950/70 dark:text-slate-100"
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
                          {isStreamingAssistant && liveStreamState?.reasoning ? (
                            <div className="mb-3 rounded-2xl border border-current/10 bg-white/50 px-3 py-2 text-xs leading-6 opacity-80 dark:bg-black/10">
                              <div className="mb-1 font-medium">思路摘要</div>
                              <div className="whitespace-pre-wrap">{liveStreamState.reasoning}</div>
                            </div>
                          ) : null}
                          <div className="whitespace-pre-wrap">{message.content}</div>
                          {isStreamingAssistant && (sending || approving) && (
                            <div className="mt-3 flex items-center gap-2 text-xs opacity-70">
                              <Loader2 size={12} className="animate-spin" />
                              正在生成...
                            </div>
                          )}
                          {message.artifact_url && (
                            <a
                              href={withAuthToken(message.artifact_url)}
                              target="_blank"
                              rel="noreferrer"
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
                  <div className="rounded-[24px] border border-dashed border-slate-200 px-4 py-6 text-sm text-slate-400 dark:border-slate-800 dark:text-slate-500">
                    当前阶段还没有输出内容。
                  </div>
                )}

                {selectedStage.recommendation?.artifacts?.length ? (
                  <div className="flex flex-wrap gap-2 pt-2">
                    {selectedStage.recommendation.artifacts.map((artifact, index) =>
                      artifact.url ? (
                        <a
                          key={`${selectedStage.id}-${index}-${artifact.url}`}
                          href={withAuthToken(artifact.url)}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
                        >
                          <Paperclip size={12} />
                          {artifact.label || artifact.type || "阶段附件"}
                        </a>
                      ) : null,
                    )}
                  </div>
                ) : null}

                <div ref={messagesEndRef} />
              </div>

              <form onSubmit={submitMessage} className="border-t border-slate-200/80 px-5 py-4 dark:border-slate-800">
                <div className="rounded-[26px] border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950/70">
                  <div className="mb-3 flex items-center justify-between gap-3">
                    <div className="flex items-center gap-2 text-sm font-semibold text-slate-900 dark:text-slate-100">
                      <Send size={16} className="text-sky-500" />
                      继续当前阶段
                    </div>
                    <div className="text-xs text-slate-400">
                      你可以继续补充、纠偏，或在这一阶段已经完整时直接确认进入下一阶段
                    </div>
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
                      当前阶段会持续对话；只有确认后才进入下一阶段。
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Button
                        variant="outline"
                        type="submit"
                        disabled={sending || approving || !draft.trim()}
                        className="h-auto rounded-full border-slate-200 bg-white px-4 py-2.5 text-sm text-slate-700 hover:border-sky-200 hover:text-sky-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
                      >
                        {sending ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
                        发送
                      </Button>
                      <Button
                        type="button"
                        onClick={approveStage}
                        disabled={approving || sending || selectedStage.status === "approved"}
                        className="h-auto rounded-full bg-sky-600 px-4 py-2.5 text-sm text-white hover:bg-sky-500"
                      >
                        {approving ? <Loader2 size={16} className="animate-spin" /> : <Check size={16} />}
                        {hasStageConclusion ? "确认进入下一阶段" : "生成阶段结论"}
                      </Button>
                    </div>
                  </div>
                </div>
              </form>
            </div>
          </Card>
        </div>
      </main>
    </div>
  );
}
