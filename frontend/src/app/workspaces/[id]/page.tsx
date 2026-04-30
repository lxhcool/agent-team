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
  awaiting_confirmation: "bg-indigo-50 text-indigo-600 dark:bg-indigo-500/15 dark:text-indigo-300",
  approved: "bg-emerald-50 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-300",
  revision_requested: "bg-amber-50 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300",
  skipped: "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400",
};

const AUTO_STOP_STAGES = new Set<StageKey>(["prototype", "acceptance", "deployment"]);

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
      setError(err.message || "切换方案失败");
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
      setError(err.message || "确认失败");
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
      await loadWorkspace();
    } catch (err: any) {
      setError(err.message || "提交修改意见失败");
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
      setError(err.message || "生成推荐失败");
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
      setError(err.message || "重新绑定目录失败");
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
  const activeOption = options.find((option) => option.title === selectedOptionTitle) || null;
  const activeContent = activeOption?.content || selectedStage.content;
  const hasGeneratedRecommendation = Boolean(selectedStage.recommendation?.source);
  const artifacts = selectedStage.recommendation?.artifacts || [];
  const stageAgentMeta = STAGE_AGENT_META[selectedStage.stage_key];
  const stageTasks = selectedStage.recommendation?.task_items || [];
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
                项目阶段确认
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
                      className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs text-slate-900 outline-none transition focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:focus:ring-indigo-500/20"
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
                        className="inline-flex items-center justify-center rounded-xl bg-indigo-600 px-3 py-2 text-xs font-medium text-white transition hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
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
                  onClick={generateSelectedStage}
                  disabled={generating}
                  className="inline-flex items-center justify-center gap-2 rounded-xl border border-indigo-200 bg-white px-4 py-2.5 text-sm font-medium text-indigo-600 shadow-sm transition hover:bg-indigo-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-indigo-500/30 dark:bg-slate-900 dark:text-indigo-300 dark:hover:bg-indigo-500/10"
                >
                  {generating ? <Loader2 size={16} className="animate-spin" /> : <Wand2 size={16} />}
                  生成推荐
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
                  className="inline-flex items-center justify-center gap-2 rounded-xl bg-indigo-600 px-4 py-2.5 text-sm font-medium text-white shadow-sm transition hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {saving ? <Loader2 size={16} className="animate-spin" /> : <Check size={16} />}
                  确认通过
                </button>
              </div>
            </div>

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

            <div className="mb-6 rounded-2xl border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950/60">
              <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-900 dark:text-slate-100">
                <ShieldCheck size={16} className="text-indigo-500" />
                当前阶段 Agent
              </div>
              <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                <div>
                  <div className="text-sm font-medium text-slate-900 dark:text-slate-100">
                    {stageAgentMeta.label}
                  </div>
                  <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                    默认标识：{selectedStage.recommendation?.agent_name || stageAgentMeta.name}
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

            {(selectedStage.stage_key === "development" || selectedStage.stage_key === "acceptance") && developmentPreviewUrl && (
              <div className="mb-6 overflow-hidden rounded-2xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-950">
                <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3 dark:border-slate-800">
                  <div>
                    <div className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                      {selectedStage.stage_key === "development" ? "开发阶段预览" : "验收预览"}
                    </div>
                    <div className="text-xs text-slate-400">当前阶段绑定的真实 HTML 预览产物</div>
                  </div>
                  <a
                    href={developmentPreviewUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-600 transition hover:border-indigo-200 hover:text-indigo-600 dark:border-slate-700 dark:text-slate-300"
                  >
                    新窗口打开
                  </a>
                </div>
                <iframe
                  title="Workspace Development Preview"
                  src={developmentPreviewUrl}
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
                {(selectedStage.recommendation?.execution_session_id || selectedStage.recommendation?.project_path) && (
                  <div className="mt-4 rounded-xl border border-slate-200 bg-white p-3 text-xs text-slate-600 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300">
                    {selectedStage.recommendation.execution_session_id && (
                      <div>执行会话：{selectedStage.recommendation.execution_session_id}</div>
                    )}
                    {selectedStage.recommendation.project_path && (
                      <div className="mt-1 break-all">项目目录：{selectedStage.recommendation.project_path}</div>
                    )}
                    {selectedStage.recommendation.checkpoint_id && (
                      <div className="mt-1">Checkpoint：{selectedStage.recommendation.checkpoint_id}</div>
                    )}
                  </div>
                )}
                <div className="mt-4 rounded-xl bg-white p-3 text-sm text-slate-600 dark:bg-slate-900 dark:text-slate-300">
                  {activeOption
                    ? `当前已选方案：${activeOption.title}。${activeOption.description}`
                    : (selectedStage.recommendation?.recommended_action || "建议先确认本阶段方向。")}
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
                {hasGeneratedRecommendation && options.length > 0 && (
                  <div className="mt-5 space-y-2">
                    <div className="text-xs font-semibold text-slate-400">可选方向</div>
                    {options.map((option) => (
                      <button
                        key={option.title}
                        type="button"
                        onClick={() => selectStageOption(selectedStage, option.title)}
                        disabled={selectingOption}
                        aria-pressed={selectedOptionTitle === option.title}
                        className={`w-full rounded-xl border p-3 text-left transition ${
                          selectedOptionTitle === option.title
                            ? "border-indigo-300 bg-indigo-50 shadow-sm shadow-indigo-100/60 dark:border-indigo-400/40 dark:bg-indigo-500/10"
                            : option.recommended
                              ? "border-indigo-200 bg-indigo-50/70 dark:border-indigo-500/30 dark:bg-indigo-500/10"
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
                              <span className="rounded-full bg-indigo-600 px-2 py-0.5 text-[10px] font-medium text-white">推荐</span>
                            )}
                          </div>
                        </div>
                        <p className="text-xs leading-5 text-slate-500 dark:text-slate-400">{option.description}</p>
                      </button>
                    ))}
                  </div>
                )}
                {artifacts.length > 0 && (
                  <div className="mt-5 space-y-2">
                    <div className="text-xs font-semibold text-slate-400">阶段产物</div>
                    {artifacts.map((artifact) => (
                      <div
                        key={`${artifact.type}-${artifact.label}`}
                        className="flex items-center justify-between rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs dark:border-slate-800 dark:bg-slate-900"
                      >
                        <div className="min-w-0">
                          <div className="truncate text-slate-600 dark:text-slate-300">{artifact.label || artifact.type}</div>
                          {artifact.url && (
                            <a
                              href={withAuthToken(artifact.url)}
                              target="_blank"
                              rel="noreferrer"
                              className="mt-1 inline-block text-[11px] text-indigo-600 hover:underline dark:text-indigo-300"
                            >
                              打开产物
                            </a>
                          )}
                        </div>
                        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-slate-500 dark:bg-slate-800 dark:text-slate-400">
                          {artifact.status || "pending"}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
                {stageTasks.length > 0 && (
                  <div className="mt-5 space-y-2">
                    <div className="text-xs font-semibold text-slate-400">执行任务</div>
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
                            {task.assigned_agent ? `Agent: ${task.assigned_agent}` : ""}
                            {task.assigned_agent && task.result_summary ? " · " : ""}
                            {task.result_summary || ""}
                          </div>
                        )}
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
                  {activeContent || "该阶段还没有生成具体产物。后续会在这里展示 PRD、页面结构、UI 截图、技术方案或代码执行结果。"}
                </div>
                {selectedStage.approved_at && (
                  <div className="mt-3 text-xs text-slate-400">
                    已确认于 {formatDate(selectedStage.approved_at)}
                  </div>
                )}
                {!hasGeneratedRecommendation && (
                  <div className="mt-3 text-xs text-slate-400">
                    先生成推荐方案，再选择一个方案并确认进入下一阶段。
                  </div>
                )}
                {(selectedStage.stage_key === "development" || selectedStage.stage_key === "acceptance") && (
                  <div className="mt-4 flex flex-wrap gap-2">
                    {developmentReportUrl && (
                      <a
                        href={developmentReportUrl}
                        target="_blank"
                        rel="noreferrer"
                        className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 transition hover:border-indigo-200 hover:text-indigo-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
                      >
                        打开开发执行记录
                      </a>
                    )}
                    {acceptanceReportUrl && (
                      <a
                        href={acceptanceReportUrl}
                        target="_blank"
                        rel="noreferrer"
                        className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 transition hover:border-indigo-200 hover:text-indigo-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
                      >
                        打开验收报告
                      </a>
                    )}
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
                placeholder="告诉 AI 团队你希望怎么改，例如：风格更年轻一点、页面少一点、先不要做支付、移动端按钮再明显一点。"
                className="w-full resize-none rounded-xl border border-slate-200 bg-white px-3 py-3 text-sm text-slate-900 outline-none transition focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:focus:ring-indigo-500/20"
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
