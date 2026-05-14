"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  Clock3, Loader2, Sparkles, Trash2, ChevronRight, X,
  PackageCheck, Eclipse, ReceiptText, FileText,
  FolderCode, CheckCircle2, ClipboardList,
} from "lucide-react";

type StageKey =
  | "requirements"
  | "product"
  | "ui_direction"
  | "prototype"
  | "technical"
  | "development"
  | "acceptance"
  | "deployment";

const STAGE_ICON: Record<StageKey, React.ComponentType<{ size?: number; className?: string }>> = {
  requirements: PackageCheck,
  product: Eclipse,
  ui_direction: ReceiptText,
  prototype: FileText,
  technical: FolderCode,
  development: FileText,
  acceptance: CheckCircle2,
  deployment: ClipboardList,
};

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

const STATUS: Record<string, { label: string; color: string; bg: string; icon: React.ReactNode }> = {
  created:       { label: "待处理", color: "var(--muted)", bg: "var(--accent-soft)", icon: <Clock3 size={9} /> },
  planning:      { label: "规划中", color: "var(--accent)", bg: "var(--accent-soft)", icon: <Loader2 size={9} className="animate-spin" /> },
  analyzing:     { label: "分析中", color: "var(--accent)", bg: "var(--accent-soft)", icon: <Loader2 size={9} className="animate-spin" /> },
  researching:   { label: "调研中", color: "var(--accent)", bg: "var(--accent-soft)", icon: <Loader2 size={9} className="animate-spin" /> },
  generating_proposal: { label: "生成方案", color: "var(--accent)", bg: "var(--accent-soft)", icon: <Sparkles size={9} /> },
  reviewing:     { label: "审查中", color: "var(--accent)", bg: "var(--accent-soft)", icon: <Loader2 size={9} className="animate-spin" /> },
  awaiting_approval: { label: "待审批", color: "var(--warning)", bg: "var(--warning-soft)", icon: <Sparkles size={9} /> },
  generating_plan: { label: "生成计划", color: "var(--accent)", bg: "var(--accent-soft)", icon: <Sparkles size={9} /> },
  ready_for_export:{ label: "可导出", color: "var(--success)", bg: "var(--success-soft)", icon: <Sparkles size={9} /> },
  completed:     { label: "已完成", color: "var(--success)", bg: "var(--success-soft)", icon: <Sparkles size={9} /> },
  cancelled:     { label: "已取消", color: "var(--muted)", bg: "var(--accent-soft)", icon: <X size={9} /> },
  failed:        { label: "失败", color: "var(--danger)", bg: "var(--danger-soft)", icon: <X size={9} /> },
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
    <div className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-indigo-50/30 dark:from-slate-950 dark:via-slate-950 dark:to-indigo-950/20">
      <TopNav />
      <main className="min-w-0 pt-14">
        <div className="mx-auto max-w-5xl px-6 py-8">
          {/* Header */}
          <div className="mb-6 flex items-center justify-between">
            <div className="flex items-center gap-4">
              <Link
                href="/"
                className="flex size-8 items-center justify-center rounded-lg bg-white dark:bg-slate-800/50 text-slate-500 dark:text-slate-400 ring-1 ring-slate-200/60 dark:ring-slate-700/40 hover:ring-indigo-300 dark:hover:ring-indigo-500/40 hover:text-indigo-600 dark:hover:text-indigo-400 transition-all cursor-pointer"
              >
                <ChevronRight size={16} className="rotate-180" />
              </Link>
              <div>
                <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight text-slate-900 dark:text-slate-100">
                  <div className="flex size-8 items-center justify-center rounded-lg bg-indigo-100 dark:bg-indigo-500/15">
                    <Sparkles size={16} className="text-indigo-600 dark:text-indigo-400" />
                  </div>
                  项目流程
                </h1>
              </div>
            </div>
            <span className="inline-flex items-center gap-1.5 rounded-full bg-indigo-50 dark:bg-indigo-500/10 px-3 py-1.5 text-xs font-semibold text-indigo-600 dark:text-indigo-400">
              <Sparkles size={11} />
              {flows.length} 个流程
            </span>
          </div>

          {loading ? (
            <div className="flex items-center justify-center py-20">
              <div className="flex gap-1.5"><span className="typing-dot" /><span className="typing-dot" /><span className="typing-dot" /></div>
            </div>
          ) : sortedFlows.length === 0 ? (
            <div className="flex items-center gap-3 rounded-xl bg-white/70 dark:bg-slate-900/70 backdrop-blur-sm ring-1 ring-slate-200/60 dark:ring-slate-700/40 px-4 py-3.5">
              <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-slate-100 dark:bg-slate-800 text-slate-300 dark:text-slate-600">
                <Sparkles size={15} strokeWidth={1.5} />
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-[13px] font-medium text-slate-400 dark:text-slate-500">还没有流程</div>
                <div className="text-[11px] text-slate-300 dark:text-slate-600 mt-1">去首页输入需求后，这里会出现可管理的列表</div>
              </div>
            </div>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {sortedFlows.map((flow) => {
                const progress = flow.stage_total
                  ? Math.round((flow.stage_approved / flow.stage_total) * 100)
                  : 0;
                const isDeleting = deletingId === flow.id;
                const st = STATUS[flow.current_stage] || { label: flow.current_stage, color: "#64748b", bg: "rgba(100,116,139,0.06)" };

                return (
                  <div
                    key={flow.id}
                    className="group relative rounded-xl bg-white/70 dark:bg-slate-900/70 backdrop-blur-sm ring-1 ring-slate-200/60 dark:ring-slate-700/40 transition-all duration-200 hover:ring-indigo-300/80 dark:hover:ring-indigo-500/30 hover:shadow-md hover:shadow-indigo-500/5 flex flex-col"
                  >
                    {/* Card body */}
                    <Link href={`/flows/${flow.id}`} className="px-4 pt-3.5 pb-2">
                      <div className="flex items-start gap-3">
                        {/* Icon */}
                        <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-indigo-50 dark:bg-indigo-500/10 text-indigo-600 dark:text-indigo-400">
                          {(() => {
                            const StageIcon = STAGE_ICON[flow.current_stage as StageKey] || Sparkles;
                            return <StageIcon size={16} />;
                          })()}
                        </div>
                        {/* Info */}
                        <div className="min-w-0 flex-1 pr-5">
                          {/* Line 1: Name */}
                          <h3 className="text-[13px] font-semibold text-slate-800 dark:text-slate-100 truncate">{flow.name}</h3>
                          {/* Line 2: Status · Platform · Time */}
                          <div className="mt-1 flex items-center gap-1.5 text-[11px] text-slate-500 dark:text-slate-400">
                            <span className="inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[9px] font-semibold" style={{ background: st.bg, color: st.color as string }}>{st.icon}{st.label}</span>
                            <span className="text-slate-300 dark:text-slate-600">·</span>
                            <span>{formatRelative(flow.updated_at)}</span>
                          </div>
                          {/* Line 3: Description */}
                          <p className="mt-1 text-[11px] text-slate-400 dark:text-slate-500 truncate">{flow.description || "等待补充项目背景"}</p>
                        </div>
                      </div>
                    </Link>

                    {/* Card bottom: progress + actions */}
                    <div className="mt-auto px-4 py-2 border-t border-slate-100 dark:border-slate-800/60 flex items-center gap-2">
                      <div className="flex items-center gap-2 flex-1 min-w-0">
                        <div className="flex-1 h-1.5 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                          <div className="h-full rounded-full bg-indigo-500 transition-all" style={{ width: `${progress}%` }} />
                        </div>
                        <span className="text-[10px] font-medium text-indigo-600 dark:text-indigo-400 shrink-0">{progress}%</span>
                      </div>
                      <button
                        type="button"
                        onClick={(e) => { e.preventDefault(); e.stopPropagation(); handleDelete(flow); }}
                        disabled={isDeleting}
                        className={`inline-flex items-center gap-1 rounded-md px-2 py-1 text-[10px] font-medium transition-colors duration-200 cursor-pointer shrink-0 ${
                          isDeleting
                            ? "bg-slate-50 dark:bg-slate-800 text-slate-300 dark:text-slate-600 cursor-not-allowed"
                            : "bg-red-50 dark:bg-red-500/10 hover:bg-red-100 dark:hover:bg-red-500/20 text-red-600 dark:text-red-400"
                        }`}
                      >
                        {isDeleting ? <Loader2 size={10} className="animate-spin" /> : <Trash2 size={10} />}删除
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </main>
      {ConfirmDialog}
    </div>
  );
}
