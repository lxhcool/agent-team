"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  Loader2, Sparkles, Clock, X,
  MessageCircle, Trash2, ChevronRight, UsersRound, Zap,
  MessageSquare
} from "lucide-react";
import { useConfirm } from "@/components/ui/confirm-dialog";

import { TopNav } from "./components/topnav";
import { useAuth } from "@/lib/auth";

type FlowSummary = {
  id: string; name: string; description: string | null; target_platform: string;
  current_stage: string; stage_total: number; stage_approved: number;
  created_at: string; updated_at: string;
};

type RoundtableSession = {
  id: string; topic: string; status: string;
  current_round: number; max_rounds: number;
  summary: string | null; created_at: string; updated_at: string;
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
  ready_for_export:{ label: "可导出", color: "var(--success)", bg: "var(--success-soft)", icon: <Sparkles size={9} /> },
  completed:     { label: "已完成", color: "var(--success)", bg: "var(--success-soft)", icon: <Sparkles size={9} /> },
  cancelled:     { label: "已取消", color: "var(--muted)", bg: "var(--accent-soft)", icon: <X size={9} /> },
  failed:        { label: "失败", color: "var(--danger)", bg: "var(--danger-soft)", icon: <X size={9} /> },
  active:        { label: "进行中", color: "var(--accent)", bg: "var(--accent-soft)", icon: <MessageCircle size={9} /> },
  consensus:     { label: "已达成共识", color: "var(--success)", bg: "var(--success-soft)", icon: <Sparkles size={9} /> },
};

const QUICK_TAGS: { label: string; content: string }[] = [
  { label: "做网站", content: "我想做一个品牌官网，需要展示公司介绍、服务内容、案例、联系方式，并且适配手机访问" },
  { label: "做小程序", content: "我想做一个预约类小程序，用户可以浏览服务、选择时间、提交预约，后台可以查看和处理预约" },
  { label: "做管理后台", content: "我想做一个内部管理后台，需要登录、数据看板、列表管理、详情编辑和权限控制" },
  { label: "改现有项目", content: "我想在现有项目里新增一个功能，需要先分析需求、确认范围、补齐方案和验收标准" },
];

const ROUNDTABLE_PRESETS: { icon: string; title: string; desc: string; agents: { emoji: string; name: string; agentKey: string }[]; content: string; participants: string[] }[] = [
  {
    icon: "⚔️", title: "辩论赛", desc: "两个对立观点针锋相对，碰撞思想火花",
    agents: [{ emoji: "🔴", name: "正方辩手", agentKey: "debater_pro" }, { emoji: "🔵", name: "反方辩手", agentKey: "debater_con" }],
    content: "请围绕以下话题展开辩论，正方和反方各抒己见，针锋相对：\n\n正方观点：AI 将让人类更自由\n反方观点：AI 将让人类更依赖",
    participants: ["debater_pro", "debater_con"],
  },
  {
    icon: "💡", title: "头脑风暴", desc: "多角色自由碰撞，激发无限灵感",
    agents: [{ emoji: "🎨", name: "创意狂人", agentKey: "creative_ideator" }, { emoji: "🔧", name: "务实工程师", agentKey: "pragmatic_engineer" }, { emoji: "👤", name: "用户代言人", agentKey: "user_advocate" }],
    content: "请从创意、可行性和用户需求三个视角，围绕以下主题头脑风暴：\n\n如果重新设计社交网络，你会怎么做？",
    participants: ["creative_ideator", "pragmatic_engineer", "user_advocate"],
  },
  {
    icon: "🔍", title: "代码审查", desc: "开发者提交方案，审查员严格把关",
    agents: [{ emoji: "👨‍💻", name: "开发者", agentKey: "backend_dev" }, { emoji: "🕵️", name: "审查员", agentKey: "reviewer" }],
    content: "请模拟代码审查场景，开发者阐述设计思路，审查员从质量、安全性、可维护性严格审查：\n\n审查对象：用 Redis 实现的分布式锁方案",
    participants: ["backend_dev", "reviewer"],
  },
  {
    icon: "📖", title: "故事接龙", desc: "多个 Agent 轮流续写，编织意想不到的故事",
    agents: [{ emoji: "📝", name: "叙事者", agentKey: "storyteller" }, { emoji: "💬", name: "对话师", agentKey: "dialogue_writer" }, { emoji: "🔄", name: "反转王", agentKey: "plot_twister" }],
    content: "请三个角色轮流续写故事：叙事者推进情节，对话师编写对话，反转王在关键时刻制造意想不到的转折：\n\n开头：一个程序员深夜 debug 时，发现代码里有一段从未写过的注释……",
    participants: ["storyteller", "dialogue_writer", "plot_twister"],
  },
  {
    icon: "🎯", title: "模拟面试", desc: "面试官和候选人模拟真实面试场景",
    agents: [{ emoji: "👔", name: "面试官", agentKey: "interviewer" }, { emoji: "🧑‍💼", name: "候选人", agentKey: "interviewee" }],
    content: "请模拟一场高级前端工程师的技术面试，面试官提问并追问，候选人展示技术深度：\n\n面试方向：React 性能优化、系统设计、前端架构",
    participants: ["interviewer", "interviewee"],
  },
  {
    icon: "🎭", title: "角色扮演", desc: "穿越时空的对话，碰撞跨时代智慧",
    agents: [{ emoji: "🏛️", name: "古代哲人", agentKey: "philosopher" }, { emoji: "🚀", name: "未来学家", agentKey: "futurist" }, { emoji: "🧪", name: "科学家", agentKey: "scientist_agent" }],
    content: "三个来自不同时代的角色，围绕以下话题展开跨时空对话：\n\n话题：人类应该如何定义「进步」？",
    participants: ["philosopher", "futurist", "scientist_agent"],
  },
];

