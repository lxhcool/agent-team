"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  Clock, Loader2, Sparkles, X, ArrowRight,
  Trash2, Search, CheckCircle2, AlertCircle, Diamond
} from "lucide-react";
import { TopNav } from "../components/topnav";
import { useAuth } from "@/lib/auth";

type PlanningSession = {
  id: string; title: string; status: string; mode: string;
  input_text: string; summary: string | null;
  created_at: string; updated_at: string;
};

const STATUS: Record<string, { label: string; color: string; bg: string }> = {
  created:       { label: "待处理", color: "#9ca3af", bg: "rgba(99,102,241,0.06)" },
  planning:      { label: "规划中", color: "#6366f1", bg: "rgba(99,102,241,0.06)" },
  analyzing:     { label: "分析中", color: "#6366f1", bg: "rgba(99,102,241,0.06)" },
  researching:   { label: "调研中", color: "#6366f1", bg: "rgba(99,102,241,0.06)" },
  generating_proposal: { label: "生成方案", color: "#6366f1", bg: "rgba(99,102,241,0.06)" },
  reviewing:     { label: "审查中", color: "#6366f1", bg: "rgba(99,102,241,0.06)" },
  awaiting_approval: { label: "待审批", color: "#f59e0b", bg: "rgba(245,158,11,0.06)" },
  generating_plan: { label: "生成计划", color: "#6366f1", bg: "rgba(99,102,241,0.06)" },
  ready_for_export:{ label: "可导出", color: "#10b981", bg: "rgba(16,185,129,0.06)" },
  completed:     { label: "已完成", color: "#10b981", bg: "rgba(16,185,129,0.06)" },
  cancelled:     { label: "已取消", color: "#9ca3af", bg: "rgba(99,102,241,0.06)" },
  failed:        { label: "失败", color: "#ef4444", bg: "rgba(239,68,68,0.06)" },
};

