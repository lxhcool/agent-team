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
} from "lucide-react";

import { TopNav } from "../components/topnav";

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
  requirements: "需求确认",
  product: "产品方案",
  ui_direction: "UI 方向",
  prototype: "原型确认",
  technical: "技术方案",
  development: "开发执行",
  acceptance: "预览验收",
  deployment: "部署测试",
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
    <div className="min-h-screen bg-slate-50 dark:bg-slate-950">
      <TopNav />
      <main className="mx-auto max-w-6xl px-6 pb-16 pt-24">
        <div className="mb-8 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <div className="mb-2 inline-flex items-center gap-2 rounded-full bg-indigo-50 px-3 py-1 text-xs font-medium text-indigo-600 dark:bg-indigo-500/15 dark:text-indigo-300">
              <FolderKanban size={14} />
              AI 开发团队工作区
            </div>
            <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-slate-100">
              工作区
            </h1>
            <p className="mt-2 max-w-2xl text-sm text-slate-500 dark:text-slate-400">
              每个工作区对应一个产品项目，按需求、产品、UI、原型、技术、开发、验收和部署逐步确认。
            </p>
          </div>
        </div>

        <section className="mb-8 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <form onSubmit={handleCreate} className="grid gap-4 lg:grid-cols-2">
            <div>
              <label className="mb-1.5 block text-xs font-medium text-slate-500 dark:text-slate-400">
                项目名称
              </label>
              <input
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="例如：本地生活小程序"
                className="w-full rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5 text-sm text-slate-900 outline-none transition focus:border-indigo-400 focus:bg-white focus:ring-2 focus:ring-indigo-100 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100 dark:focus:ring-indigo-500/20"
              />
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-medium text-slate-500 dark:text-slate-400">
                初始需求
              </label>
              <input
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                placeholder="一句话描述你想做什么，后续 AI 团队会继续追问和推荐"
                className="w-full rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5 text-sm text-slate-900 outline-none transition focus:border-indigo-400 focus:bg-white focus:ring-2 focus:ring-indigo-100 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100 dark:focus:ring-indigo-500/20"
              />
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-medium text-slate-500 dark:text-slate-400">
                目标类型
              </label>
              <select
                value={targetPlatform}
                onChange={(event) => setTargetPlatform(event.target.value)}
                className="w-full rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5 text-sm text-slate-900 outline-none transition focus:border-indigo-400 focus:bg-white focus:ring-2 focus:ring-indigo-100 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100 dark:focus:ring-indigo-500/20"
              >
                <option value="website">网站</option>
                <option value="miniapp">小程序</option>
                <option value="dashboard">管理后台</option>
                <option value="app">应用</option>
              </select>
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-medium text-slate-500 dark:text-slate-400">
                存储方式
              </label>
              <select
                value={storageMode}
                onChange={(event) => setStorageMode(event.target.value as "server" | "local")}
                className="w-full rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5 text-sm text-slate-900 outline-none transition focus:border-indigo-400 focus:bg-white focus:ring-2 focus:ring-indigo-100 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100 dark:focus:ring-indigo-500/20"
              >
                <option value="server">服务器托管目录</option>
                <option value="local">本地目录</option>
              </select>
            </div>
            <div className="lg:col-span-2">
              <label className="mb-1.5 block text-xs font-medium text-slate-500 dark:text-slate-400">
                项目目录
              </label>
              <div className="flex flex-col gap-3 sm:flex-row">
                <input
                  value={rootPath}
                  onChange={(event) => setRootPath(event.target.value)}
                  placeholder={storageMode === "local" ? "输入本地绝对路径，例如 /Users/you/projects/my-app" : "服务器托管模式下可留空"}
                  className="w-full rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5 text-sm text-slate-900 outline-none transition focus:border-indigo-400 focus:bg-white focus:ring-2 focus:ring-indigo-100 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100 dark:focus:ring-indigo-500/20"
                />
                {isDesktop && (
                  <button
                    type="button"
                    onClick={chooseDirectory}
                    className="inline-flex h-[42px] shrink-0 items-center justify-center gap-2 rounded-xl border border-slate-200 bg-white px-4 text-sm font-medium text-slate-700 shadow-sm transition hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300 dark:hover:bg-slate-800"
                  >
                    选择目录
                  </button>
                )}
                <button
                  type="submit"
                  disabled={!name.trim() || creating || (storageMode === "local" && !rootPath.trim())}
                  className="inline-flex h-[42px] shrink-0 items-center justify-center gap-2 rounded-xl bg-indigo-600 px-4 text-sm font-medium text-white shadow-sm transition hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {creating ? <Loader2 size={16} className="animate-spin" /> : <Plus size={16} />}
                  创建
                </button>
              </div>
              <p className="mt-2 text-xs text-slate-400">
                {storageMode === "local"
                  ? "推荐桌面端使用本地目录模式：后续开发文件直接写到你自己的电脑目录里。"
                  : "服务器托管模式适合快速体验，但长期会占用服务端磁盘。"}
              </p>
            </div>
          </form>
          {error && (
            <div className="mt-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-600 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-300">
              {error}
            </div>
          )}
        </section>

        <section className="mb-8 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div className="mb-3">
            <div className="text-sm font-semibold text-slate-900 dark:text-slate-100">导入本地目录</div>
            <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">
              当工作区记录被删掉，但本地目录和 `.agent-workspace.json` 还在时，可以从这里重新绑定或恢复。
            </div>
          </div>
          <div className="flex flex-col gap-3 sm:flex-row">
            <input
              value={importPath}
              onChange={(event) => setImportPath(event.target.value)}
              placeholder="输入要导入的本地目录绝对路径"
              className="w-full rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5 text-sm text-slate-900 outline-none transition focus:border-indigo-400 focus:bg-white focus:ring-2 focus:ring-indigo-100 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100 dark:focus:ring-indigo-500/20"
            />
            {isDesktop && (
              <button
                type="button"
                onClick={chooseImportDirectory}
                className="inline-flex h-[42px] shrink-0 items-center justify-center gap-2 rounded-xl border border-slate-200 bg-white px-4 text-sm font-medium text-slate-700 shadow-sm transition hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300 dark:hover:bg-slate-800"
              >
                选择目录
              </button>
            )}
            <button
              type="button"
              onClick={handleImport}
              disabled={!importPath.trim() || importing}
              className="inline-flex h-[42px] shrink-0 items-center justify-center gap-2 rounded-xl bg-emerald-600 px-4 text-sm font-medium text-white shadow-sm transition hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {importing ? <Loader2 size={16} className="animate-spin" /> : <ArrowRight size={16} />}
              导入目录
            </button>
          </div>
        </section>

        {loading ? (
          <div className="flex h-48 items-center justify-center text-slate-400">
            <Loader2 className="animate-spin" />
          </div>
        ) : workspaces.length === 0 ? (
          <section className="rounded-2xl border border-dashed border-slate-300 bg-white px-6 py-14 text-center dark:border-slate-700 dark:bg-slate-900">
            <Sparkles className="mx-auto mb-4 text-indigo-500" size={28} />
            <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">
              还没有工作区
            </h2>
            <p className="mx-auto mt-2 max-w-md text-sm text-slate-500 dark:text-slate-400">
              先创建一个项目，系统会从需求确认开始，像真实开发团队一样一步步推进。
            </p>
          </section>
        ) : (
          <div className="grid gap-4 md:grid-cols-2">
            {workspaces.map((workspace) => {
              const progress = workspace.stage_total
                ? Math.round((workspace.stage_approved / workspace.stage_total) * 100)
                : 0;
              return (
                <div
                  key={workspace.id}
                  className="group rounded-2xl border border-slate-200 bg-white p-5 shadow-sm transition hover:border-indigo-200 hover:shadow-md dark:border-slate-800 dark:bg-slate-900 dark:hover:border-indigo-500/30"
                >
                  <div className="mb-4 flex items-start justify-between gap-3">
                    <Link href={`/workspaces/${workspace.id}`} className="min-w-0 flex-1">
                      <h2 className="truncate text-base font-semibold text-slate-900 dark:text-slate-100">
                        {workspace.name}
                      </h2>
                      <p className="mt-1 line-clamp-2 text-sm text-slate-500 dark:text-slate-400">
                        {workspace.description || "还没有补充需求描述"}
                      </p>
                      <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-slate-400">
                        <span className="rounded-full bg-slate-100 px-2 py-1 dark:bg-slate-800">
                          {workspace.storage_mode === "local" ? "本地目录" : "服务器目录"}
                        </span>
                        {workspace.binding_id && (
                          <span className="rounded-full bg-slate-100 px-2 py-1 dark:bg-slate-800">
                            {workspace.binding_id}
                          </span>
                        )}
                        {workspace.root_path && (
                          <span className="max-w-full truncate rounded-full bg-slate-100 px-2 py-1 dark:bg-slate-800">
                            {workspace.root_path}
                          </span>
                        )}
                        {workspace.binding_state === "missing_directory" && (
                          <span className="rounded-full bg-amber-50 px-2 py-1 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300">
                            本地目录丢失
                          </span>
                        )}
                        {workspace.binding_state === "missing_manifest" && (
                          <span className="rounded-full bg-amber-50 px-2 py-1 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300">
                            Manifest 丢失
                          </span>
                        )}
                      </div>
                    </Link>
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => handleDelete(workspace)}
                        disabled={deletingId === workspace.id}
                        className="inline-flex h-9 w-9 items-center justify-center rounded-xl border border-slate-200 text-slate-400 transition hover:border-red-200 hover:bg-red-50 hover:text-red-600 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-700 dark:text-slate-500 dark:hover:border-red-500/20 dark:hover:bg-red-500/10 dark:hover:text-red-300"
                        aria-label={`删除工作区 ${workspace.name}`}
                      >
                        {deletingId === workspace.id ? <Loader2 size={16} className="animate-spin" /> : <Trash2 size={16} />}
                      </button>
                      <Link href={`/workspaces/${workspace.id}`}>
                        <ArrowRight
                          size={18}
                          className="mt-1 shrink-0 text-slate-300 transition group-hover:translate-x-0.5 group-hover:text-indigo-500"
                        />
                      </Link>
                    </div>
                  </div>

                  <Link href={`/workspaces/${workspace.id}`} className="block">
                    <div className="mb-4 flex flex-wrap items-center gap-2 text-xs">
                    <span className="rounded-full bg-slate-100 px-2.5 py-1 font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                      {workspace.target_platform}
                    </span>
                    <span className="rounded-full bg-indigo-50 px-2.5 py-1 font-medium text-indigo-600 dark:bg-indigo-500/15 dark:text-indigo-300">
                      当前：{STAGE_LABEL[workspace.current_stage]}
                    </span>
                    <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2.5 py-1 font-medium text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-300">
                      <CheckCircle2 size={12} />
                      {workspace.stage_approved}/{workspace.stage_total}
                    </span>
                    </div>

                    <div className="h-2 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                      <div
                        className="h-full rounded-full bg-indigo-500 transition-all"
                        style={{ width: `${progress}%` }}
                      />
                    </div>
                    <div className="mt-3 text-xs text-slate-400">
                      更新于 {formatDate(workspace.updated_at)}
                    </div>
                  </Link>
                </div>
              );
            })}
          </div>
        )}
      </main>
    </div>
  );
}
