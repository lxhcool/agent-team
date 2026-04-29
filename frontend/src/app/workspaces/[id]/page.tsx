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
  RefreshCcw,
  Rocket,
  Send,
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
    recommended?: boolean;
  }[];
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
  feedback_used?: string;
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
  awaiting_confirmation: "bg-indigo-50 text-indigo-600 dark:bg-indigo-500/15 dark:text-indigo-300",
  approved: "bg-emerald-50 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-300",
  revision_requested: "bg-amber-50 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300",
  skipped: "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400",
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

export default function WorkspaceDetailPage() {
  const params = useParams<{ id: string }>();
  const workspaceId = params.id;
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [selectedKey, setSelectedKey] = useState<StageKey | null>(null);
  const [feedback, setFeedback] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [generatingPrototype, setGeneratingPrototype] = useState(false);
  const [generatingDesigns, setGeneratingDesigns] = useState(false);
  const [error, setError] = useState("");

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

  const selectedStage = useMemo(() => {
    if (!workspace?.stages?.length) return null;
    return workspace.stages.find((stage) => stage.stage_key === selectedKey) || workspace.stages[0];
  }, [selectedKey, workspace]);

  useEffect(() => {
    setFeedback(selectedStage?.user_feedback || "");
  }, [selectedStage?.id, selectedStage?.user_feedback]);

  const approveStage = async () => {
    if (!selectedStage || saving) return;
    setSaving(true);
    setError("");
    try {
      const res = await fetch(
        `/api/workspaces/${workspaceId}/stages/${selectedStage.stage_key}/approve`,
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
      setError(err.message || "确认失败");
    } finally {
      setSaving(false);
    }
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
        throw new Error(data.detail || "提交反馈失败");
      }
      await loadWorkspace();
    } catch (err: any) {
      setError(err.message || "提交反馈失败");
    } finally {
      setSaving(false);
    }
  };

  const generateStage = async () => {
    if (!selectedStage || generating) return;
    setGenerating(true);
    setError("");
    try {
      const res = await fetch(
        `/api/workspaces/${workspaceId}/stages/${selectedStage.stage_key}/generate`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ instruction: feedback.trim() || null }),
        }
      );
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "生成推荐失败");
      }
      const stage = await res.json();
      setWorkspace((current) => {
        if (!current) return current;
        return {
          ...current,
          stages: current.stages.map((item) => item.id === stage.id ? stage : item),
        };
      });
      setSelectedKey(stage.stage_key);
      setFeedback(stage.user_feedback || "");
    } catch (err: any) {
      setError(err.message || "生成推荐失败");
    } finally {
      setGenerating(false);
    }
  };

  const generatePrototype = async () => {
    if (generatingPrototype) return;
    setGeneratingPrototype(true);
    setError("");
    try {
      const res = await fetch(`/api/workspaces/${workspaceId}/prototype`, { method: "POST" });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "生成 HTML 原型失败");
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
      setError(err.message || "生成 HTML 原型失败");
    } finally {
      setGeneratingPrototype(false);
    }
  };

  const generateDesigns = async () => {
    if (generatingDesigns) return;
    setGeneratingDesigns(true);
    setError("");
    try {
      const res = await fetch(`/api/workspaces/${workspaceId}/designs`, { method: "POST" });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "生成设计稿失败");
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
      setError(err.message || "生成设计稿失败");
    } finally {
      setGeneratingDesigns(false);
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
          <Link href="/workspaces" className="inline-flex items-center gap-2 text-sm text-slate-500 hover:text-indigo-500">
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

  const progress = workspace.stage_total
    ? Math.round((workspace.stage_approved / workspace.stage_total) * 100)
    : 0;
  const StageIcon = STAGE_ICON[selectedStage.stage_key];
  const focus = selectedStage.recommendation?.focus || [];
  const options = selectedStage.recommendation?.options || [];
  const artifacts = selectedStage.recommendation?.artifacts || [];
  const prototypeArtifact = artifacts.find((artifact) =>
    artifact.type === "prototype_html" && artifact.status === "ready" && artifact.url
  );
  const desktopDesign = artifacts.find((artifact) =>
    artifact.type === "desktop_design" && artifact.status === "ready" && artifact.url
  );
  const mobileDesign = artifacts.find((artifact) =>
    artifact.type === "mobile_design" && artifact.status === "ready" && artifact.url
  );
  const prototypeUrl = prototypeArtifact?.url ? withAuthToken(prototypeArtifact.url) : "";
  const desktopDesignUrl = desktopDesign?.url ? withAuthToken(desktopDesign.url) : "";
  const mobileDesignUrl = mobileDesign?.url ? withAuthToken(mobileDesign.url) : "";

  return (
    <div className="min-h-screen bg-slate-50 dark:bg-slate-950">
      <TopNav />
      <main className="mx-auto max-w-7xl px-6 pb-16 pt-24">
        <Link
          href="/workspaces"
          className="mb-6 inline-flex items-center gap-2 text-sm font-medium text-slate-500 transition hover:text-indigo-500"
        >
          <ArrowLeft size={16} />
          返回工作区
        </Link>

        <section className="mb-6 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
            <div>
              <div className="mb-2 inline-flex items-center gap-2 rounded-full bg-indigo-50 px-3 py-1 text-xs font-medium text-indigo-600 dark:bg-indigo-500/15 dark:text-indigo-300">
                <Sparkles size={14} />
                AI 开发团队流程
              </div>
              <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-slate-100">
                {workspace.name}
              </h1>
              <p className="mt-2 max-w-3xl text-sm text-slate-500 dark:text-slate-400">
                {workspace.description || "这个工作区还没有补充详细需求。"}
              </p>
            </div>
            <div className="min-w-[220px]">
              <div className="mb-2 flex items-center justify-between text-xs text-slate-500 dark:text-slate-400">
                <span>确认进度</span>
                <span>{workspace.stage_approved}/{workspace.stage_total}</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                <div className="h-full rounded-full bg-indigo-500" style={{ width: `${progress}%` }} />
              </div>
              <div className="mt-3 rounded-full bg-slate-100 px-3 py-1 text-center text-xs font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                {workspace.target_platform}
              </div>
            </div>
          </div>
        </section>

        {error && (
          <div className="mb-6 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-600 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-300">
            {error}
          </div>
        )}

        <div className="grid gap-6 lg:grid-cols-[340px_1fr]">
          <aside className="rounded-2xl border border-slate-200 bg-white p-3 shadow-sm dark:border-slate-800 dark:bg-slate-900">
            <div className="px-3 py-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
              阶段确认
            </div>
            <div className="space-y-1">
              {workspace.stages.map((stage) => {
                const Icon = STAGE_ICON[stage.stage_key];
                const active = stage.stage_key === selectedStage.stage_key;
                return (
                  <button
                    key={stage.id}
                    onClick={() => setSelectedKey(stage.stage_key)}
                    className={`flex w-full items-center gap-3 rounded-xl px-3 py-3 text-left transition ${
                      active
                        ? "bg-indigo-50 text-indigo-600 dark:bg-indigo-500/15 dark:text-indigo-300"
                        : "text-slate-600 hover:bg-slate-50 dark:text-slate-300 dark:hover:bg-slate-800/70"
                    }`}
                  >
                    <span className={`flex size-8 items-center justify-center rounded-lg ${
                      active ? "bg-white dark:bg-slate-900" : "bg-slate-100 dark:bg-slate-800"
                    }`}>
                      <Icon size={16} />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm font-medium">{stage.title}</span>
                      <span className="mt-0.5 block truncate text-xs opacity-70">{stage.description}</span>
                    </span>
                    {stage.status === "approved" ? (
                      <CheckCircle2 size={16} className="text-emerald-500" />
                    ) : stage.status === "awaiting_confirmation" ? (
                      <Clock3 size={16} className="text-indigo-500" />
                    ) : null}
                  </button>
                );
              })}
            </div>
          </aside>

          <section className="min-w-0 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-800 dark:bg-slate-900">
            <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
              <div className="flex items-start gap-3">
                <span className="flex size-11 items-center justify-center rounded-xl bg-indigo-50 text-indigo-600 dark:bg-indigo-500/15 dark:text-indigo-300">
                  <StageIcon size={20} />
                </span>
                <div>
                  <div className="mb-2 flex flex-wrap items-center gap-2">
                    <h2 className="text-xl font-semibold text-slate-900 dark:text-slate-100">
                      {selectedStage.title}
                    </h2>
                    <span className={`rounded-full px-2.5 py-1 text-xs font-medium ${STATUS_CLASS[selectedStage.status]}`}>
                      {STATUS_LABEL[selectedStage.status]}
                    </span>
                  </div>
                  <p className="text-sm text-slate-500 dark:text-slate-400">
                    {selectedStage.description}
                  </p>
                </div>
              </div>

              <div className="flex flex-col gap-2 sm:flex-row">
                {selectedStage.stage_key === "prototype" && (
                  <>
                    <button
                      onClick={generateDesigns}
                      disabled={generatingDesigns}
                      className="inline-flex items-center justify-center gap-2 rounded-xl border border-emerald-200 bg-white px-4 py-2.5 text-sm font-medium text-emerald-700 shadow-sm transition hover:bg-emerald-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-emerald-500/30 dark:bg-slate-900 dark:text-emerald-300 dark:hover:bg-emerald-500/10"
                    >
                      {generatingDesigns ? <Loader2 size={16} className="animate-spin" /> : <Palette size={16} />}
                      生成设计稿
                    </button>
                    <button
                      onClick={generatePrototype}
                      disabled={generatingPrototype}
                      className="inline-flex items-center justify-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 shadow-sm transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300 dark:hover:bg-slate-800"
                    >
                      {generatingPrototype ? <Loader2 size={16} className="animate-spin" /> : <Eye size={16} />}
                      生成 HTML 原型
                    </button>
                  </>
                )}
                <button
                  onClick={generateStage}
                  disabled={generating}
                  className="inline-flex items-center justify-center gap-2 rounded-xl border border-indigo-200 bg-white px-4 py-2.5 text-sm font-medium text-indigo-600 shadow-sm transition hover:bg-indigo-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-indigo-500/30 dark:bg-slate-900 dark:text-indigo-300 dark:hover:bg-indigo-500/10"
                >
                  {generating ? <Loader2 size={16} className="animate-spin" /> : <Wand2 size={16} />}
                  生成推荐
                </button>
                <button
                  onClick={approveStage}
                  disabled={saving || selectedStage.status === "approved"}
                  className="inline-flex items-center justify-center gap-2 rounded-xl bg-indigo-600 px-4 py-2.5 text-sm font-medium text-white shadow-sm transition hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {saving ? <Loader2 size={16} className="animate-spin" /> : <Check size={16} />}
                  确认通过
                </button>
              </div>
            </div>

            {selectedStage.stage_key === "prototype" && (desktopDesignUrl || mobileDesignUrl) && (
              <div className="mb-6 rounded-2xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-950">
                <div className="mb-4">
                  <div className="text-sm font-semibold text-slate-900 dark:text-slate-100">设计稿</div>
                  <div className="text-xs text-slate-400">给用户确认视觉方向的桌面端和移动端页面图</div>
                </div>
                <div className="grid gap-4 xl:grid-cols-[1fr_340px]">
                  {desktopDesignUrl && (
                    <a href={desktopDesignUrl} target="_blank" rel="noreferrer" className="block overflow-hidden rounded-xl border border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-900">
                      <div className="border-b border-slate-200 px-3 py-2 text-xs font-medium text-slate-500 dark:border-slate-800 dark:text-slate-400">桌面端设计稿</div>
                      <img src={desktopDesignUrl} alt="桌面端设计稿" className="w-full bg-white" />
                    </a>
                  )}
                  {mobileDesignUrl && (
                    <a href={mobileDesignUrl} target="_blank" rel="noreferrer" className="block overflow-hidden rounded-xl border border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-900">
                      <div className="border-b border-slate-200 px-3 py-2 text-xs font-medium text-slate-500 dark:border-slate-800 dark:text-slate-400">移动端设计稿</div>
                      <img src={mobileDesignUrl} alt="移动端设计稿" className="mx-auto max-h-[520px] bg-white" />
                    </a>
                  )}
                </div>
              </div>
            )}

            {selectedStage.stage_key === "prototype" && prototypeUrl && (
              <div className="mb-6 overflow-hidden rounded-2xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-950">
                <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3 dark:border-slate-800">
                  <div>
                    <div className="text-sm font-semibold text-slate-900 dark:text-slate-100">HTML 原型预览</div>
                    <div className="text-xs text-slate-400">真实 HTML/CSS 页面，用作后续代码落地基础</div>
                  </div>
                  <a
                    href={prototypeUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-600 transition hover:border-indigo-200 hover:text-indigo-600 dark:border-slate-700 dark:text-slate-300"
                  >
                    新窗口打开
                  </a>
                </div>
                <iframe
                  title="Workspace HTML Prototype"
                  src={prototypeUrl}
                  className="h-[520px] w-full bg-white"
                />
              </div>
            )}

            <div className="grid gap-4 xl:grid-cols-2">
              <div className="rounded-2xl border border-slate-200 bg-slate-50 p-5 dark:border-slate-800 dark:bg-slate-950/60">
                <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-900 dark:text-slate-100">
                  <Sparkles size={16} className="text-indigo-500" />
                  推荐方案
                </div>
                <p className="text-sm leading-6 text-slate-600 dark:text-slate-300">
                  {selectedStage.recommendation?.summary || "等待 AI 团队生成推荐方案。"}
                </p>
                {selectedStage.recommendation?.source && (
                  <div className="mt-3 inline-flex max-w-full items-center gap-1 rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-medium text-slate-500 dark:bg-slate-800 dark:text-slate-400">
                    来源：{selectedStage.recommendation.source === "llm" ? "模型生成" : "模板兜底"}
                    {selectedStage.recommendation.model ? ` · ${selectedStage.recommendation.model}` : ""}
                  </div>
                )}
                <div className="mt-4 rounded-xl bg-white p-3 text-sm text-slate-600 dark:bg-slate-900 dark:text-slate-300">
                  {selectedStage.recommendation?.recommended_action || "建议先确认本阶段方向。"}
                </div>
                {focus.length > 0 && (
                  <div className="mt-4 flex flex-wrap gap-2">
                    {focus.map((item) => (
                      <span
                        key={item}
                        className="rounded-full bg-indigo-50 px-2.5 py-1 text-xs font-medium text-indigo-600 dark:bg-indigo-500/15 dark:text-indigo-300"
                      >
                        {item}
                      </span>
                    ))}
                  </div>
                )}
                {options.length > 0 && (
                  <div className="mt-5 space-y-2">
                    <div className="text-xs font-semibold text-slate-400">可选方向</div>
                    {options.map((option) => (
                      <div
                        key={option.title}
                        className={`rounded-xl border p-3 ${
                          option.recommended
                            ? "border-indigo-200 bg-indigo-50/70 dark:border-indigo-500/30 dark:bg-indigo-500/10"
                            : "border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900"
                        }`}
                      >
                        <div className="mb-1 flex items-center justify-between gap-2">
                          <span className="text-sm font-medium text-slate-900 dark:text-slate-100">{option.title}</span>
                          {option.recommended && (
                            <span className="rounded-full bg-indigo-600 px-2 py-0.5 text-[10px] font-medium text-white">推荐</span>
                          )}
                        </div>
                        <p className="text-xs leading-5 text-slate-500 dark:text-slate-400">{option.description}</p>
                      </div>
                    ))}
                  </div>
                )}
                {artifacts.length > 0 && (
                  <div className="mt-5 space-y-2">
                    <div className="text-xs font-semibold text-slate-400">后续视觉产物</div>
                    {artifacts.map((artifact) => (
                      <div
                        key={`${artifact.type}-${artifact.label}`}
                        className="flex items-center justify-between rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs dark:border-slate-800 dark:bg-slate-900"
                      >
                        <span className="text-slate-600 dark:text-slate-300">{artifact.label || artifact.type}</span>
                        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-slate-500 dark:bg-slate-800 dark:text-slate-400">
                          {artifact.status || "pending"}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <div className="rounded-2xl border border-slate-200 bg-slate-50 p-5 dark:border-slate-800 dark:bg-slate-950/60">
                <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-900 dark:text-slate-100">
                  <MessageSquare size={16} className="text-indigo-500" />
                  当前产物
                </div>
                <div className="min-h-[148px] whitespace-pre-wrap rounded-xl bg-white p-4 text-sm leading-6 text-slate-600 dark:bg-slate-900 dark:text-slate-300">
                  {selectedStage.content || "该阶段还没有生成具体产物。后续会在这里展示 PRD、页面结构、UI 截图、技术方案或代码执行结果。"}
                </div>
                {selectedStage.approved_at && (
                  <div className="mt-3 text-xs text-slate-400">
                    已确认于 {formatDate(selectedStage.approved_at)}
                  </div>
                )}
              </div>
            </div>

            <form onSubmit={requestRevision} className="mt-6 rounded-2xl border border-slate-200 bg-slate-50 p-5 dark:border-slate-800 dark:bg-slate-950/60">
              <label className="mb-2 flex items-center gap-2 text-sm font-semibold text-slate-900 dark:text-slate-100">
                <RefreshCcw size={16} className="text-amber-500" />
                需要调整
              </label>
              <textarea
                value={feedback}
                onChange={(event) => setFeedback(event.target.value)}
                rows={4}
                placeholder="告诉 AI 团队你希望怎么改，例如：风格更年轻一点、页面少一点、先不要做支付。"
                className="w-full resize-none rounded-xl border border-slate-200 bg-white px-3 py-3 text-sm text-slate-900 outline-none transition focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:focus:ring-indigo-500/20"
              />
              <div className="mt-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <p className="text-xs text-slate-400">
                  打回后该阶段会标记为需调整，后续生成逻辑会使用这段反馈。
                </p>
                <button
                  type="submit"
                  disabled={saving || !feedback.trim()}
                  className="inline-flex items-center justify-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 transition hover:border-amber-200 hover:text-amber-700 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
                >
                  {saving ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
                  提交反馈
                </button>
              </div>
            </form>
          </section>
        </div>
      </main>
    </div>
  );
}