const LIMIT = 6;

export default function HomePage() {
  const router = useRouter();
  const { confirm, ConfirmDialog } = useConfirm();
  const { loading: authLoading } = useAuth();
  const [flows, setFlows] = useState<FlowSummary[]>([]);
  const [roundtables, setRoundtables] = useState<RoundtableSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [quickInput, setQuickInput] = useState("");
  const [errorMsg, setErrorMsg] = useState("");
  const [starting, setStarting] = useState(false);
  const [mode, setMode] = useState<"flow" | "roundtable">("flow");
  const [composing, setComposing] = useState(false);
  const [presetParticipants, setPresetParticipants] = useState<string[] | null>(null);

  useEffect(() => {
    Promise.all([
      fetch("/api/flows").then((r) => r.json()).catch(() => []),
      fetch("/api/roundtable-sessions").then((r) => r.json()).catch(() => []),
    ])
      .then(([flowData, rData]) => { setFlows(flowData); setRoundtables(rData); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  const makeFlowName = (text: string) => {
    const firstLine = text.trim().split(/\n/)[0] || text.trim();
    return firstLine.replace(/^我(想|需要|要)/, "").slice(0, 28) || "新项目";
  };

  const handleQuickStart = async () => {
    if (!quickInput.trim() || starting) return;
    setStarting(true); setErrorMsg("");
    try {
      if (mode === "roundtable") {
        const res = await fetch("/api/roundtable-sessions", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ topic: quickInput.trim() }),
        });
        if (res.ok) {
          const data = await res.json();
          // If preset has participants, pass them to roundtable page via URL params
          if (presetParticipants && presetParticipants.length > 0) {
            router.push(`/roundtable/${data.id}?participants=${encodeURIComponent(presetParticipants.join(","))}`);
          } else {
            router.push(`/roundtable/${data.id}`);
          }
        } else { setErrorMsg("创建圆桌讨论失败"); }
      } else {
        const flowRes = await fetch("/api/flows", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: makeFlowName(quickInput),
            description: quickInput.trim(),
            target_platform: "website",
          }),
        });
        if (!flowRes.ok) {
          const data = await flowRes.json().catch(() => ({}));
          throw new Error(data.detail || "创建流程失败");
        }
        const flow = await flowRes.json();
        router.push(`/flows/${flow.id}`);
      }
    } catch (err: any) { setErrorMsg(err.message || "无法连接后端服务"); } finally { setStarting(false); }
  };

  const handleDeleteRoundtable = async (id: string) => {
    if (!await confirm({ description: "确定删除此圆桌讨论？", variant: "destructive" })) return;
    try { await fetch(`/api/roundtable-sessions/${id}`, { method: "DELETE" }); setRoundtables((p) => p.filter((r) => r.id !== id)); } catch {}
  };

  const fmtRelative = (d: string) => {
    const diff = Date.now() - new Date(d).getTime();
    const mins = Math.floor(diff / 60000);
    const hours = Math.floor(diff / 3600000);
    const days = Math.floor(diff / 86400000);
    if (mins < 1) return "刚刚";
    if (mins < 60) return `${mins} 分钟前`;
    if (hours < 24) return `${hours} 小时前`;
    return `${days} 天前`;
  };

  const recentR = roundtables.slice(0, LIMIT);

  if (authLoading) {
    return (
      <div className="min-h-screen bg-slate-50 dark:bg-slate-950 flex items-center justify-center">
        <div className="flex gap-1.5"><span className="typing-dot" /><span className="typing-dot" /><span className="typing-dot" /></div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-indigo-50/30 dark:from-slate-950 dark:via-slate-950 dark:to-indigo-950/20">
      <TopNav />

      <main className="min-w-0 pt-28">
        <div className="mx-auto max-w-6xl px-6 pb-20">
          {/* Hero + Search — Primary focal point */}
          <section className="mb-8">
            {/* Ambient glow */}
            <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[600px] h-[300px] bg-indigo-500/8 dark:bg-indigo-500/5 blur-[100px] rounded-full pointer-events-none" />
            <div className="relative">
              <div className="text-center mb-6">
                <h1 className="text-3xl font-bold tracking-tight sm:text-4xl text-slate-900 dark:text-slate-100">
                  输入需求，<span className="bg-gradient-to-r from-indigo-500 to-violet-500 bg-clip-text text-transparent">推进交付定稿</span>
                </h1>
                <p className="mx-auto mt-2 max-w-lg text-sm text-slate-500 dark:text-slate-400">
                  从一句话需求或已有材料开始，先把产品理解对齐，再逐步完成方案设计、细节确认、开发方案和交付清单，让团队更快进入可开工状态。
                </p>
              </div>

              {/* Search — Glassmorphism hero card */}
              <div className="mx-auto max-w-2xl min-h-[200px]">
                <div className="group relative rounded-2xl bg-white/70 dark:bg-slate-900/70 backdrop-blur-xl shadow-lg shadow-indigo-500/5 dark:shadow-none ring-1 ring-slate-200/80 dark:ring-slate-700/50 hover:ring-indigo-300 dark:hover:ring-indigo-500/30 transition-all duration-300 hover:shadow-xl hover:shadow-indigo-500/8">
                  {/* Mode tabs + action */}
                  <div className="flex items-center justify-between px-4 pt-3 pb-3">
                    <div className="flex gap-1">
                      <button
                        onClick={() => { setMode("flow"); setErrorMsg(""); setPresetParticipants(null); }}
                        className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-all cursor-pointer ${mode === "flow" ? "bg-indigo-50 dark:bg-indigo-500/15 text-indigo-600 dark:text-indigo-300" : "text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200"}`}
                      >
                        <Sparkles size={12} />项目流程
                      </button>
                      <button
                        onClick={() => { setMode("roundtable"); setErrorMsg(""); setPresetParticipants(null); }}
                        className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-all cursor-pointer ${mode === "roundtable" ? "bg-emerald-50 dark:bg-emerald-500/15 text-emerald-600 dark:text-emerald-300" : "text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200"}`}
                      >
                        <UsersRound size={12} />圆桌讨论
                      </button>
                    </div>
                      <div className={`flex items-center gap-1.5 text-xs font-medium ${mode === "flow" ? "text-indigo-500" : "text-emerald-500"}`}>
                      {mode === "flow" ? <Sparkles size={12} /> : <UsersRound size={12} />}
                      {mode === "flow" ? "直接进入阶段对话" : "多人讨论"}
                    </div>
                  </div>
                  {/* Textarea */}
                  <div className="px-4 pb-2">
                    <textarea
                      value={quickInput}
                      onChange={(e) => { setQuickInput(e.target.value); setErrorMsg(""); setPresetParticipants(null); }}
                      onCompositionStart={() => setComposing(true)}
                      onCompositionEnd={() => setComposing(false)}
                      onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !composing && !e.nativeEvent.isComposing && quickInput.trim() && !starting) { e.preventDefault(); handleQuickStart(); } }}
                      placeholder={mode === "flow" ? "描述你的新项目需求，或说明这次迭代要改什么，例如：\n我想做一个宠物店预约小程序，支持服务展示、在线预约、后台处理预约..." : "输入一个有趣的话题，让 AI 们展开讨论...\n也可以点击下方的预设模式快速开始"}
                      rows={4}
                      className="w-full resize-none bg-transparent text-sm text-slate-700 dark:text-slate-200 placeholder:text-slate-400/70 dark:placeholder:text-slate-500/70 outline-none leading-relaxed"
                    />
                  </div>
                  {/* Bottom bar */}
                  <div className="flex items-center justify-between px-4 pb-3">
                    <span className="text-[11px] text-slate-400 dark:text-slate-500">Shift + Enter 换行</span>
                    <button
                      onClick={handleQuickStart} disabled={!quickInput.trim() || starting}
                      className={`flex items-center gap-2 rounded-xl px-5 py-2.5 text-sm font-medium text-white shadow-md transition-all disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer active:scale-[0.97] ${mode === "flow" ? "bg-gradient-to-r from-indigo-600 to-violet-600 shadow-indigo-500/25 hover:from-indigo-700 hover:to-violet-700" : "bg-gradient-to-r from-emerald-600 to-teal-600 shadow-emerald-500/25 hover:from-emerald-700 hover:to-teal-700"}`}
                    >
                      {starting ? <Loader2 size={16} className="animate-spin" /> : <Zap size={16} />}
                      {starting ? "处理中" : mode === "flow" ? "开始分析" : "发起讨论"}
                    </button>
                  </div>
                </div>
                {/* Quick Tags / Roundtable Presets */}
                {mode === "flow" ? (
                  <div className="mt-3 flex items-center justify-center gap-2 py-1">
                    {QUICK_TAGS.map((tag) => (
                      <button
                        key={tag.label}
                        onClick={() => setQuickInput(tag.content)}
                        className="rounded-full bg-white/60 dark:bg-slate-800/60 backdrop-blur-sm px-3.5 py-1.5 text-xs font-medium text-slate-600 dark:text-slate-300 ring-1 ring-slate-200/60 dark:ring-slate-700/40 hover:ring-indigo-300 dark:hover:ring-indigo-600/40 hover:text-indigo-600 dark:hover:text-indigo-400 transition-all cursor-pointer"
                      >
                        {tag.label}
                      </button>
                    ))}
                  </div>
                ) : (
                  <div className="mt-3 flex flex-wrap items-center justify-center gap-2 py-1">
                    {ROUNDTABLE_PRESETS.map((preset) => {
                      const isSelected = presetParticipants && JSON.stringify(presetParticipants) === JSON.stringify(preset.participants);
                      return (
                        <button
                          key={preset.title}
                          onClick={() => { setQuickInput(preset.content); setPresetParticipants(isSelected ? null : preset.participants); }}
                          className={`shrink-0 flex items-center gap-1.5 rounded-full px-3.5 py-1.5 text-xs font-medium ring-1 transition-all cursor-pointer whitespace-nowrap ${isSelected ? "bg-emerald-50 dark:bg-emerald-500/15 text-emerald-600 dark:text-emerald-300 ring-emerald-300 dark:ring-emerald-500/60" : "bg-white/60 dark:bg-slate-800/60 backdrop-blur-sm text-slate-600 dark:text-slate-300 ring-slate-200/60 dark:ring-slate-700/40 hover:ring-emerald-300 dark:hover:ring-emerald-500/40 hover:text-emerald-600 dark:hover:text-emerald-400"}`}
                        >
                          <span>{preset.icon}</span>
                          {preset.title}
                        </button>
                      );
                    })}
                  </div>
                )}
                {errorMsg && (
                  <div className="mt-3 flex items-center gap-2 rounded-xl border border-red-200 dark:border-red-900/30 bg-red-50/80 dark:bg-red-500/10 backdrop-blur-sm px-4 py-2.5 text-xs text-red-600 dark:text-red-400">
                    <X size={12} /><span>{errorMsg}</span>
                  </div>
                )}
              </div>
            </div>
          </section>

          {loading ? (
            <div className="flex items-center justify-center py-20">
              <div className="flex gap-1.5"><span className="typing-dot" /><span className="typing-dot" /><span className="typing-dot" /></div>
            </div>
          ) : (
            <>
              <section className="mb-6 space-y-3">
                <div className="flex items-center justify-between px-1">
                  <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-700 dark:text-slate-200">
                    <div className="flex size-5 items-center justify-center rounded-md bg-violet-100 dark:bg-violet-500/15">
                      <Sparkles size={11} className="text-violet-600 dark:text-violet-400" />
                    </div>
                    最近流程
                  </h2>
                  <Link href="/flows" className="flex items-center gap-0.5 text-xs text-slate-400 dark:text-slate-500 hover:text-violet-500 dark:hover:text-violet-400 transition-colors duration-200 cursor-pointer">
                    查看全部 <ChevronRight size={12} />
                  </Link>
                </div>
                {flows.length === 0 ? (
                  <div className="flex items-center gap-3 rounded-xl bg-white/70 dark:bg-slate-900/70 backdrop-blur-sm ring-1 ring-slate-200/60 dark:ring-slate-700/40 px-4 py-3.5">
                    <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-slate-100 dark:bg-slate-800 text-slate-300 dark:text-slate-600">
                      <Sparkles size={15} strokeWidth={1.5} />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="text-[13px] font-medium text-slate-400 dark:text-slate-500">还没有流程</div>
                      <div className="text-[11px] text-slate-300 dark:text-slate-600 mt-1">在上方输入需求后，会自动创建第一条流程</div>
                    </div>
                  </div>
                ) : (
                  <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                    {flows.slice(0, 3).map((flow) => {
                      const progress = flow.stage_total
                        ? Math.round((flow.stage_approved / flow.stage_total) * 100)
                        : 0;
                      const st = STATUS[flow.current_stage] || STATUS.created;
                      return (
                        <Link
                          key={flow.id}
                          href={`/flows/${flow.id}`}
                          className="group relative rounded-xl bg-white/70 dark:bg-slate-900/70 backdrop-blur-sm ring-1 ring-slate-200/60 dark:ring-slate-700/40 transition-all duration-200 hover:ring-violet-300/80 dark:hover:ring-violet-500/30 hover:shadow-md hover:shadow-violet-500/5 flex flex-col"
                        >
                          {/* Card body */}
                          <div className="px-4 pt-3.5 pb-2">
                            <div className="flex items-start gap-3">
                              {/* Icon */}
                              <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-violet-50 dark:bg-violet-500/10 text-violet-600 dark:text-violet-400">
                                <Sparkles size={16} />
                              </div>
                              {/* Info */}
                              <div className="min-w-0 flex-1 pr-5">
                                <h3 className="text-[13px] font-semibold text-slate-800 dark:text-slate-100 truncate">{flow.name}</h3>
                                <div className="mt-1 flex items-center gap-1.5 text-[11px] text-slate-500 dark:text-slate-400">
                                  <span className="inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[9px] font-semibold" style={{ background: st.bg, color: st.color as string }}>{st.icon}{st.label}</span>
                                  <span className="text-slate-300 dark:text-slate-600">·</span>
                                  <span>{flow.target_platform}</span>
                                </div>
                                <p className="mt-1 text-[11px] text-slate-400 dark:text-slate-500 truncate">{flow.description || "等待补充项目目标"}</p>
                              </div>
                            </div>
                          </div>
                          {/* Card bottom: progress bar */}
                          <div className="mt-auto px-4 py-2 border-t border-slate-100 dark:border-slate-800/60">
                            <div className="flex items-center gap-2">
                              <div className="flex-1 h-1.5 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                                <div className="h-full rounded-full bg-violet-500 transition-all" style={{ width: `${progress}%` }} />
                              </div>
                              <span className="text-[10px] font-medium text-violet-600 dark:text-violet-400">{progress}%</span>
                            </div>
                          </div>
                        </Link>
                      );
                    })}
                  </div>
                )}
              </section>

              <section className="space-y-3">
                <div className="flex items-center justify-between px-1">
                  <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-700 dark:text-slate-200">
                    <div className="flex size-5 items-center justify-center rounded-md bg-emerald-100 dark:bg-emerald-500/15">
                      <UsersRound size={11} className="text-emerald-600 dark:text-emerald-400" />
                    </div>
                    圆桌讨论
                  </h2>
                  <Link href="/roundtable" className="flex items-center gap-0.5 text-xs text-slate-400 dark:text-slate-500 hover:text-emerald-500 dark:hover:text-emerald-400 transition-colors duration-200 cursor-pointer">
                    查看全部 <ChevronRight size={12} />
                  </Link>
                </div>
                {recentR.length === 0 ? (
                  <div className="flex items-center gap-3 rounded-xl bg-white/70 dark:bg-slate-900/70 backdrop-blur-sm ring-1 ring-slate-200/60 dark:ring-slate-700/40 px-4 py-3.5">
                    <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-slate-100 dark:bg-slate-800 text-slate-300 dark:text-slate-600">
                      <UsersRound size={15} strokeWidth={1.5} />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="text-[13px] font-medium text-slate-400 dark:text-slate-500">还没有圆桌讨论</div>
                      <div className="text-[11px] text-slate-300 dark:text-slate-600 mt-1">选择预设模式快速开始</div>
                    </div>
                  </div>
                ) : (
                  <div className="space-y-2">
                    {recentR.map((r) => {
                      const st = STATUS[r.status] || { label: r.status, color: "#9ca3af", bg: "rgba(16,185,129,0.06)" };
                      return (
                        <Link key={r.id} href={`/roundtable/${r.id}`} className="group flex items-center gap-3 rounded-xl bg-white/70 dark:bg-slate-900/70 backdrop-blur-sm ring-1 ring-slate-200/60 dark:ring-slate-700/40 px-4 py-3.5 transition-all duration-200 hover:ring-emerald-300/80 dark:hover:ring-emerald-500/30 hover:bg-emerald-50/50 dark:hover:bg-emerald-950/30 hover:shadow-sm hover:shadow-emerald-500/5 cursor-pointer">
                          <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-emerald-50 dark:bg-emerald-500/10 text-emerald-500 transition-colors duration-200">
                            <MessageSquare size={15} />
                          </div>
                          <div className="min-w-0 flex-1">
                            <div className="text-[13px] font-medium text-slate-800 dark:text-slate-100 truncate group-hover:text-emerald-700 dark:group-hover:text-emerald-300 transition-colors duration-200">{r.topic}</div>
                            <div className="flex items-center gap-2 mt-1">
                              <span className="inline-flex items-center rounded-full px-1.5 py-0.5 text-[10px] font-semibold" style={{ background: st.bg, color: st.color as string }}>{st.label}</span>
                              <span className="text-[11px] text-slate-400 dark:text-slate-500">{fmtRelative(r.created_at)}</span>
                            </div>
                          </div>
                          <button
                            onClick={(e) => { e.preventDefault(); e.stopPropagation(); handleDeleteRoundtable(r.id); }}
                            className="flex size-7 shrink-0 items-center justify-center rounded-lg text-slate-300 dark:text-slate-600 opacity-0 transition-all duration-200 hover:bg-red-50 dark:hover:bg-red-500/10 hover:text-red-500 group-hover:opacity-100 cursor-pointer"
                          >
                            <Trash2 size={12} />
                          </button>
                        </Link>
                      );
                    })}
                  </div>
                )}
              </section>
            </>
          )}
        </div>
      </main>
      {ConfirmDialog}
    </div>
  );
}
