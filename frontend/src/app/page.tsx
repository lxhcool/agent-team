"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  Loader2, Sparkles, X,
  ChevronRight, Zap, FolderKanban
} from "lucide-react";

import { TopNav } from "./components/topnav";
import { useAuth } from "@/lib/auth";

type WorkspaceSummary = {
  id: string; name: string; description: string | null; target_platform: string;
  current_stage: string; stage_total: number; stage_approved: number;
  created_at: string; updated_at: string;
};

const QUICK_TAGS: { label: string; content: string }[] = [
  { label: "做网站", content: "我想做一个品牌官网，需要展示公司介绍、服务内容、案例、联系方式，并且适配手机访问" },
  { label: "做小程序", content: "我想做一个预约类小程序，用户可以浏览服务、选择时间、提交预约，后台可以查看和处理预约" },
  { label: "做管理后台", content: "我想做一个内部管理后台，需要登录、数据看板、列表管理、详情编辑和权限控制" },
  { label: "改现有项目", content: "我想在现有项目里新增一个功能，需要先分析需求、确认方案，再进入开发执行" },
];

export default function HomePage() {
  const router = useRouter();
  const { loading: authLoading } = useAuth();
  const [workspaces, setWorkspaces] = useState<WorkspaceSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [quickInput, setQuickInput] = useState("");
  const [errorMsg, setErrorMsg] = useState("");
  const [starting, setStarting] = useState(false);
  const [composing, setComposing] = useState(false);

  useEffect(() => {
    fetch("/api/workspaces")
      .then((r) => r.json())
      .then((wData) => { setWorkspaces(wData); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  const makeWorkspaceName = (text: string) => {
    const firstLine = text.trim().split(/\n/)[0] || text.trim();
    return firstLine.replace(/^我(想|需要|要)/, "").slice(0, 28) || "新项目";
  };

  const handleQuickStart = async () => {
    if (!quickInput.trim() || starting) return;
    setStarting(true); setErrorMsg("");
    try {
      const workspaceRes = await fetch("/api/workspaces", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: makeWorkspaceName(quickInput),
          description: quickInput.trim(),
          target_platform: "website",
        }),
      });
      if (!workspaceRes.ok) {
        const data = await workspaceRes.json().catch(() => ({}));
        throw new Error(data.detail || "创建工作区失败");
      }
      const workspace = await workspaceRes.json();
      router.push(`/workspaces/${workspace.id}`);
    } catch (err: any) { setErrorMsg(err.message || "无法连接后端服务"); } finally { setStarting(false); }
  };

  const workspaceProgress = (workspace: WorkspaceSummary) =>
    workspace.stage_total ? Math.round((workspace.stage_approved / workspace.stage_total) * 100) : 0;

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
          {/* Hero + Search */}
          <section className="mb-8">
            <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[600px] h-[300px] bg-indigo-500/8 dark:bg-indigo-500/5 blur-[100px] rounded-full pointer-events-none" />
            <div className="relative">
              <div className="text-center mb-6">
                <h1 className="text-3xl font-bold tracking-tight sm:text-4xl text-slate-900 dark:text-slate-100">
                  把目标交给 <span className="bg-gradient-to-r from-indigo-500 to-violet-500 bg-clip-text text-transparent">Agent Team</span>
                </h1>
                <p className="mx-auto mt-2 max-w-lg text-sm text-slate-500 dark:text-slate-400">
                  首页只负责进入平台和开始一个任务。输入你想完成的事情，系统会创建一个 Workspace 并持续推进。
                </p>
              </div>

              <div className="mx-auto max-w-2xl min-h-[200px]">
                <div className="group relative rounded-2xl bg-white/70 dark:bg-slate-900/70 backdrop-blur-xl shadow-lg shadow-indigo-500/5 dark:shadow-none ring-1 ring-slate-200/80 dark:ring-slate-700/50 hover:ring-indigo-300 dark:hover:ring-indigo-500/30 transition-all duration-300 hover:shadow-xl hover:shadow-indigo-500/8">
                  <div className="flex items-center justify-between px-4 pt-3 pb-3">
                    <div className="flex items-center gap-1.5 rounded-lg bg-indigo-50 dark:bg-indigo-500/15 px-3 py-1.5 text-xs font-medium text-indigo-600 dark:text-indigo-300">
                      <FolderKanban size={12} />
                      新建 Workspace
                    </div>
                    <div className="flex items-center gap-1.5 text-xs font-medium text-indigo-500">
                      <Sparkles size={12} />
                      平台主任务入口
                    </div>
                  </div>
                  <div className="px-4 pb-2">
                    <textarea
                      value={quickInput}
                      onChange={(e) => { setQuickInput(e.target.value); setErrorMsg(""); }}
                      onCompositionStart={() => setComposing(true)}
                      onCompositionEnd={() => setComposing(false)}
                      onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !composing && !e.nativeEvent.isComposing && quickInput.trim() && !starting) { e.preventDefault(); handleQuickStart(); } }}
                      placeholder={"描述你想完成什么，例如：\n帮我做一个个人博客\n给现有项目补一个支付流程\n先把这个产品方案敲定下来"}
                      rows={4}
                      className="w-full resize-none bg-transparent text-sm text-slate-700 dark:text-slate-200 placeholder:text-slate-400/70 dark:placeholder:text-slate-500/70 outline-none leading-relaxed"
                    />
                  </div>
                  <div className="flex items-center justify-between px-4 pb-3">
                    <span className="text-[11px] text-slate-400 dark:text-slate-500">Shift + Enter 换行</span>
                    <button
                      onClick={handleQuickStart} disabled={!quickInput.trim() || starting}
                      className="flex items-center gap-2 rounded-xl bg-gradient-to-r from-indigo-600 to-violet-600 px-5 py-2.5 text-sm font-medium text-white shadow-md shadow-indigo-500/25 transition-all disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer active:scale-[0.97] hover:from-indigo-700 hover:to-violet-700"
                    >
                      {starting ? <Loader2 size={16} className="animate-spin" /> : <Zap size={16} />}
                      {starting ? "处理中" : "开始一个任务"}
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
            <>
              <section className="mb-6 space-y-3">
                <div className="flex items-center justify-between px-1">
                  <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-700 dark:text-slate-200">
                    <div className="flex size-5 items-center justify-center rounded-md bg-violet-100 dark:bg-violet-500/15">
                      <Sparkles size={11} className="text-violet-600 dark:text-violet-400" />
                    </div>
                    最近任务
                  </h2>
                  <Link href="/workspaces" className="flex items-center gap-0.5 text-xs text-slate-400 dark:text-slate-500 hover:text-violet-500 dark:hover:text-violet-400 transition-colors duration-200 cursor-pointer">
                    查看全部 <ChevronRight size={12} />
                  </Link>
                </div>
                {workspaces.length === 0 ? (
                  <div className="rounded-xl bg-white/70 dark:bg-slate-900/70 backdrop-blur-sm ring-1 ring-slate-200/60 dark:ring-slate-700/40 px-4 py-4 text-sm text-slate-400 dark:text-slate-500">
                    在上方输入目标后，会自动创建第一个 Workspace。
                  </div>
                ) : (
                  <div className="grid gap-3 md:grid-cols-3">
                    {workspaces.slice(0, 3).map((workspace) => {
                      return (
                        <Link
                          key={workspace.id}
                          href={`/workspaces/${workspace.id}`}
                          className="rounded-xl bg-white/70 dark:bg-slate-900/70 backdrop-blur-sm ring-1 ring-slate-200/60 dark:ring-slate-700/40 px-4 py-4 transition-all hover:ring-violet-300 dark:hover:ring-violet-500/40"
                        >
                          <div className="mb-1 truncate text-sm font-semibold text-slate-800 dark:text-slate-100">{workspace.name}</div>
                          <div className="line-clamp-2 min-h-[34px] text-xs leading-5 text-slate-500 dark:text-slate-400">
                            {workspace.description || "等待补充任务目标"}
                          </div>
                          <div className="mt-3 flex items-center justify-between text-[11px] text-slate-400 dark:text-slate-500">
                            <span>{workspace.target_platform}</span>
                            <span>{workspaceProgress(workspace)}% 已推进</span>
                          </div>
                          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                            <div className="h-full rounded-full bg-violet-500" style={{ width: `${workspaceProgress(workspace)}%` }} />
                          </div>
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
    </div>
  );
}