export default function SessionsPage() {
  const { loading: authLoading } = useAuth();
  const [sessions, setSessions] = useState<PlanningSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [deleting, setDeleting] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/planning-sessions")
      .then((r) => r.json())
      .then((data) => { setSessions(data); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  const handleDelete = async (id: string) => {
    if (!confirm("确定删除此会话？")) return;
    setDeleting(id);
    try { await fetch(`/api/planning-sessions/${id}`, { method: "DELETE" }); setSessions((p) => p.filter((s) => s.id !== id)); } catch {} finally { setDeleting(null); }
  };

  const fmtDate = (d: string) => new Date(d).toLocaleString("zh-CN", { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

  const filtered = sessions.filter((s) => {
    if (!searchQuery.trim()) return true;
    const q = searchQuery.toLowerCase();
    return (s.title && s.title.toLowerCase().includes(q)) || (s.summary && s.summary.toLowerCase().includes(q));
  });

  if (authLoading) return <div className="min-h-screen flex items-center justify-center"><Loader2 className="animate-spin text-slate-400" size={24} /></div>;

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-indigo-50/30 dark:from-slate-950 dark:via-slate-950 dark:to-indigo-950/20">
      <TopNav />
      <main className="min-w-0 pt-14">
        <div className="mx-auto max-w-5xl px-6 py-8">
          {/* Header */}
          <div className="mb-6 flex items-center justify-between">
            <div>
              <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight text-slate-900 dark:text-slate-100">
                <div className="flex size-8 items-center justify-center rounded-lg bg-indigo-100 dark:bg-indigo-500/15">
                  <Sparkles size={16} className="text-indigo-600 dark:text-indigo-400" />
                </div>
                任务规划
              </h1>
              <p className="mt-1.5 text-sm text-slate-500 dark:text-slate-400">所有历史规划会话</p>
            </div>
            <span className="inline-flex items-center gap-1.5 rounded-full bg-indigo-50 dark:bg-indigo-500/10 px-3 py-1.5 text-xs font-semibold text-indigo-600 dark:text-indigo-400">
              <Sparkles size={11} />
              {sessions.length} 个会话
            </span>
          </div>

          {/* Search */}
          {sessions.length > 0 && (
            <div className="mb-5 relative">
              <Search size={14} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400 dark:text-slate-500" />
              <input type="text" value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)} placeholder="搜索会话..."
                className="h-10 w-full rounded-xl bg-white/70 dark:bg-slate-900/70 backdrop-blur-sm pl-10 pr-4 text-sm text-slate-700 dark:text-slate-200 placeholder:text-slate-400 dark:placeholder:text-slate-500 outline-none ring-1 ring-slate-200/60 dark:ring-slate-700/40 focus:ring-indigo-300 dark:focus:ring-indigo-500/40 transition-all duration-200" />
            </div>
          )}

          {loading ? (
            <div className="flex items-center justify-center py-20"><div className="flex gap-1.5"><span className="typing-dot" /><span className="typing-dot" /><span className="typing-dot" /></div></div>
          ) : filtered.length === 0 ? (
            <div className="flex items-center gap-3 rounded-xl bg-white/70 dark:bg-slate-900/70 backdrop-blur-sm ring-1 ring-slate-200/60 dark:ring-slate-700/40 px-4 py-3.5">
              <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-slate-100 dark:bg-slate-800 text-slate-300 dark:text-slate-600">
                <Sparkles size={15} strokeWidth={1.5} />
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-[13px] font-medium text-slate-400 dark:text-slate-500">{searchQuery ? "没有匹配的会话" : "还没有会话"}</div>
                <div className="text-[11px] text-slate-300 dark:text-slate-600 mt-1">{searchQuery ? "尝试更换搜索关键词" : "返回工作台创建第一个规划"}</div>
              </div>
            </div>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {filtered.map((s) => {
                const st = STATUS[s.status] || { label: s.status, color: "#9ca3af", bg: "rgba(99,102,241,0.06)" };
                const isActive = ["planning", "analyzing", "researching", "generating_proposal", "reviewing", "generating_plan"].includes(s.status);
                return (
                  <div key={s.id} className="group relative rounded-xl bg-white/70 dark:bg-slate-900/70 backdrop-blur-sm ring-1 ring-slate-200/60 dark:ring-slate-700/40 p-4 transition-all duration-200 hover:ring-indigo-300/80 dark:hover:ring-indigo-500/30 hover:bg-indigo-50/30 dark:hover:bg-indigo-950/20 hover:shadow-sm hover:shadow-indigo-500/5">
                    <button onClick={(e) => { e.preventDefault(); e.stopPropagation(); handleDelete(s.id); }} disabled={deleting === s.id}
                      className="absolute right-2.5 top-2.5 flex size-7 items-center justify-center rounded-lg text-slate-300 dark:text-slate-600 opacity-0 transition-all duration-200 hover:bg-red-50 dark:hover:bg-red-500/10 hover:text-red-500 group-hover:opacity-100 cursor-pointer disabled:opacity-50">
                      {deleting === s.id ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
                    </button>
                    <Link href={`/sessions/${s.id}`} className="cursor-pointer block">
                      <div className="mb-2 flex items-start gap-2.5">
                        <div className="flex size-9 shrink-0 items-center justify-center rounded-lg transition-colors duration-200" style={{ background: st.bg, color: st.color }}>
                          {s.status === "completed" ? <CheckCircle2 size={15} /> : s.status === "failed" ? <AlertCircle size={15} /> : isActive ? <Loader2 size={15} className="animate-spin" /> : <Diamond size={15} />}
                        </div>
                        <div className="min-w-0 flex-1 pr-5">
                          <div className="text-[13px] font-medium text-slate-800 dark:text-slate-100 truncate group-hover:text-indigo-700 dark:group-hover:text-indigo-300 transition-colors duration-200">{s.title}</div>
                          <p className="mt-1 line-clamp-2 text-xs text-slate-500 dark:text-slate-400 leading-relaxed">{s.summary || s.input_text}</p>
                        </div>
                      </div>
                      <div className="flex items-center justify-between pt-2 border-t border-slate-100/80 dark:border-slate-800/60">
                        <span className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold" style={{ background: st.bg, color: st.color }}>{st.label}</span>
                        <span className="text-[11px] text-slate-400 dark:text-slate-500">{fmtDate(s.created_at)}</span>
                      </div>
                    </Link>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
