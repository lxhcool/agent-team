"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  ChevronRight,
  Clock,
  Loader2,
  Sparkles,
  X,
  Zap,
} from "lucide-react";

import { TopNav } from "./components/topnav";
import { useAuth } from "@/lib/auth";

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
  created: { label: "待处理", color: "var(--muted)", bg: "var(--accent-soft)", icon: <Clock size={9} /> },
  planning: { label: "规划中", color: "var(--accent)", bg: "var(--accent-soft)", icon: <Loader2 size={9} className="animate-spin" /> },
  analyzing: { label: "分析中", color: "var(--accent)", bg: "var(--accent-soft)", icon: <Loader2 size={9} className="animate-spin" /> },
  researching: { label: "调研中", color: "var(--accent)", bg: "var(--accent-soft)", icon: <Loader2 size={9} className="animate-spin" /> },
  generating_proposal: { label: "生成方案", color: "var(--accent)", bg: "var(--accent-soft)", icon: <Sparkles size={9} /> },
  reviewing: { label: "审查中", color: "var(--accent)", bg: "var(--accent-soft)", icon: <Loader2 size={9} className="animate-spin" /> },
  awaiting_approval: { label: "待审批", color: "var(--warning)", bg: "var(--warning-soft)", icon: <Sparkles size={9} /> },
  generating_plan: { label: "生成计划", color: "var(--accent)", bg: "var(--accent-soft)", icon: <Sparkles size={9} /> },
  ready_for_export: { label: "可导出", color: "var(--success)", bg: "var(--success-soft)", icon: <Sparkles size={9} /> },
  completed: { label: "已完成", color: "var(--success)", bg: "var(--success-soft)", icon: <Sparkles size={9} /> },
  cancelled: { label: "已取消", color: "var(--muted)", bg: "var(--accent-soft)", icon: <X size={9} /> },
  failed: { label: "失败", color: "var(--danger)", bg: "var(--danger-soft)", icon: <X size={9} /> },
};

const QUICK_TAGS: { label: string; content: string }[] = [
  { label: "做网站", content: "我想做一个品牌官网，需要展示公司介绍、服务内容、案例、联系方式，并且适配手机访问" },
  { label: "做小程序", content: "我想做一个预约类小程序，用户可以浏览服务、选择时间、提交预约，后台可以查看和处理预约" },
  { label: "做管理后台", content: "我想做一个内部管理后台，需要登录、数据看板、列表管理、详情编辑和权限控制" },
  { label: "改现有项目", content: "我想在现有项目里新增一个功能，需要先分析需求、确认范围、补齐方案和验收标准" },
];

export default function HomePage() {
  const router = useRouter();
  const { loading: authLoading } = useAuth();
  const [flows, setFlows] = useState<FlowSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [quickInput, setQuickInput] = useState("");
  const [errorMsg, setErrorMsg] = useState("");
  const [starting, setStarting] = useState(false);
  const [composing, setComposing] = useState(false);

  useEffect(() => {
    fetch("/api/flows")
      .then((r) => r.json())
      .then((flowData) => {
        setFlows(flowData);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  const makeFlowName = (text: string) => {
    const firstLine = text.trim().split(/\n/)[0] || text.trim();
    return firstLine.replace(/^我(想|需要|要)/, "").slice(0, 28) || "新项目";
  };

  const handleQuickStart = async () => {
    if (!quickInput.trim() || starting) return;
    setStarting(true);
    setErrorMsg("");
    try {
      const flowRes = await fetch("/api/flows", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
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
    } catch (err: any) {
      setErrorMsg(err.message || "无法连接后端服务");
    } finally {
      setStarting(false);
    }
  };

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
          <section className="mb-8">
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

              <div className="mx-auto max-w-2xl min-h-[200px]">
                <div className="group relative rounded-2xl bg-white/70 dark:bg-slate-900/70 backdrop-blur-xl shadow-lg shadow-indigo-500/5 dark:shadow-none ring-1 ring-slate-200/80 dark:ring-slate-700/50 hover:ring-indigo-300 dark:hover:ring-indigo-500/30 transition-all duration-300 hover:shadow-xl hover:shadow-indigo-500/8">
                  <div className="flex items-center justify-between px-4 pt-3 pb-3">
                    <div className="flex items-center gap-1.5 rounded-lg bg-indigo-50 dark:bg-indigo-500/15 px-3 py-1.5 text-xs font-medium text-indigo-600 dark:text-indigo-300">
                      <Sparkles size={12} />项目流程
                    </div>
                    <div className="flex items-center gap-1.5 text-xs font-medium text-indigo-500">
                      <Sparkles size={12} />
                      直接进入阶段对话
                    </div>
                  </div>

                  <div className="px-4 pb-2">
                    <textarea
                      value={quickInput}
                      onChange={(e) => { setQuickInput(e.target.value); setErrorMsg(""); }}
                      onCompositionStart={() => setComposing(true)}
                      onCompositionEnd={() => setComposing(false)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey && !composing && !e.nativeEvent.isComposing && quickInput.trim() && !starting) {
                          e.preventDefault();
                          handleQuickStart();
                        }
                      }}
                      placeholder={"描述你的新项目需求，或说明这次迭代要改什么，例如：\n我想做一个宠物店预约小程序，支持服务展示、在线预约、后台处理预约..."}
                      rows={4}
                      className="w-full resize-none bg-transparent text-sm text-slate-700 dark:text-slate-200 placeholder:text-slate-400/70 dark:placeholder:text-slate-500/70 outline-none leading-relaxed"
                    />
                  </div>

                  <div className="flex items-center justify-between px-4 pb-3">
                    <span className="text-[11px] text-slate-400 dark:text-slate-500">Shift + Enter 换行</span>
                    <button
                      onClick={handleQuickStart}
                      disabled={!quickInput.trim() || starting}
                      className="flex items-center gap-2 rounded-xl bg-gradient-to-r from-indigo-600 to-violet-600 px-5 py-2.5 text-sm font-medium text-white shadow-md shadow-indigo-500/25 transition-all hover:from-indigo-700 hover:to-violet-700 disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer active:scale-[0.97]"
                    >
                      {starting ? <Loader2 size={16} className="animate-spin" /> : <Zap size={16} />}
                      {starting ? "处理中" : "开始分析"}
                    </button>
                  </div>
                </div>

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
                        <div className="px-4 pt-3.5 pb-2">
                          <div className="flex items-start gap-3">
                            <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-violet-50 dark:bg-violet-500/10 text-violet-600 dark:text-violet-400">
                              <Sparkles size={16} />
                            </div>
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
          )}
        </div>
      </main>
    </div>
  );
}
