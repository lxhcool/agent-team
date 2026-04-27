"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  Loader2, Sparkles, X, Trash2, Search, UsersRound, Plus, Zap, MessageSquare
} from "lucide-react";
import { TopNav } from "../components/topnav";
import { useAuth } from "@/lib/auth";
import { useConfirm } from "@/components/ui/confirm-dialog";

type RoundtableSession = {
  id: string; topic: string; status: string;
  current_round: number; max_rounds: number;
  summary: string | null; created_at: string; updated_at: string;
};

const STATUS: Record<string, { label: string; color: string; bg: string }> = {
  active:    { label: "进行中", color: "#10b981", bg: "rgba(16,185,129,0.06)" },
  completed: { label: "已完成", color: "#10b981", bg: "rgba(16,185,129,0.06)" },
  consensus: { label: "已达成共识", color: "#10b981", bg: "rgba(16,185,129,0.06)" },
  cancelled: { label: "已取消", color: "#9ca3af", bg: "rgba(99,102,241,0.06)" },
};

export default function RoundtableListPage() {
  const router = useRouter();
  const { confirm, ConfirmDialog } = useConfirm();
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
    if (!await confirm({ description: "确定删除此圆桌讨论？", variant: "destructive" })) return;
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
    <div className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-emerald-50/30 dark:from-slate-950 dark:via-slate-950 dark:to-emerald-950/20">
      <TopNav />
      <main className="min-w-0 pt-14">
        <div className="mx-auto max-w-5xl px-6 py-8">
          {/* Header */}
          <div className="mb-6 flex items-center justify-between">
            <div>
              <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight text-slate-900 dark:text-slate-100">
                <div className="flex size-8 items-center justify-center rounded-lg bg-emerald-100 dark:bg-emerald-500/15">
                  <UsersRound size={16} className="text-emerald-600 dark:text-emerald-400" />
                </div>
                圆桌讨论
              </h1>
              <p className="mt-1.5 text-sm text-slate-500 dark:text-slate-400">让多个 Agent 围绕话题深入探讨</p>
            </div>
            <button
              onClick={() => setShowCreate(!showCreate)}
              className="flex items-center gap-1.5 rounded-xl bg-emerald-600 dark:bg-emerald-500 px-4 py-2.5 text-sm font-medium text-white shadow-md shadow-emerald-500/20 hover:bg-emerald-700 dark:hover:bg-emerald-600 transition-all duration-200 cursor-pointer active:scale-[0.97]"
            >
              <Plus size={15} />
              发起讨论
            </button>
          </div>

          {/* Create Form */}
          {showCreate && (
            <div className="mb-6 rounded-xl bg-white/70 dark:bg-slate-900/70 backdrop-blur-sm p-4 ring-1 ring-slate-200/60 dark:ring-slate-700/40 shadow-sm shadow-emerald-500/5">
              <div className="flex items-center gap-3">
                <div className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-emerald-100 dark:bg-emerald-500/15 text-emerald-600 dark:text-emerald-400">
                  <UsersRound size={18} />
                </div>
                <input
                  type="text" value={topic}
                  onChange={(e) => { setTopic(e.target.value); setErrorMsg(""); }}
                  onKeyDown={(e) => { if (e.key === "Enter" && topic.trim() && !creating) handleCreate(); }}
                  placeholder="输入讨论话题，例如：如何优化系统架构..."
                  className="h-11 flex-1 bg-white/50 dark:bg-slate-800/50 rounded-xl px-4 text-sm text-slate-700 dark:text-slate-200 placeholder:text-slate-400 dark:placeholder:text-slate-500 outline-none ring-1 ring-slate-200 dark:ring-slate-700 focus:ring-emerald-300 dark:focus:ring-emerald-500/40 transition-all duration-200"
                  autoFocus
                />
                <button
                  onClick={handleCreate} disabled={!topic.trim() || creating}
                  className="flex h-11 shrink-0 items-center gap-2 rounded-xl bg-emerald-600 dark:bg-emerald-500 px-5 text-sm font-medium text-white shadow-md shadow-emerald-500/20 hover:bg-emerald-700 dark:hover:bg-emerald-600 disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer active:scale-[0.97] transition-all duration-200"
                >
                  {creating ? <Loader2 size={15} className="animate-spin" /> : <Zap size={15} />}
                  开始
                </button>
                <button
                  onClick={() => { setShowCreate(false); setTopic(""); setErrorMsg(""); }}
                  className="flex size-11 items-center justify-center rounded-xl text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors duration-200 cursor-pointer"
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

          {/* Search */}
          {roundtables.length > 0 && (
            <div className="mb-5 relative">
              <Search size={14} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400 dark:text-slate-500" />
              <input type="text" value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)} placeholder="搜索圆桌讨论..."
                className="h-10 w-full rounded-xl bg-white/70 dark:bg-slate-900/70 backdrop-blur-sm pl-10 pr-4 text-sm text-slate-700 dark:text-slate-200 placeholder:text-slate-400 dark:placeholder:text-slate-500 outline-none ring-1 ring-slate-200/60 dark:ring-slate-700/40 focus:ring-emerald-300 dark:focus:ring-emerald-500/40 transition-all duration-200" />
            </div>
          )}

          {loading ? (
            <div className="flex items-center justify-center py-20"><div className="flex gap-1.5"><span className="typing-dot" /><span className="typing-dot" /><span className="typing-dot" /></div></div>
          ) : filtered.length === 0 ? (
            <div className="flex items-center gap-3 rounded-xl bg-white/70 dark:bg-slate-900/70 backdrop-blur-sm ring-1 ring-slate-200/60 dark:ring-slate-700/40 px-4 py-3.5">
              <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-slate-100 dark:bg-slate-800 text-slate-300 dark:text-slate-600">
                <UsersRound size={15} strokeWidth={1.5} />
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-[13px] font-medium text-slate-400 dark:text-slate-500">{searchQuery ? "没有匹配的讨论" : "还没有圆桌讨论"}</div>
                <div className="text-[11px] text-slate-300 dark:text-slate-600 mt-1">{searchQuery ? "尝试更换搜索关键词" : "点击上方按钮发起第一个讨论"}</div>
              </div>
            </div>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {filtered.map((r) => {
                const st = STATUS[r.status] || { label: r.status, color: "#9ca3af", bg: "rgba(99,102,241,0.06)" };
                const isActive = r.status === "active";
                return (
                  <div key={r.id} className="group relative rounded-xl bg-white/70 dark:bg-slate-900/70 backdrop-blur-sm ring-1 ring-slate-200/60 dark:ring-slate-700/40 p-4 transition-all duration-200 hover:ring-emerald-300/80 dark:hover:ring-emerald-500/30 hover:bg-emerald-50/30 dark:hover:bg-emerald-950/20 hover:shadow-sm hover:shadow-emerald-500/5">
                    <button onClick={(e) => { e.preventDefault(); e.stopPropagation(); handleDelete(r.id); }} disabled={deleting === r.id}
                      className="absolute right-2.5 top-2.5 flex size-7 items-center justify-center rounded-lg text-slate-300 dark:text-slate-600 opacity-0 transition-all duration-200 hover:bg-red-50 dark:hover:bg-red-500/10 hover:text-red-500 group-hover:opacity-100 cursor-pointer disabled:opacity-50">
                      {deleting === r.id ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
                    </button>
                    <Link href={`/roundtable/${r.id}`} className="cursor-pointer block">
                      <div className="mb-2 flex items-start gap-2.5">
                        <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-emerald-50 dark:bg-emerald-500/10 text-emerald-500 transition-colors duration-200">
                          {isActive ? <Loader2 size={15} className="animate-spin" /> : <MessageSquare size={15} />}
                        </div>
                        <div className="min-w-0 flex-1 pr-5">
                          <div className="text-[13px] font-medium text-slate-800 dark:text-slate-100 truncate group-hover:text-emerald-700 dark:group-hover:text-emerald-300 transition-colors duration-200">{r.topic}</div>
                          <p className="mt-1 line-clamp-2 text-xs text-slate-500 dark:text-slate-400 leading-relaxed">{r.summary || `第 ${r.current_round} / ${r.max_rounds} 轮讨论`}</p>
                        </div>
                      </div>
                      <div className="flex items-center justify-between pt-2 border-t border-slate-100/80 dark:border-slate-800/60">
                        <span className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold" style={{ background: st.bg, color: st.color }}>{st.label}</span>
                        <span className="text-[11px] text-slate-400 dark:text-slate-500">{fmtDate(r.created_at)}</span>
                      </div>
                    </Link>
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
