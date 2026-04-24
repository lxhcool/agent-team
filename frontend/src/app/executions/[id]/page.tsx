"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";

type ExecutionTask = {
  title: string;
  status: string;
  result_summary: string | null;
};

type ExecutionResult = {
  plan_id: string;
  status: string;
  project_path: string | null;
  started_at: string | null;
  finished_at: string | null;
  tasks: ExecutionTask[];
};

const TASK_STATUS_COLORS: Record<string, string> = {
  completed: "var(--success)",
  failed: "var(--danger)",
  skipped: "var(--muted)",
  pending: "var(--warning)",
  in_progress: "var(--accent)",
};

const TASK_STATUS_LABELS: Record<string, string> = {
  completed: "已完成",
  failed: "失败",
  skipped: "已跳过",
  pending: "待执行",
  in_progress: "执行中",
};

const STATUS_COLORS: Record<string, string> = {
  completed: "var(--success)",
  failed: "var(--danger)",
  running: "var(--accent)",
  pending: "var(--warning)",
};

export default function ExecutionDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [result, setResult] = useState<ExecutionResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [theme, setTheme] = useState<"dark" | "light">("dark");

  useEffect(() => {
    const saved = localStorage.getItem("theme");
    if (saved === "light" || saved === "dark") setTheme(saved as "dark" | "light");
  }, []);

  useEffect(() => {
    if (!id) return;
    fetch(`/api/execution-results/${id}`)
      .then((r) => {
        if (r.status === 404) {
          setNotFound(true);
          return null;
        }
        return r.json();
      })
      .then((data) => {
        if (data) setResult(data);
      })
      .catch(() => setNotFound(true))
      .finally(() => setLoading(false));
  }, [id]);

  const toggleTheme = () => {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    localStorage.setItem("theme", next);
    document.documentElement.setAttribute("data-theme", next);
  };

  const fmtDate = (d: string | null) => {
    if (!d) return "-";
    return new Date(d).toLocaleString("zh-CN", {
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    });
  };

  const duration = () => {
    if (!result?.started_at || !result?.finished_at) return "-";
    const ms = new Date(result.finished_at).getTime() - new Date(result.started_at).getTime();
    if (ms < 1000) return `${ms}ms`;
    if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
    return `${(ms / 60000).toFixed(1)}min`;
  };

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <div className="flex gap-1.5">
          <span className="typing-dot" /><span className="typing-dot" /><span className="typing-dot" />
        </div>
      </div>
    );
  }

  if (notFound || !result) {
    return (
      <main className="min-h-screen px-4 py-8 md:px-8">
        <div className="mx-auto max-w-3xl">
          <div className="mb-8 flex items-center justify-between">
            <Link href="/"
              className="flex size-8 cursor-pointer items-center justify-center rounded-lg text-[var(--muted)] transition-colors hover:bg-[var(--surface)] hover:text-[var(--foreground)]"
              title="返回首页">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="15 18 9 12 15 6" /></svg>
            </Link>
            <button onClick={toggleTheme}
              className="flex size-8 cursor-pointer items-center justify-center rounded-lg text-[var(--muted)] transition-colors hover:bg-[var(--surface)] hover:text-[var(--foreground)]">
              {theme === "dark" ? (
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="5" /></svg>
              ) : (
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" /></svg>
              )}
            </button>
          </div>

          <div className="glass rounded-xl py-16 text-center">
            <div className="mb-4 flex justify-center">
              <div className="flex size-16 items-center justify-center rounded-2xl bg-[var(--accent-soft)]">
                <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
                </svg>
              </div>
            </div>
            <p className="mb-1 text-sm font-medium">暂无执行结果</p>
            <p className="text-xs text-[var(--muted)]">请使用 CLI 执行后回传</p>
            <code className="mt-4 inline-block rounded-lg bg-[var(--code-bg)] px-4 py-2 text-xs text-[var(--code-fg)]">
              agent-team execute --plan-id {id} --server http://localhost:{typeof window !== 'undefined' ? (process.env.NEXT_PUBLIC_BACKEND_PORT || '8000') : '8000'}
            </code>
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen px-4 py-8 md:px-8">
      <div className="mx-auto max-w-3xl">
        {/* Header */}
        <div className="mb-8 flex items-center justify-between">
          <Link href="/"
            className="flex size-8 cursor-pointer items-center justify-center rounded-lg text-[var(--muted)] transition-colors hover:bg-[var(--surface)] hover:text-[var(--foreground)]"
            title="返回首页">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="15 18 9 12 15 6" /></svg>
          </Link>
          <button onClick={toggleTheme}
            className="flex size-8 cursor-pointer items-center justify-center rounded-lg text-[var(--muted)] transition-colors hover:bg-[var(--surface)] hover:text-[var(--foreground)]">
            {theme === "dark" ? (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="5" /></svg>
            ) : (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" /></svg>
            )}
          </button>
        </div>

        <h1 className="mb-2 text-3xl font-bold">执行结果</h1>
        <p className="mb-8 text-sm text-[var(--muted)]">
          Plan: <code className="text-xs bg-[var(--code-bg)] px-1.5 py-0.5 rounded">{result.plan_id}</code>
        </p>

        {/* Overview */}
        <div className="glass rounded-xl p-5 mb-6">
          <h2 className="text-sm font-semibold mb-4">执行概览</h2>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <span className="text-xs text-[var(--muted)]">状态</span>
              <div className="mt-1">
                <span className="text-sm font-semibold" style={{ color: STATUS_COLORS[result.status] || "var(--muted)" }}>
                  {result.status}
                </span>
              </div>
            </div>
            <div>
              <span className="text-xs text-[var(--muted)]">耗时</span>
              <div className="mt-1 text-sm font-semibold">{duration()}</div>
            </div>
            <div>
              <span className="text-xs text-[var(--muted)]">项目路径</span>
              <div className="mt-1 text-sm break-all">{result.project_path || "-"}</div>
            </div>
            <div>
              <span className="text-xs text-[var(--muted)]">任务数</span>
              <div className="mt-1 text-sm font-semibold">{result.tasks.length}</div>
            </div>
            <div>
              <span className="text-xs text-[var(--muted)]">开始时间</span>
              <div className="mt-1 text-sm">{fmtDate(result.started_at)}</div>
            </div>
            <div>
              <span className="text-xs text-[var(--muted)]">完成时间</span>
              <div className="mt-1 text-sm">{fmtDate(result.finished_at)}</div>
            </div>
          </div>
        </div>

        {/* Task List */}
        <div>
          <h2 className="mb-3 text-sm font-semibold">任务列表</h2>
          {result.tasks.length === 0 ? (
            <div className="glass rounded-xl py-8 text-center text-xs text-[var(--muted)]">暂无任务</div>
          ) : (
            <div className="space-y-3">
              {result.tasks.map((task, i) => (
                <div key={i} className="glass rounded-xl p-4">
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-sm font-medium">{task.title}</span>
                    <span
                      className="text-xs font-semibold px-2 py-0.5 rounded-md"
                      style={{
                        color: TASK_STATUS_COLORS[task.status] || "var(--muted)",
                        background: `${TASK_STATUS_COLORS[task.status] || "var(--muted)"}15`,
                      }}
                    >
                      {TASK_STATUS_LABELS[task.status] || task.status}
                    </span>
                  </div>
                  {task.result_summary && (
                    <p className="text-xs text-[var(--muted)] leading-relaxed">{task.result_summary}</p>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </main>
  );
}
