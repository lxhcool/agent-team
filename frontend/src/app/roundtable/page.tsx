"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  Loader2, Sparkles, X, MessageCircle, Trash2, Search, UsersRound, Plus, Zap
} from "lucide-react";
import { TopNav } from "../components/topnav";
import { useAuth } from "@/lib/auth";

type RoundtableSession = {
  id: string; topic: string; status: string;
  current_round: number; max_rounds: number;
  summary: string | null; created_at: string; updated_at: string;
};

const STATUS: Record<string, { label: string; color: string; bg: string; icon: React.ReactNode }> = {
  active:    { label: "进行中", color: "var(--accent)", bg: "var(--accent-soft)", icon: <MessageCircle size={9} /> },
  completed: { label: "已完成", color: "var(--success)", bg: "var(--success-soft)", icon: <Sparkles size={9} /> },
  consensus: { label: "已达成共识", color: "var(--success)", bg: "var(--success-soft)", icon: <Sparkles size={9} /> },
  cancelled: { label: "已取消", color: "var(--muted)", bg: "var(--accent-soft)", icon: <X size={9} /> },
};

export default function RoundtableListPage() {
  const router = useRouter();
  const { loading: authLoading } = useAuth();
  const [roundtables, setRoundtables] = useState<RoundtableSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [deleting, setDeleting] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [topic, setTopic] = useState("");
  const [creating, setCreating] = useState(false);
  const [errorMsg, setErrorMsg] = useState("");

  useEffect(() => {
    fetch("/api/roundtable-sessions")
      .then((r) => r.json())
      .then((data) => { setRoundtables(data); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  const handleCreate = async () => {
    if (!topic.trim() || creating) return;
    setCreating(true); setErrorMsg("");
    try {
      const res = await fetch("/api/roundtable-sessions", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ topic: topic.trim() }),
      });
      if (res.ok) {
        const data = await res.json();
        router.push(`/roundtable/${data.id}`);
      } else { setErrorMsg("创建失败，请检查后端服务"); }
    } catch { setErrorMsg("无法连接后端服务"); } finally { setCreating(false); }
  };

  const handleDelete = async (id: string) => {
    if (!confirm("确定删除此圆桌讨论？")) return;
    setDeleting(id);
    try { await fetch(`/api/roundtable-sessions/${id}`, { method: "DELETE" }); setRoundtables((p) => p.filter((r) => r.id !== id)); } catch {} finally { setDeleting(null); }
  };

  const fmtDate = (d: string) => new Date(d).toLocaleString("zh-CN", { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

  const filtered = roundtables.filter((r) => {
    if (!searchQuery.trim()) return true;
    const q = searchQuery.toLowerCase();
    return r.topic.toLowerCase().includes(q) || (r.summary && r.summary.toLowerCase().includes(q));
  });

  if (authLoading) return <div className="min-h-screen flex items-center justify-center"><Loader2 className="animate-spin text-slate-400" size={24} /></div>;

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-indigo-50/30 dark:from-slate-950 dark:via-slate-950 dark:to-indigo-950/20">
      <TopNav />
      <main className="min-w-0 pt-14">
        <div className="mx-auto max-w-5xl px-6 py-8">
          <div className="mb-6 flex items-center justify-between">
            <div>
              <h1 className="text-2xl font-semibold tracking-tight text-slate-900 dark:text-slate-100">圆桌讨论</h1>
              <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">让多个 Agent 围绕话题深入探讨</p>
            </div>
            <button
              onClick={() => setShowCreate(!showCreate)}
              className="flex items-center gap-1.5 rounded-xl bg-gradient-to-r from-indigo-600 to-violet-600 px-4 py-2.5 text-sm font-medium text-white shadow-md shadow-indigo-500/20 hover:from-indigo-700 hover:to-violet-700 transition-all cursor-pointer active:scale-[0.97]"
            >
              <Plus size={16} />
              发起讨论
            </button>
          </div>

          {/* Create Form */}
          {showCreate && (
            <div className="mb-6 rounded-2xl bg-white/70 dark:bg-slate-900/70 backdrop-blur-xl p-4 ring-1 ring-slate-200/80 dark:ring-slate-700/50 shadow-lg shadow-indigo-500/5">
              <div className="flex items-center gap-3">
                <div className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 to-violet-500 text-white shadow-md shadow-indigo-500/25">
                  <UsersRound size={18} />
                </div>
                <input
                  type="text" value={topic}
                  onChange={(e) => { setTopic(e.target.value); setErrorMsg(""); }}
                  onKeyDown={(e) => { if (e.key === "Enter" && topic.trim() && !creating) handleCreate(); }}
                  placeholder="输入讨论话题，例如：如何优化系统架构..."
                  className="h-11 flex-1 bg-white/50 dark:bg-slate-800/50 rounded-xl px-4 text-sm text-slate-700 dark:text-slate-200 placeholder:text-slate-400 dark:placeholder:text-slate-500 outline-none ring-1 ring-slate-200 dark:ring-slate-700 focus:ring-indigo-300 dark:focus:ring-indigo-500/40 transition"
                  autoFocus
                />
                <button
                  onClick={handleCreate} disabled={!topic.trim() || creating}
                  className="flex h-11 shrink-0 items-center gap-2 rounded-xl bg-gradient-to-r from-indigo-600 to-violet-600 px-5 text-sm font-medium text-white shadow-md shadow-indigo-500/20 hover:from-indigo-700 hover:to-violet-700 disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer active:scale-[0.97] transition-all"
                >
                  {creating ? <Loader2 size={16} className="animate-spin" /> : <Zap size={16} />}
                  开始
                </button>
                <button
                  onClick={() => { setShowCreate(false); setTopic(""); setErrorMsg(""); }}
                  className="flex size-11 items-center justify-center rounded-xl text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800 transition cursor-pointer"
                >
                  <X size={18} />
                </button>
              </div>
              {errorMsg && (
                <div className="mt-3 flex items-center gap-2 rounded-lg bg-red-50 dark:bg-red-500/10 px-3 py-2 text-xs text-red-600 dark:text-red-400">
                  <X size={12} /><span>{errorMsg}</span>
                </div>
              )}
            </div>
          )}

          {roundtables.length > 0 && (
            <div className="mb-5 relative">
              <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
              <input type="text" value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)} placeholder="搜索圆桌讨论..."
                className="h-9 w-full rounded-xl bg-white/60 dark:bg-slate-900/60 backdrop-blur-sm pl-9 pr-3 text-xs outline-none ring-1 ring-slate-200/60 dark:ring-slate-700/40 focus:ring-indigo-300 dark:focus:ring-indigo-500/40 transition" />
            </div>
          )}

          {loading ? (
            <div className="flex items-center justify-center py-20"><div className="flex gap-1.5"><span className="typing-dot" /><span className="typing-dot" /><span className="typing-dot" /></div></div>
          ) : filtered.length === 0 ? (
            <div className="rounded-2xl bg-white/60 dark:bg-slate-900/60 backdrop-blur-sm ring-1 ring-slate-200/60 dark:ring-slate-700/40 py-16 text-center">
              <UsersRound size={24} className="mx-auto mb-3 text-slate-300 dark:text-slate-600" strokeWidth={1.5} />
              <p className="text-sm font-medium text-slate-700 dark:text-slate-200">{searchQuery ? "没有匹配的讨论" : "还没有圆桌讨论"}</p>
              <p className="mt-1 text-xs text-slate-400 dark:text-slate-500">{searchQuery ? "尝试更换搜索关键词" : "点击上方按钮发起第一个讨论"}</p>
            </div>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {filtered.map((r) => {
                const st = STATUS[r.status] || { label: r.status, color: "var(--muted)", bg: "var(--accent-soft)", icon: null };
                return (
                  <div key={r.id} className="group relative rounded-2xl bg-white/60 dark:bg-slate-900/60 backdrop-blur-sm ring-1 ring-slate-200/60 dark:ring-slate-700/40 p-4 transition-all hover:shadow-md hover:ring-indigo-300/60 dark:hover:ring-indigo-600/40">
                    <button onClick={(e) => { e.preventDefault(); e.stopPropagation(); handleDelete(r.id); }} disabled={deleting === r.id}
                      className="absolute right-2 top-2 flex size-6 items-center justify-center rounded-lg text-slate-300 dark:text-slate-600 opacity-0 transition hover:bg-red-50 dark:hover:bg-red-500/10 hover:text-red-500 group-hover:opacity-100 cursor-pointer disabled:opacity-50">
                      {deleting === r.id ? <Loader2 size={11} className="animate-spin" /> : <Trash2 size={11} />}
                    </button>
                    <Link href={`/roundtable/${r.id}`} className="cursor-pointer block">
                      <div className="mb-1.5 text-sm font-medium text-slate-800 dark:text-slate-100 group-hover:text-indigo-600 dark:group-hover:text-indigo-400 truncate pr-5 transition-colors">{r.topic}</div>
                      <p className="mb-3 line-clamp-2 text-xs text-slate-500 dark:text-slate-400 leading-relaxed">{r.summary || `第 ${r.current_round} / ${r.max_rounds} 轮讨论`}</p>
                      <div className="flex items-center justify-between">
                        <span className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] font-medium" style={{ background: st.bg, color: st.color }}>{st.icon}{st.label}</span>
                        <span className="text-[10px] text-slate-400 dark:text-slate-500">{fmtDate(r.created_at)}</span>
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
