"use client";

import { FormEvent, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ArrowRight,
  CheckCircle2,
  Trash2,
  FolderKanban,
  Loader2,
  Plus,
  Sparkles,
  Import,
} from "lucide-react";

import { TopNav } from "../components/topnav";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

type WorkspaceStageKey =
  | "requirements"
  | "product"
  | "ui_direction"
  | "prototype"
  | "technical"
  | "development"
  | "acceptance"
  | "deployment";

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
  status: "active" | "archived";
  current_stage: WorkspaceStageKey;
  stage_total: number;
  stage_approved: number;
  updated_at: string | null;
};

const STAGE_LABEL: Record<WorkspaceStageKey, string> = {
  requirements: "明确方向",
  product: "补全结构",
  ui_direction: "调整页面风格",
  prototype: "生成页面预览",
  technical: "整理扩展边界",
  development: "完善前端结果",
  acceptance: "检查与修正",
  deployment: "交付预览版本",
};

const PLATFORM_LABEL: Record<string, string> = {
  website: "网站",
  miniapp: "小程序",
  dashboard: "管理后台",
  app: "应用",
};

function formatDate(value: string | null) {
  if (!value) return "刚刚更新";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export default function WorkspacesPage() {
  const router = useRouter();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [importing, setImporting] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [targetPlatform, setTargetPlatform] = useState("website");
  const [storageMode, setStorageMode] = useState<"server" | "local">("server");
  const [rootPath, setRootPath] = useState("");
  const [importPath, setImportPath] = useState("");
  const [isDesktop, setIsDesktop] = useState(false);

  useEffect(() => {
    setIsDesktop(Boolean(window.teamAgentDesktop?.isDesktop));
  }, []);

  const loadWorkspaces = async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/workspaces");
      if (!res.ok) throw new Error("获取工作区失败");
      setWorkspaces(await res.json());
    } catch (err: any) {
      setError(err.message || "获取工作区失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadWorkspaces();
  }, []);

  const handleDelete = async (workspace: Workspace) => {
    if (deletingId) return;
    const confirmed = window.confirm(`确认删除工作区「${workspace.name}」？此操作不可恢复。`);
    if (!confirmed) return;

    setDeletingId(workspace.id);
    setError("");
    try {
      const res = await fetch(`/api/workspaces/${workspace.id}`, { method: "DELETE" });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "删除工作区失败");
      }
      setWorkspaces((current) => current.filter((item) => item.id !== workspace.id));
    } catch (err: any) {
      setError(err.message || "删除工作区失败");
    } finally {
      setDeletingId(null);
    }
  };

  const handleCreate = async (event: FormEvent) => {
    event.preventDefault();
    if (!name.trim() || creating) return;

    setCreating(true);
    setError("");
    try {
      const res = await fetch("/api/workspaces", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: name.trim(),
          description: description.trim() || null,
          target_platform: targetPlatform,
          storage_mode: storageMode,
          root_path: storageMode === "local" ? rootPath.trim() || null : null,
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "创建工作区失败");
      }
      const data = await res.json();
      router.push(`/workspaces/${data.id}`);
    } catch (err: any) {
      setError(err.message || "创建工作区失败");
    } finally {
      setCreating(false);
    }
  };

  const chooseDirectory = async () => {
    try {
      const chosen = await window.teamAgentDesktop?.workspace?.chooseDirectory?.();
      if (chosen?.path) {
        setRootPath(chosen.path);
        setStorageMode("local");
      }
    } catch (err: any) {
      setError(err?.message || "选择目录失败");
    }
  };

  const chooseImportDirectory = async () => {
    try {
      const chosen = await window.teamAgentDesktop?.workspace?.chooseDirectory?.();
      if (chosen?.path) {
        setImportPath(chosen.path);
      }
    } catch (err: any) {
      setError(err?.message || "选择导入目录失败");
    }
  };

  const handleImport = async () => {
    if (!importPath.trim() || importing) return;
    setImporting(true);
    setError("");
    try {
      const res = await fetch("/api/workspaces/import-local", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ root_path: importPath.trim() }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "导入本地目录失败");
      }
      const data = await res.json();
      const workspace = data.workspace as Workspace;
      if (typeof window !== "undefined" && workspace?.id && data.message) {
        window.sessionStorage.setItem(`workspace_import_notice_${workspace.id}`, data.message);
      }
      router.push(`/workspaces/${workspace.id}`);
    } catch (err: any) {
      setError(err.message || "导入本地目录失败");
    } finally {
      setImporting(false);
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-indigo-50/30 dark:from-slate-950 dark:via-slate-950 dark:to-indigo-950/20">
      <TopNav />
      <main className="min-w-0 pt-14">
        <div className="mx-auto max-w-6xl px-6 py-8">
          {/* Header */}
          <div className="mb-6 flex items-center justify-between">
            <div>
              <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight text-slate-900 dark:text-slate-100">
                <div className="flex size-8 items-center justify-center rounded-lg bg-indigo-100 dark:bg-indigo-500/15">
                  <FolderKanban size={16} className="text-indigo-600 dark:text-indigo-400" />
                </div>
                工作区
              </h1>
              <p className="mt-1.5 text-sm text-slate-500 dark:text-slate-400">
                每个 Workspace 都是一件正在推进的任务。在这里整理方向、生成结果、持续修改。
              </p>
            </div>
            <div className="flex items-center gap-3">
              <span className="inline-flex items-center gap-1.5 rounded-full bg-indigo-50 dark:bg-indigo-500/10 px-3 py-1.5 text-xs font-semibold text-indigo-600 dark:text-indigo-400">
                <FolderKanban size={11} />
                {workspaces.length} 个工作区
              </span>
            </div>
          </div>

          {/* Error */}
          {error && (
            <div className="mb-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-600 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-300">
              {error}
            </div>
          )}

          {/* Main: Left + Right layout */}
          <div className="grid gap-6 lg:grid-cols-[320px_minmax(0,1fr)]">
            {/* Left: Create & Import */}
            <div className="space-y-4">
              {/* Create Form Card */}
              <div className="rounded-xl bg-white/70 dark:bg-slate-900/70 backdrop-blur-sm ring-1 ring-slate-200/60 dark:ring-slate-700/40">
                <div className="px-4 pt-4 pb-2">
                  <div className="flex items-center gap-2 text-sm font-semibold text-slate-900 dark:text-slate-100">
                    <Plus size={14} className="text-indigo-600 dark:text-indigo-400" />
                    创建工作区
                  </div>
                  <p className="mt-1 text-[11px] text-slate-400 dark:text-slate-500">
                    填写基本信息，开始一件新任务
                  </p>
                </div>
                <form onSubmit={handleCreate} className="space-y-3 px-4 pb-4">
                  <div>
                    <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-400">
                      任务名称
                    </label>
                    <input
                      value={name}
                      onChange={(event) => setName(event.target.value)}
                      placeholder="例如：个人博客、支付流程改造"
                      className="h-9 w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/50 px-3 text-sm text-slate-700 dark:text-slate-200 placeholder:text-slate-400 outline-none focus:border-indigo-300 dark:focus:border-indigo-500/40 focus:ring-2 focus:ring-indigo-500/10 transition-all duration-200"
                    />
                  </div>
                  <div>
                    <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-400">
                      任务目标
                    </label>
                    <input
                      value={description}
                      onChange={(event) => setDescription(event.target.value)}
                      placeholder="一句话描述你想完成什么"
                      className="h-9 w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/50 px-3 text-sm text-slate-700 dark:text-slate-200 placeholder:text-slate-400 outline-none focus:border-indigo-300 dark:focus:border-indigo-500/40 focus:ring-2 focus:ring-indigo-500/10 transition-all duration-200"
                    />
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-400">
                        任务类型
                      </label>
                      <Select value={targetPlatform} onValueChange={(v) => v != null && setTargetPlatform(v)}>
                        <SelectTrigger className="w-full h-9 text-sm">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectGroup>
                            <SelectItem value="website">网站</SelectItem>
                            <SelectItem value="miniapp">小程序</SelectItem>
                            <SelectItem value="dashboard">管理后台</SelectItem>
                            <SelectItem value="app">应用</SelectItem>
                          </SelectGroup>
                        </SelectContent>
                      </Select>
                    </div>
                    <div>
                      <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-400">
                        存储方式
                      </label>
                      <Select value={storageMode} onValueChange={(v) => v != null && setStorageMode(v as "server" | "local")}>
                        <SelectTrigger className="w-full h-9 text-sm">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectGroup>
                            <SelectItem value="server">服务器托管</SelectItem>
                            <SelectItem value="local">本地目录</SelectItem>
                          </SelectGroup>
                        </SelectContent>
                      </Select>
                    </div>
                  </div>
                  {storageMode === "local" && (
                    <div>
                      <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-400">
                        项目目录
                      </label>
                      <div className="flex gap-2">
                        <input
                          value={rootPath}
                          onChange={(event) => setRootPath(event.target.value)}
                          placeholder="/Users/you/projects/my-app"
                          className="h-9 min-w-0 flex-1 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/50 px-3 text-sm text-slate-700 dark:text-slate-200 placeholder:text-slate-400 outline-none focus:border-indigo-300 dark:focus:border-indigo-500/40 focus:ring-2 focus:ring-indigo-500/10 transition-all duration-200"
                        />
                        {isDesktop && (
                          <button
                            type="button"
                            onClick={chooseDirectory}
                            className="inline-flex h-9 shrink-0 items-center justify-center rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/50 px-3 text-xs font-medium text-slate-600 dark:text-slate-300 transition-colors duration-200 hover:bg-slate-50 dark:hover:bg-slate-800 cursor-pointer"
                          >
                            选择
                          </button>
                        )}
                      </div>
                    </div>
                  )}
                  <button
                    type="submit"
                    disabled={!name.trim() || creating || (storageMode === "local" && !rootPath.trim())}
                    className="inline-flex h-9 w-full items-center justify-center gap-1.5 rounded-lg bg-indigo-600 px-4 text-sm font-medium text-white shadow-sm transition-all duration-200 hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50 cursor-pointer"
                  >
                    {creating ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
                    {creating ? "创建中..." : "创建工作区"}
                  </button>
                </form>
              </div>

              {/* Import Card */}
              <div className="rounded-xl bg-white/70 dark:bg-slate-900/70 backdrop-blur-sm ring-1 ring-slate-200/60 dark:ring-slate-700/40">
                <div className="px-4 pt-4 pb-2">
                  <div className="flex items-center gap-2 text-sm font-semibold text-slate-900 dark:text-slate-100">
                    <Import size={14} className="text-emerald-600 dark:text-emerald-400" />
                    导入本地目录
                  </div>
                  <p className="mt-1 text-[11px] text-slate-400 dark:text-slate-500">
                    本地目录和配置还在时，可以重新绑定恢复
                  </p>
                </div>
                <div className="space-y-3 px-4 pb-4">
                  <div>
                    <div className="flex gap-2">
                      <input
                        value={importPath}
                        onChange={(event) => setImportPath(event.target.value)}
                        placeholder="输入本地目录绝对路径"
                        className="h-9 min-w-0 flex-1 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/50 px-3 text-sm text-slate-700 dark:text-slate-200 placeholder:text-slate-400 outline-none focus:border-emerald-300 dark:focus:border-emerald-500/40 focus:ring-2 focus:ring-emerald-500/10 transition-all duration-200"
                      />
                      {isDesktop && (
                        <button
                          type="button"
                          onClick={chooseImportDirectory}
                          className="inline-flex h-9 shrink-0 items-center justify-center rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/50 px-3 text-xs font-medium text-slate-600 dark:text-slate-300 transition-colors duration-200 hover:bg-slate-50 dark:hover:bg-slate-800 cursor-pointer"
                        >
                          选择
                        </button>
                      )}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={handleImport}
                    disabled={!importPath.trim() || importing}
                    className="inline-flex h-9 w-full items-center justify-center gap-1.5 rounded-lg bg-emerald-600 px-4 text-sm font-medium text-white shadow-sm transition-all duration-200 hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50 cursor-pointer"
                  >
                    {importing ? <Loader2 size={14} className="animate-spin" /> : <ArrowRight size={14} />}
                    {importing ? "导入中..." : "导入目录"}
                  </button>
                </div>
              </div>

              {/* Hint */}
              <div className="rounded-xl border border-slate-200/60 bg-slate-50/80 dark:border-slate-800/40 dark:bg-slate-900/40 px-4 py-3 text-xs text-slate-500 dark:text-slate-400">
                {storageMode === "local"
                  ? "推荐桌面端使用本地目录模式：后续执行结果可以直接落到你自己的目录里。"
                  : "服务器托管模式适合快速开始，后续也可以再迁到本地目录。"}
              </div>
            </div>

            {/* Right: Workspace List */}
            <div className="min-w-0">
              {loading ? (
                <div className="flex h-64 items-center justify-center text-slate-400">
                  <Loader2 className="animate-spin" />
                </div>
              ) : workspaces.length === 0 ? (
                <div className="flex h-64 flex-col items-center justify-center rounded-xl border border-dashed border-slate-300 bg-white/70 dark:border-slate-700 dark:bg-slate-900/70 backdrop-blur-sm px-6 text-center">
                  <div className="flex size-12 items-center justify-center rounded-xl bg-slate-100 dark:bg-slate-800 text-slate-300 dark:text-slate-600 mb-4">
                    <FolderKanban size={22} strokeWidth={1.5} />
                  </div>
                  <div className="text-sm font-medium text-slate-400 dark:text-slate-500">还没有工作区</div>
                  <div className="mt-1 text-[11px] text-slate-300 dark:text-slate-600">
                    在左侧创建第一个，平台会把它变成持续推进的协作空间
                  </div>
                </div>
              ) : (
                <div className="grid gap-3 sm:grid-cols-2">
                  {workspaces.map((workspace) => {
                    const progress = workspace.stage_total
                      ? Math.round((workspace.stage_approved / workspace.stage_total) * 100)
                      : 0;
                    return (
                      <div
                        key={workspace.id}
                        className="group relative rounded-xl bg-white/70 dark:bg-slate-900/70 backdrop-blur-sm ring-1 ring-slate-200/60 dark:ring-slate-700/40 transition-all duration-200 hover:ring-indigo-300/80 dark:hover:ring-indigo-500/30 hover:shadow-md hover:shadow-indigo-500/5 flex flex-col cursor-pointer"
                        onClick={() => router.push(`/workspaces/${workspace.id}`)}
                      >
                        {/* Card body */}
                        <div className="px-4 pt-3.5 pb-2">
                          <div className="flex items-start gap-3">
                            {/* Icon */}
                            <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-indigo-50 dark:bg-indigo-500/10 text-indigo-600 dark:text-indigo-400">
                              <FolderKanban size={16} />
                            </div>

                            {/* Info */}
                            <div className="min-w-0 flex-1 pr-5">
                              {/* Line 1: Name */}
                              <div className="flex items-center gap-1.5">
                                <h3 className="text-[13px] font-semibold text-slate-800 dark:text-slate-100 truncate">
                                  {workspace.name}
                                </h3>
                                {workspace.binding_state === "missing_directory" && (
                                  <span className="shrink-0 rounded bg-amber-50 dark:bg-amber-500/10 px-1.5 py-0.5 text-[9px] font-semibold text-amber-600 dark:text-amber-400">
                                    目录丢失
                                  </span>
                                )}
                                {workspace.binding_state === "missing_manifest" && (
                                  <span className="shrink-0 rounded bg-amber-50 dark:bg-amber-500/10 px-1.5 py-0.5 text-[9px] font-semibold text-amber-600 dark:text-amber-400">
                                    配置丢失
                                  </span>
                                )}
                              </div>

                              {/* Line 2: Description */}
                              <p className="mt-0.5 text-[11px] text-slate-400 dark:text-slate-500 truncate">
                                {workspace.description || "还没有补充任务目标"}
                              </p>

                              {/* Line 3: Tags */}
                              <div className="mt-1.5 flex items-center gap-1.5 text-[11px] text-slate-500 dark:text-slate-400 flex-wrap">
                                <span className="inline-flex items-center gap-0.5 rounded bg-slate-100 dark:bg-slate-800 px-1.5 py-0.5 text-[9px] font-medium">
                                  {PLATFORM_LABEL[workspace.target_platform] || workspace.target_platform}
                                </span>
                                <span className="inline-flex items-center gap-0.5 rounded bg-slate-100 dark:bg-slate-800 px-1.5 py-0.5 text-[9px] font-medium">
                                  {workspace.storage_mode === "local" ? "本地目录" : "服务器目录"}
                                </span>
                                <span className="inline-flex items-center gap-0.5 rounded bg-indigo-50 dark:bg-indigo-500/10 px-1.5 py-0.5 text-[9px] font-medium text-indigo-600 dark:text-indigo-400">
                                  <CheckCircle2 size={9} />
                                  {workspace.stage_approved}/{workspace.stage_total}
                                </span>
                              </div>
                            </div>
                          </div>
                        </div>

                        {/* Progress bar */}
                        <div className="px-4 py-2">
                          <div className="flex items-center gap-2 text-[11px] text-slate-500 dark:text-slate-400">
                            <span className="shrink-0">进展：{STAGE_LABEL[workspace.current_stage]}</span>
                            <span className="text-slate-300 dark:text-slate-600">·</span>
                            <span className="text-slate-400 dark:text-slate-500">{formatDate(workspace.updated_at)}</span>
                          </div>
                          <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                            <div
                              className="h-full rounded-full bg-indigo-500 transition-all duration-300"
                              style={{ width: `${progress}%` }}
                            />
                          </div>
                        </div>

                        {/* Card bottom actions */}
                        <div className="mt-auto px-4 py-2 border-t border-slate-100 dark:border-slate-800/60 flex items-center justify-between">
                          <button
                            type="button"
                            onClick={(e) => { e.stopPropagation(); handleDelete(workspace); }}
                            disabled={deletingId === workspace.id}
                            className="inline-flex items-center gap-1 rounded-md bg-red-50 dark:bg-red-500/10 hover:bg-red-100 dark:hover:bg-red-500/20 px-2 py-1 text-[10px] font-medium text-red-600 dark:text-red-400 transition-colors duration-200 cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
                          >
                            {deletingId === workspace.id ? <Loader2 size={10} className="animate-spin" /> : <Trash2 size={10} />}
                            删除
                          </button>
                          <ArrowRight
                            size={14}
                            className="text-slate-300 dark:text-slate-600 transition-all duration-200 group-hover:translate-x-0.5 group-hover:text-indigo-500 dark:group-hover:text-indigo-400"
                          />
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
