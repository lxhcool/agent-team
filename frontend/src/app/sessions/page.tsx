"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  Clock, Loader2, Sparkles, X, ArrowRight,
  MessageCircle, Trash2, Search
} from "lucide-react";
import { TopNav } from "../components/topnav";
import { useAuth } from "@/lib/auth";

type PlanningSession = {
  id: string; title: string; status: string; mode: string;
  input_text: string; summary: string | null;
  created_at: string; updated_at: string;
};

const STATUS: Record<string, { label: string; color: string; bg: string; icon: React.ReactNode }> = {
  created:       { label: "待处理", color: "var(--muted)", bg: "var(--accent-soft)", icon: <Clock size={9} /> },
  planning:      { label: "规划中", color: "var(--accent)", bg: "var(--accent-soft)", icon: <Loader2 size={9} className="animate-spin" /> },
  analyzing:     { label: "分析中", color: "var(--accent)", bg: "var(--accent-soft)", icon: <Loader2 size={9} className="animate-spin" /> },
  researching:   { label: "调研中", color: "var(--accent)", bg: "var(--accent-soft)", icon: <Loader2 size={9} className="animate-spin" /> },
  generating_proposal: { label: "生成方案", color: "var(--accent)", bg: "var(--accent-soft)", icon: <Sparkles size={9} /> },
  reviewing:     { label: "审查中", color: "var(--accent)", bg: "var(--accent-soft)", icon: <Loader2 size={9} className="animate-spin" /> },
  awaiting_approval: { label: "待审批", color: "var(--warning)", bg: "var(--warning-soft)", icon: <Sparkles size={9} /> },
  generating_plan: { label: "生成计划", color: "var(--accent)", bg: "var(--accent-soft)", icon: <Sparkles size={9} /> },
  ready_for_export:{ label: "可导出", color: "var(--success)", bg: "var(--success-soft)", icon: <ArrowRight size={9} /> },
  completed:     { label: "已完成", color: "var(--success)", bg: "var(--success-soft)", icon: <Sparkles size={9} /> },
  cancelled:     { label: "已取消", color: "var(--muted)", bg: "var(--accent-soft)", icon: <X size={9} /> },
  failed:        { label: "失败", color: "var(--danger)", bg: "var(--danger-soft)", icon: <X size={9} /> },
};

export default function SessionsPage() {
  const { loading: authLoading } = useAuth();
  if (authLoading) return <div className="min-h-screen flex items-center justify-center"><Loader2 className="animate-spin text-[var(--muted)]" size={24} /></div>;
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

  return (
    <div className="min-h-screen">
      <TopNav />
      <main className="min-w-0 pt-14">
        <div className="mx-auto max-w-5xl px-6 py-8">
          {/* Header */}
          <div className="mb-6 flex items-center justify-between">
            <div>
              <h1 className="text-2xl font-semibold tracking-tight">Planning 会话</h1>
              <p className="mt-1 text-sm text-[var(--muted)]">所有历史 Planning Session</p>
            </div>
            <span className="rounded-md bg-[var(--accent-soft)] px-2 py-1 text-xs font-medium text-[var(--accent)]">{sessions.length} 个会话</span>
          </div>

          {/* Search */}
          {sessions.length > 0 && (
            <div className="mb-5 relative">
              <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--muted)]" />
              <input type="text" value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)} placeholder="搜索会话..."
                className="h-9 w-full rounded-md border border-[var(--card-border)] bg-[var(--card)] pl-9 pr-3 text-xs outline-none transition focus:border-[var(--accent)] focus:shadow-[var(--shadow-glow)]" />
            </div>
          )}

          {loading ? (
            <div className="flex items-center justify-center py-20"><div className="flex gap-1.5"><span className="typing-dot" /><span className="typing-dot" /><span className="typing-dot" /></div></div>
          ) : filtered.length === 0 ? (
            <div className="card py-16 text-center">
              <MessageCircle size={24} className="mx-auto mb-3 text-[var(--muted)]" />
              <p className="text-sm font-medium">{searchQuery ? "没有匹配的会话" : "还没有会话"}</p>
              <p className="mt-1 text-xs text-[var(--muted)]">{searchQuery ? "尝试更换搜索关键词" : "返回工作台创建你的第一个 Planning Session"}</p>
            </div>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {filtered.map((s) => {
                const st = STATUS[s.status] || { label: s.status, color: "var(--muted)", bg: "var(--accent-soft)", icon: null };
                return (
                  <div key={s.id} className="card card-hover group relative p-4">
                    <button onClick={(e) => { e.preventDefault(); e.stopPropagation(); handleDelete(s.id); }} disabled={deleting === s.id}
                      className="absolute right-2 top-2 flex size-6 items-center justify-center rounded text-[var(--muted)] opacity-0 transition hover:bg-[var(--danger-soft)] hover:text-[var(--danger)] group-hover:opacity-100 cursor-pointer disabled:opacity-50">
                      {deleting === s.id ? <Loader2 size={11} className="animate-spin" /> : <Trash2 size={11} />}
                    </button>
                    <Link href={`/sessions/${s.id}`} className="cursor-pointer block">
                      <div className="mb-1.5 text-sm font-medium group-hover:text-[var(--accent)] truncate pr-5 transition-colors">{s.title}</div>
                      <p className="mb-3 line-clamp-2 text-xs text-[var(--muted)] leading-relaxed">{s.summary || s.input_text}</p>
                      <div className="flex items-center justify-between">
                        <span className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] font-medium" style={{ background: st.bg, color: st.color }}>{st.icon}{st.label}</span>
                        <span className="text-[10px] text-[var(--muted)]">{fmtDate(s.created_at)}</span>
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
