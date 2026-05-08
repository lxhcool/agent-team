"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Clock3, Loader2, Sparkles, Trash2, ChevronRight } from "lucide-react";

import { TopNav } from "../components/topnav";
import { useConfirm } from "@/components/ui/confirm-dialog";

type FlowSummary = {
  id: string;
  name: string;
  description: string | null;
  target_platform: string;
  current_stage: string;
  stage_total: number;
  stage_approved: number;
  created_at: string;
  updated_at: string;
};

function formatRelative(value: string) {
  const diff = Date.now() - new Date(value).getTime();
  const mins = Math.floor(diff / 60000);
  const hours = Math.floor(diff / 3600000);
  const days = Math.floor(diff / 86400000);
  if (mins < 1) return "刚刚";
  if (mins < 60) return `${mins} 分钟前`;
  if (hours < 24) return `${hours} 小时前`;
  return `${days} 天前`;
}

export default function FlowsPage() {
  const { confirm, ConfirmDialog } = useConfirm();
  const [flows, setFlows] = useState<FlowSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/flows")
      .then((res) => res.json())
      .then((data) => setFlows(Array.isArray(data) ? data : []))
      .catch(() => setFlows([]))
      .finally(() => setLoading(false));
  }, []);

  const sortedFlows = useMemo(
    () => [...flows].sort((a, b) => +new Date(b.updated_at) - +new Date(a.updated_at)),
    [flows],
  );

  const handleDelete = async (flow: FlowSummary) => {
    const approved = await confirm({
      title: "删除流程",
      description: `确定删除「${flow.name}」吗？这个流程的阶段内容会一起删除。`,
      variant: "destructive",
    });
    if (!approved) return;

    setDeletingId(flow.id);
    try {
      const res = await fetch(`/api/flows/${flow.id}`, { method: "DELETE" });
      if (res.ok) {
        setFlows((current) => current.filter((item) => item.id !== flow.id));
      }
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className="min-h-screen bg-[radial-gradient(circle_at_top,_rgba(79,70,229,0.08),_transparent_30%),linear-gradient(180deg,_#f8fbff_0%,_#eef3f8_100%)] dark:bg-slate-950">
      <TopNav />
      <main className="mx-auto max-w-6xl px-6 pb-16 pt-24">
        <section className="mb-6 rounded-[30px] border border-white/70 bg-white/92 p-6 shadow-[0_24px_80px_rgba(15,23,42,0.08)] backdrop-blur dark:border-slate-800 dark:bg-slate-900/92">
          <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
            <div>
              <div className="text-[11px] font-semibold uppercase tracking-[0.2em] text-indigo-400">流程管理</div>
              <h1 className="mt-2 text-3xl font-semibold tracking-tight text-slate-950 dark:text-slate-50">
                所有项目流程
              </h1>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500 dark:text-slate-400">
                首页只展示最近几条，这里用来集中查看、继续进入和删除已有流程。
              </p>
            </div>
            <Link
              href="/"
              className="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 transition hover:border-indigo-200 hover:text-indigo-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
            >
              返回首页
              <ChevronRight size={16} />
            </Link>
          </div>
        </section>

        {loading ? (
          <div className="flex items-center justify-center py-20 text-slate-400">
            <Loader2 className="animate-spin" />
          </div>
        ) : sortedFlows.length === 0 ? (
          <div className="rounded-[28px] border border-white/70 bg-white/92 px-6 py-10 text-sm text-slate-400 shadow-[0_24px_80px_rgba(15,23,42,0.08)] backdrop-blur dark:border-slate-800 dark:bg-slate-900/92 dark:text-slate-500">
            还没有流程。去首页输入一句需求后，这里会出现可管理的列表。
          </div>
        ) : (
          <div className="space-y-3">
            {sortedFlows.map((flow) => {
              const progress = flow.stage_total
                ? Math.round((flow.stage_approved / flow.stage_total) * 100)
                : 0;
              const isDeleting = deletingId === flow.id;

              return (
                <div
                  key={flow.id}
                  className="group rounded-[26px] border border-white/70 bg-white/92 px-5 py-5 shadow-[0_18px_60px_rgba(15,23,42,0.07)] backdrop-blur transition hover:shadow-[0_22px_70px_rgba(79,70,229,0.12)] dark:border-slate-800 dark:bg-slate-900/92"
                >
                  <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <div className="truncate text-lg font-semibold text-slate-900 dark:text-slate-100">
                          {flow.name}
                        </div>
                        <span className="rounded-full bg-indigo-50 px-2.5 py-1 text-[11px] font-medium text-indigo-700 dark:bg-indigo-500/15 dark:text-indigo-300">
                          {flow.target_platform}
                        </span>
                        <span className="rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                          {flow.current_stage}
                        </span>
                      </div>

                      <div className="mt-2 line-clamp-2 text-sm leading-6 text-slate-500 dark:text-slate-400">
                        {flow.description || "等待补充项目背景"}
                      </div>

                      <div className="mt-4 flex flex-wrap items-center gap-4 text-xs text-slate-400 dark:text-slate-500">
                        <span className="inline-flex items-center gap-1.5">
                          <Clock3 size={13} />
                          最近更新 {formatRelative(flow.updated_at)}
                        </span>
                        <span>{progress}% 已确认</span>
                        <span>{flow.stage_approved}/{flow.stage_total} 阶段完成</span>
                      </div>

                      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                        <div className="h-full rounded-full bg-indigo-500 transition-all" style={{ width: `${progress}%` }} />
                      </div>
                    </div>

                    <div className="flex shrink-0 flex-wrap gap-2">
                      <Link
                        href={`/flows/${flow.id}`}
                        className="inline-flex items-center gap-2 rounded-full bg-indigo-600 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-indigo-500"
                      >
                        <Sparkles size={15} />
                        继续查看
                      </Link>
                      <button
                        type="button"
                        onClick={() => handleDelete(flow)}
                        disabled={isDeleting}
                        className="inline-flex items-center gap-2 rounded-full border border-red-200 bg-white px-4 py-2.5 text-sm font-medium text-red-600 transition hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-red-500/20 dark:bg-slate-900 dark:text-red-300 dark:hover:bg-red-500/10"
                      >
                        {isDeleting ? <Loader2 size={15} className="animate-spin" /> : <Trash2 size={15} />}
                        删除
                      </button>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </main>
      {ConfirmDialog}
    </div>
  );
}
