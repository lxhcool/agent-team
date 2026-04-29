"use client";

import { FormEvent, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ArrowRight,
  CheckCircle2,
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
  const [error, setError] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [targetPlatform, setTargetPlatform] = useState("website");

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
          <form onSubmit={handleCreate} className="grid gap-4 lg:grid-cols-[1fr_1.2fr_180px_auto] lg:items-end">
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
            <button
              type="submit"
              disabled={!name.trim() || creating}
              className="inline-flex h-[42px] items-center justify-center gap-2 rounded-xl bg-indigo-600 px-4 text-sm font-medium text-white shadow-sm transition hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {creating ? <Loader2 size={16} className="animate-spin" /> : <Plus size={16} />}
              创建
            </button>
          </form>
          {error && (
            <div className="mt-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-600 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-300">
              {error}
            </div>
          )}
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
                <Link
                  key={workspace.id}
                  href={`/workspaces/${workspace.id}`}
                  className="group rounded-2xl border border-slate-200 bg-white p-5 shadow-sm transition hover:border-indigo-200 hover:shadow-md dark:border-slate-800 dark:bg-slate-900 dark:hover:border-indigo-500/30"
                >
                  <div className="mb-4 flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <h2 className="truncate text-base font-semibold text-slate-900 dark:text-slate-100">
                        {workspace.name}
                      </h2>
                      <p className="mt-1 line-clamp-2 text-sm text-slate-500 dark:text-slate-400">
                        {workspace.description || "还没有补充需求描述"}
                      </p>
                    </div>
                    <ArrowRight
                      size={18}
                      className="mt-1 shrink-0 text-slate-300 transition group-hover:translate-x-0.5 group-hover:text-indigo-500"
                    />
                  </div>

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
              );
            })}
          </div>
        )}
      </main>
    </div>
  );
}
