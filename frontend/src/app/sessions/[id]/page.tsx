"use client";

import { useEffect, useState, useRef, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import {
  ArrowLeft, Sun, Moon, Play, CheckCircle, AlertTriangle,
  RotateCcw, Download, FileText, ListTodo, X, Bot,
  Loader2, ChevronDown, ChevronUp, Sparkles, Clock, Zap, StopCircle, PanelRightOpen, PanelRightClose,
  Copy, Check, Terminal, MessageSquare,
} from "lucide-react";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { ChatInputBar } from "@/components/chat-input-bar";
import { useAuth } from "@/lib/auth";

// ===== Types =====
type Message = {
  id: string;
  seq: number;
  sender: string;
  sender_display: string | null;
  receiver: string | null;
  message_type: string;
  category: string | null;
  content: string;
  created_at: string | null;
};

type Task = {
  id: string;
  title: string;
  description: string | null;
  status: string;
  assigned_agent: string | null;
  owner_role: string | null;
  order: number;
  dependencies: number[];
  target_paths: string[];
  validation_commands: string[];
};

type PlanningSession = {
  id: string;
  workspace_id?: string | null;
  title: string;
  status: string;
  mode: string;
  input_text: string;
  summary: string | null;
};

const COLLAPSE_THRESHOLD = 600;

function CollapsibleContent({ content }: { content: string }) {
  const [expanded, setExpanded] = useState(false);
  const isLong = content.length > COLLAPSE_THRESHOLD;

  if (!isLong) {
    return (
      <div className="chat-md">
        <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>{content}</ReactMarkdown>
      </div>
    );
  }

  const displayContent = expanded ? content : content.slice(0, COLLAPSE_THRESHOLD) + "...";

  return (
    <div>
      <div className="chat-md">
        <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>{displayContent}</ReactMarkdown>
      </div>
      <button
        onClick={() => setExpanded(!expanded)}
        className="mt-2 inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[11px] font-medium transition-all duration-200 hover:bg-[var(--accent-soft)]"
        style={{ color: "var(--accent)" }}
      >
        {expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
        {expanded ? "收起" : `展开全部 (${Math.ceil(content.length / 100) / 10}K 字)`}
      </button>
    </div>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }, [text]);
  return (
    <button
      onClick={handleCopy}
      className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-[10px] font-medium text-[var(--muted)] hover:text-[var(--foreground)] hover:bg-[var(--surface-elevated)]/80 cursor-pointer transition-all"
      title="复制"
    >
      {copied ? <Check size={10} className="text-[var(--success)]" /> : <Copy size={10} />}
      {copied ? "已复制" : "复制"}
    </button>
  );
}

const AGENT_COLORS: Record<string, { bubble: string; name: string; bg: string }> = {
  leader: { bubble: "bubble-p0", name: "pname-0", bg: "from-violet-500/20 to-purple-500/10" },
  researcher: { bubble: "bubble-p1", name: "pname-1", bg: "from-blue-500/20 to-indigo-500/10" },
  analyst: { bubble: "bubble-p1", name: "pname-1", bg: "from-blue-500/20 to-indigo-500/10" },
  architect: { bubble: "bubble-p2", name: "pname-2", bg: "from-amber-500/20 to-yellow-500/10" },
  planner: { bubble: "bubble-p2", name: "pname-2", bg: "from-amber-500/20 to-yellow-500/10" },
  developer: { bubble: "bubble-p3", name: "pname-3", bg: "from-red-500/20 to-rose-500/10" },
  reviewer: { bubble: "bubble-p4", name: "pname-4", bg: "from-teal-500/20 to-emerald-500/10" },
  tester: { bubble: "bubble-p4", name: "pname-4", bg: "from-teal-500/20 to-emerald-500/10" },
  user: { bubble: "bubble-human", name: "pname-human", bg: "from-indigo-500/20 to-violet-500/10" },
  system: { bubble: "", name: "", bg: "from-gray-500/20 to-slate-500/10" },
};

const avatarUrl = (seed: string) =>
  `https://api.dicebear.com/7.x/bottts/svg?seed=${encodeURIComponent(seed)}`;

const STATUS_MAP: Record<string, { label: string; color: string; bg: string; icon: React.ReactNode }> = {
  created: { label: "待开始", color: "var(--muted)", bg: "var(--accent-soft)", icon: <Clock size={10} /> },
  planning: { label: "规划中", color: "var(--accent)", bg: "var(--accent-soft)", icon: <Loader2 size={10} className="animate-spin" /> },
  analyzing: { label: "分析中", color: "var(--accent)", bg: "var(--accent-soft)", icon: <Loader2 size={10} className="animate-spin" /> },
  thinking: { label: "思考中", color: "var(--accent)", bg: "var(--accent-soft)", icon: <Loader2 size={10} className="animate-spin" /> },
  generating: { label: "输出中", color: "var(--accent)", bg: "var(--accent-soft)", icon: <Loader2 size={10} className="animate-spin" /> },
  researching: { label: "调研中", color: "var(--accent)", bg: "var(--accent-soft)", icon: <Loader2 size={10} className="animate-spin" /> },
  generating_proposal: { label: "生成方案", color: "var(--accent)", bg: "var(--accent-soft)", icon: <Sparkles size={10} /> },
  reviewing: { label: "审查中", color: "var(--accent)", bg: "var(--accent-soft)", icon: <Loader2 size={10} className="animate-spin" /> },
  awaiting_approval: { label: "待审批", color: "var(--warning)", bg: "var(--warning-soft)", icon: <Sparkles size={10} /> },
  generating_plan: { label: "生成计划", color: "var(--accent)", bg: "var(--accent-soft)", icon: <Sparkles size={10} /> },
  ready_for_export: { label: "可导出", color: "var(--success)", bg: "var(--success-soft)", icon: <Zap size={10} /> },
  completed: { label: "已完成", color: "var(--success)", bg: "var(--success-soft)", icon: <CheckCircle size={10} /> },
  cancelled: { label: "已取消", color: "var(--muted)", bg: "var(--accent-soft)", icon: <X size={10} /> },
  failed: { label: "失败", color: "var(--danger)", bg: "var(--danger-soft)", icon: <AlertTriangle size={10} /> },
};

const TASK_STATUS_COLORS: Record<string, string> = {
  pending: "var(--muted)",
  assigned: "var(--accent)",
  in_progress: "var(--accent)",
  completed: "var(--success)",
  failed: "var(--danger)",
  skipped: "var(--muted)",
};

const TASK_STATUS_BG: Record<string, string> = {
  pending: "var(--accent-soft)",
  assigned: "var(--accent-soft)",
  in_progress: "var(--accent-soft)",
  completed: "var(--success-soft)",
  failed: "var(--danger-soft)",
  skipped: "var(--accent-soft)",
};

export default function SessionPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { confirm, ConfirmDialog } = useConfirm();
  const { token: authToken } = useAuth();
  const [session, setSession] = useState<PlanningSession | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [streamingAgent, setStreamingAgent] = useState<string | null>(null);
  const [streamingContent, setStreamingContent] = useState("");
  const [streamingMsgId, setStreamingMsgId] = useState<string | null>(null);
  const [rightTab, setRightTab] = useState<"proposal" | "plan">("proposal");
  const [showRightPanel, setShowRightPanel] = useState(true);
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [statusDetail, setStatusDetail] = useState<string>("");

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const streamingBufferRef = useRef<string>("");
  const streamingRafRef = useRef<number | null>(null);
  const streamingMsgIdRef = useRef<string | null>(null);

  const flushStreamingBuffer = useCallback(() => {
    setStreamingContent(streamingBufferRef.current);
    streamingRafRef.current = null;
  }, []);

  const scheduleStreamingFlush = useCallback(() => {
    if (streamingRafRef.current === null) {
      streamingRafRef.current = window.setTimeout(flushStreamingBuffer, 50);
    }
  }, [flushStreamingBuffer]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamingContent]);

  useEffect(() => {
    const saved = localStorage.getItem("theme");
    const t = (saved === "light" || saved === "dark") ? saved : "dark";
    setTheme(t as "dark" | "light");
    document.documentElement.classList.toggle("dark", t === "dark");
  }, []);

  useEffect(() => {
    if (!id) return;
    Promise.all([
      fetch(`/api/planning-sessions/${id}`).then((r) => r.json()),
      fetch(`/api/planning-sessions/${id}/messages?limit=200`).then((r) => r.json()),
      fetch(`/api/planning-sessions/${id}/tasks`).then((r) => r.json()).catch(() => []),
    ]).then(([s, msgs, tks]) => {
      setSession(s);
      setMessages(msgs || []);
      setTasks(tks || []);
      setLoading(false);
      const activeStatuses = ["created", "planning", "analyzing", "generating_proposal", "generating_plan"];
      if (!activeStatuses.includes(s.status)) {
        setStreamingAgent(null);
        setStreamingContent("");
        setStreamingMsgId(null);
        setStatusDetail("");
        streamingBufferRef.current = "";
        if (streamingRafRef.current !== null) {
          clearTimeout(streamingRafRef.current);
          streamingRafRef.current = null;
        }
      }
    }).catch(() => setLoading(false));
  }, [id]);

  useEffect(() => {
    if (!id) return;
    const backendPort = process.env.NEXT_PUBLIC_BACKEND_PORT || "8200";
    const sseUrl = `http://localhost:${backendPort}/api/planning-sessions/${id}/stream?token=${encodeURIComponent(localStorage.getItem("agent_team_token") || "")}`;
    const es = new EventSource(sseUrl);

    es.onopen = () => {};
    es.onerror = () => {};

    es.addEventListener("message", (e) => {
      try {
        const msg: Message = JSON.parse(e.data);
        setMessages((prev) => {
          if (prev.some((m) => m.id === msg.id)) return prev;
          return [...prev, msg];
        });
      } catch {}
    });

    es.addEventListener("status", (e) => {
      try {
        const data = JSON.parse(e.data);
        setSession((prev) => prev ? { ...prev, status: data.status } : prev);
        if (data.detail) setStatusDetail(data.detail);
        const terminalStatuses = ["awaiting_approval", "completed", "failed", "cancelled"];
        if (terminalStatuses.includes(data.status)) {
          setStreamingAgent(null);
          setStreamingContent("");
          setStreamingMsgId(null);
          setStatusDetail("");
          streamingBufferRef.current = "";
          if (streamingRafRef.current !== null) {
            clearTimeout(streamingRafRef.current);
            streamingRafRef.current = null;
          }
        }
      } catch {}
    });

    es.addEventListener("stream", (e) => {
      try {
        const data = JSON.parse(e.data);
        if (!data.chunk) return;
        setStreamingAgent(data.display_name || data.agent);
        setStreamingMsgId(data.message_id || null);
        streamingMsgIdRef.current = data.message_id || null;
        streamingBufferRef.current += data.chunk;
        scheduleStreamingFlush();
      } catch {}
    });

    es.addEventListener("stream_end", () => {
      if (streamingRafRef.current !== null) {
        clearTimeout(streamingRafRef.current);
        streamingRafRef.current = null;
      }
      const finalContent = streamingBufferRef.current;
      setStreamingContent(finalContent);

      const loadMessagesAndClear = (retry = false) => {
        fetch(`/api/planning-sessions/${id}/messages?limit=200`)
          .then((r) => r.json())
          .then((msgs: Message[]) => {
            const hasStreamedMsg = msgs?.some((m) => m.id === streamingMsgIdRef.current);
            if (hasStreamedMsg || !finalContent || retry) {
              setMessages(msgs || []);
              setStreamingAgent(null);
              setStreamingContent("");
              setStreamingMsgId(null);
              streamingMsgIdRef.current = null;
              setStatusDetail("");
              streamingBufferRef.current = "";
            } else {
              setTimeout(() => loadMessagesAndClear(true), 800);
            }
          })
          .catch(() => {
            setTimeout(() => {
              setStreamingAgent(null);
              setStreamingContent("");
              setStreamingMsgId(null);
              streamingMsgIdRef.current = null;
              setStatusDetail("");
              streamingBufferRef.current = "";
            }, 500);
          });
      };
      loadMessagesAndClear();
    });

    es.addEventListener("typing", (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data.is_typing) {
          setStreamingAgent(data.display_name || data.agent);
          // Reset streaming content when a new agent starts typing
          setStreamingContent("");
          streamingBufferRef.current = "";
        }
      } catch {}
    });

    es.addEventListener("tasks", (e) => {
      try { setTasks(JSON.parse(e.data)); } catch {}
    });

    eventSourceRef.current = es;
    return () => {
      es.close();
      if (streamingRafRef.current !== null) {
        clearTimeout(streamingRafRef.current);
        streamingRafRef.current = null;
      }
    };
  }, [id, scheduleStreamingFlush]);

  const toggleTheme = useCallback(() => {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    localStorage.setItem("theme", next);
    document.documentElement.classList.toggle("dark", next === "dark");
  }, [theme]);

  const sendMessage = useCallback(async (text: string, files: File[], _model: string | null) => {
    if ((!text.trim() && files.length === 0) || sending) return;
    setSending(true);
    try {
      let attachmentInfos: string[] = [];
      if (files.length > 0) {
        for (const f of files) {
          try {
            const formData = new FormData();
            formData.append("file", f);
            const uploadResp = await fetch(`/api/planning-sessions/${id}/upload`, {
              method: "POST",
              body: formData,
            });
            if (uploadResp.ok) {
              const result = await uploadResp.json();
              attachmentInfos.push(`📎 ${result.filename} (${(result.size_bytes / 1024).toFixed(1)}KB) [id: ${result.id}]`);
            } else {
              const errData = await uploadResp.json().catch(() => ({}));
              attachmentInfos.push(`📎 ${f.name} (上传失败: ${errData.detail || '未知错误'})`);
            }
          } catch {
            attachmentInfos.push(`📎 ${f.name} (上传失败)`);
          }
        }
      }

      let content = text.trim();
      if (attachmentInfos.length > 0) {
        content = content ? `${content}\n\n${attachmentInfos.join("\n")}` : attachmentInfos.join("\n");
      }

      if (content) {
        await fetch(`/api/planning-sessions/${id}/messages`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content, sender: "user" }),
        });
      }
    } catch (e) {
      console.error("Failed to send message:", e);
    } finally {
      setSending(false);
    }
  }, [id, sending]);

  const handleApprove = useCallback(async () => {
    if (!await confirm({ description: "确认审批此方案？" })) return;
    try { await fetch(`/api/planning-sessions/${id}/approve`, { method: "POST" }); } catch {}
  }, [id, confirm]);

  const handleRevise = useCallback(async () => {
    const feedback = prompt("请输入修改意见，Agent 团队将根据你的反馈修改方案：");
    if (!feedback?.trim()) return;
    try {
      await fetch(`/api/planning-sessions/${id}/revise`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ feedback: feedback.trim() }),
      });
      setSession((prev) => prev ? { ...prev, status: "planning" } : prev);
    } catch {}
  }, [id]);

  const handleStart = useCallback(async () => {
    try { await fetch(`/api/planning-sessions/${id}/start`, { method: "POST" }); } catch {}
  }, [id]);

  const handleRetry = useCallback(async () => {
    try {
      await fetch(`/api/planning-sessions/${id}/retry`, { method: "POST" });
      setSession((prev) => prev ? { ...prev, status: "planning" } : prev);
    } catch {}
  }, [id]);

  const handleInterrupt = useCallback(async () => {
    if (!await confirm({ description: "确认中断当前流程？所有进行中的生成将被终止。", variant: "destructive" })) return;
    try {
      await fetch(`/api/planning-sessions/${id}/interrupt`, { method: "POST" });
      setStreamingAgent(null);
      setStreamingContent("");
      setStreamingMsgId(null);
      setStatusDetail("");
      streamingBufferRef.current = "";
      if (streamingRafRef.current !== null) {
        clearTimeout(streamingRafRef.current);
        streamingRafRef.current = null;
      }
      // Reload session state
      const s = await fetch(`/api/planning-sessions/${id}`).then((r) => r.json());
      if (s) setSession(s);
    } catch {}
  }, [id]);

  const handleExportProposal = useCallback(async () => {
    try {
      const resp = await fetch(`/api/planning-sessions/${id}/proposal`);
      if (!resp.ok) { alert("方案尚不可导出"); return; }
      const text = await resp.text();
      const blob = new Blob([text], { type: "text/markdown" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = "proposal.md"; a.click();
      URL.revokeObjectURL(url);
    } catch {}
  }, [id]);

  const handleExportPlan = useCallback(async () => {
    try {
      const resp = await fetch(`/api/planning-sessions/${id}/execution-plan`);
      if (!resp.ok) { alert("执行计划尚不可导出"); return; }
      const data = await resp.json();
      const text = JSON.stringify(data, null, 2);
      const blob = new Blob([text], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = "execution_plan.json"; a.click();
      URL.revokeObjectURL(url);
    } catch {}
  }, [id]);

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <div className="flex gap-1.5">
          <span className="typing-dot" /><span className="typing-dot" /><span className="typing-dot" />
        </div>
      </div>
    );
  }

  if (!session) {
    return (
      <div className="flex h-screen items-center justify-center">
        <p className="text-[var(--muted)]">会话不存在</p>
      </div>
    );
  }

  const st = STATUS_MAP[session.status] || { label: session.status, color: "var(--muted)", bg: "var(--accent-soft)", icon: null };
  const proposalMsg = messages.filter((m) => m.message_type === "proposal").pop();
  const planMsg = messages.filter((m) => m.message_type === "plan").pop();
  const agentConfig = (sender: string) => AGENT_COLORS[sender] || AGENT_COLORS.system;
  const backHref = session.workspace_id ? `/workspaces/${session.workspace_id}` : "/workspaces";

  return (
    <div className="flex h-screen">
      {/* Left sidebar - Tasks */}
      <div className="w-64 shrink-0 border-r border-[var(--card-border)] bg-[var(--card)] py-4 pl-3 pr-4 overflow-y-auto hidden md:block">
        <div className="mb-5">
          <Link href={backHref} className="inline-flex items-center gap-2 rounded-xl bg-[var(--surface-elevated)] px-3 py-2 text-xs text-[var(--muted)] hover:text-[var(--foreground)] hover:bg-[var(--accent-soft)] cursor-pointer transition-all duration-200">
            <ArrowLeft size={14} />
            {session.workspace_id ? "返回 Workspace" : "返回工作区"}
          </Link>
        </div>

        <div className="mb-4 flex items-center gap-2">
          <ListTodo size={14} className="text-[var(--accent)]" />
          <h2 className="text-sm font-bold">任务列表</h2>
        </div>
        {tasks.length === 0 ? (
          <div className="rounded-xl border border-dashed border-[var(--card-border)] p-4 text-center">
            <p className="text-xs text-[var(--muted)]">审批方案后将生成任务列表</p>
          </div>
        ) : (
          <div className="space-y-2">
            {tasks.map((t) => (
              <div key={t.id} className="rounded-xl border border-[var(--card-border)] bg-[var(--surface-elevated)]/50 p-3 transition-all duration-200 hover:border-[var(--card-border-hover)]">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold">{t.title}</span>
                  <span className="rounded-md px-1.5 py-0.5 text-[10px] font-medium" style={{ color: TASK_STATUS_COLORS[t.status], background: TASK_STATUS_BG[t.status] }}>
                    {t.status}
                  </span>
                </div>
                {t.assigned_agent && (
                  <div className="mt-1.5 flex items-center gap-1 text-[10px] text-[var(--muted)]">
                    <Bot size={10} />
                    {t.assigned_agent}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Main chat area */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-[var(--card-border)] bg-[var(--card)]/50 backdrop-blur-sm px-5 py-3 shrink-0">
          <div className="flex items-center gap-3 min-w-0">
            <Link href={backHref} className="md:hidden flex size-8 items-center justify-center rounded-xl text-[var(--muted)] hover:text-[var(--foreground)] hover:bg-[var(--surface-elevated)] transition-all">
              <ArrowLeft size={16} />
            </Link>
            <div className="min-w-0">
              <h1 className="text-sm font-bold truncate">{session.title}</h1>
              <span className="inline-flex items-center gap-1 text-[11px] font-medium mt-0.5" style={{ color: st.color }}>
                {st.icon} {st.label}
              </span>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={() => setShowRightPanel(v => !v)}
              className="flex size-8 cursor-pointer items-center justify-center rounded-xl text-[var(--muted)] transition-all hover:bg-[var(--surface-elevated)] hover:text-[var(--foreground)]"
              title={showRightPanel ? "隐藏侧栏" : "显示侧栏"}>
              {showRightPanel ? <PanelRightClose size={14} /> : <PanelRightOpen size={14} />}
            </button>
            {session.status === "created" && (
              <button onClick={handleStart}
                className="rounded-xl bg-[var(--accent)] px-3.5 py-2 text-xs font-semibold text-white cursor-pointer hover:bg-[var(--accent-hover)] transition-all shadow-md shadow-indigo-500/20 flex items-center gap-1.5">
                <Play size={12} fill="currentColor" />
                开始分析
              </button>
            )}
            {streamingAgent && (
              <button onClick={handleInterrupt}
                className="rounded-xl bg-[var(--danger)] px-3.5 py-2 text-xs font-semibold text-white cursor-pointer hover:opacity-90 transition-all flex items-center gap-1.5 animate-pulse">
                <StopCircle size={12} />
                中断
              </button>
            )}
            {session.status === "failed" && (
              <button onClick={handleRetry}
                className="rounded-xl bg-[var(--warning)] px-3.5 py-2 text-xs font-semibold text-white cursor-pointer hover:opacity-90 transition-all flex items-center gap-1.5">
                <RotateCcw size={12} />
                重新开始
              </button>
            )}
            {session.status === "awaiting_approval" && (
              <button onClick={handleApprove}
                className="rounded-xl bg-[var(--success)] px-3.5 py-2 text-xs font-semibold text-white cursor-pointer hover:opacity-90 transition-all flex items-center gap-1.5">
                <CheckCircle size={12} />
                审批方案
              </button>
            )}
            {session.status === "awaiting_approval" && (
              <button onClick={handleRevise}
                className="rounded-xl border border-[var(--warning)] px-3.5 py-2 text-xs font-semibold text-[var(--warning)] cursor-pointer hover:bg-[var(--warning-soft)] transition-all flex items-center gap-1.5">
                <RotateCcw size={12} />
                退回修改
              </button>
            )}
            {(session.status === "awaiting_approval" || session.status === "generating_plan" || session.status === "completed") && (
              <button onClick={handleExportProposal}
                className="rounded-xl border border-[var(--card-border)] px-3.5 py-2 text-xs font-semibold text-[var(--foreground)] cursor-pointer hover:bg-[var(--surface-elevated)] transition-all flex items-center gap-1.5"
                title="导出 proposal.md">
                <FileText size={12} />
                导出方案
              </button>
            )}
            {(session.status === "generating_plan" || session.status === "completed") && (
              <button onClick={handleExportPlan}
                className="rounded-xl border border-[var(--card-border)] px-3.5 py-2 text-xs font-semibold text-[var(--foreground)] cursor-pointer hover:bg-[var(--surface-elevated)] transition-all flex items-center gap-1.5"
                title="导出 execution_plan.json">
                <ListTodo size={12} />
                导出计划
              </button>
            )}
            <button onClick={toggleTheme}
              className="flex size-8 cursor-pointer items-center justify-center rounded-xl text-[var(--muted)] transition-all hover:bg-[var(--surface-elevated)] hover:text-[var(--foreground)]">
              {theme === "dark" ? <Sun size={14} /> : <Moon size={14} />}
            </button>
          </div>
        </div>

        <div className="border-b border-[var(--card-border)] bg-sky-50/80 px-5 py-3 text-xs text-sky-700 dark:bg-sky-500/10 dark:text-sky-300">
          <div className="flex flex-wrap items-center gap-2">
            <span>这是旧的 Planning 记录页面，后续会逐步并入 Workspace。</span>
            {session.workspace_id && (
              <Link href={`/workspaces/${session.workspace_id}`} className="font-semibold hover:underline">
                进入对应 Workspace
              </Link>
            )}
          </div>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-6 py-6 space-y-5 bg-[var(--surface-elevated)]/30">
          {messages.map((msg) => {
            const ac = agentConfig(msg.sender);
            const isUser = msg.sender === "user";
            return (
              <div key={msg.id} className={`flex gap-3 items-start msg-appear ${isUser ? "flex-row-reverse" : ""}`}>
                <span className={`flex size-8 shrink-0 items-center justify-center rounded-lg overflow-hidden`}>
                  <img src={avatarUrl(msg.sender)} alt={msg.sender_display || msg.sender} className="size-8 rounded-lg" />
                </span>
                <div className={`min-w-0 ${isUser ? "max-w-[70%]" : "max-w-[80%]"}`}>
                  <div className={`mb-1 flex flex-col ${isUser ? "items-end" : "items-start"}`}>
                    <span className={`inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded-md ${ac.name}`} style={{ background: "var(--accent-soft)" }}>
                      {msg.sender_display || msg.sender}
                    </span>
                    {msg.category && (
                      <span className="text-[10px] text-[var(--muted)] mt-0.5">#{msg.category}</span>
                    )}
                  </div>
                  <div className={`text-sm leading-relaxed ${isUser ? "bubble-user" : `chat-bubble ${ac.bubble}`}`}>
                    <CollapsibleContent content={msg.content} />
                  </div>
                </div>
              </div>
            );
          })}

          {/* Streaming content */}
          {streamingContent && streamingAgent && (
            <div className="flex gap-3 items-start msg-appear">
              <span className="flex size-8 shrink-0 items-center justify-center rounded-lg overflow-hidden">
                <img src={avatarUrl(streamingAgent.toLowerCase())} alt={streamingAgent} className="size-8 rounded-lg" />
              </span>
              <div className="min-w-0 max-w-[75%]">
                <div className="mb-1 flex flex-col items-start">
                  <span className="inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded-md pname-0" style={{ background: "var(--accent-soft)" }}>
                    {streamingAgent}
                  </span>
                </div>
                <div className="chat-bubble bubble-p0 text-sm leading-relaxed streaming-cursor">
                  <div className="chat-md">
                    <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>{streamingContent}</ReactMarkdown>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Typing indicator */}
          {streamingAgent && !streamingContent && (
            <div className="flex gap-3 items-start msg-appear">
              <span className="flex size-8 shrink-0 items-center justify-center rounded-lg overflow-hidden">
                <img src={avatarUrl(streamingAgent.toLowerCase())} alt={streamingAgent} className="size-8 rounded-lg" />
              </span>
              <div>
                <div className="mb-1 flex flex-col items-start">
                  <span className={`inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded-md ${agentConfig(streamingAgent.toLowerCase()).name}`} style={{ background: "var(--accent-soft)" }}>
                    {streamingAgent}
                  </span>
                </div>
                <div className="chat-bubble bubble-p0 text-sm leading-relaxed">
                  <div className="flex items-center gap-2 text-xs text-[var(--muted)]">
                    <Loader2 size={14} className="animate-spin text-[var(--accent)]" />
                    <span>{statusDetail || "正在思考..."}</span>
                  </div>
                  {session.status === "thinking" && (
                    <div className="mt-2 text-[11px] text-[var(--muted)]/80">
                      正在显示进度摘要，不展示模型完整思考链路。
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* Failed state notice */}
          {session.status === "failed" && (
            <div className="flex items-center gap-3 rounded-xl border border-[var(--danger)]/20 bg-[var(--danger-soft)] px-4 py-3 text-sm">
              <AlertTriangle size={16} className="text-[var(--danger)] shrink-0" />
              <span className="text-[var(--danger)]">规划流程失败，请点击右上角「重新开始」按钮重试</span>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* Input bar */}
        <ChatInputBar
          placeholder={
            session.status === "created" ? "输入补充说明后发送，将自动开始分析..."
            : session.status === "awaiting_approval" ? "输入修改意见，或点击「审批方案」/「退回修改」..."
            : session.status === "completed" ? "追问或补充说明..."
            : session.status === "failed" || session.status === "cancelled" ? "当前会话已结束"
            : "随时输入补充说明，Agent 会参考你的输入..."
          }
          disabled={session.status === "failed" || session.status === "cancelled"}
          sending={sending}
          onSend={sendMessage}
        />
      </div>

      {/* Right sidebar - Proposal / Plan */}
      {showRightPanel && (
        <div className="w-[520px] shrink-0 border-l border-[var(--card-border)] bg-[var(--card)] flex flex-col overflow-hidden">
          <div className="p-4 pb-0 shrink-0">
            <div className="flex gap-2 mb-4">
              <button
                onClick={() => setRightTab("proposal")}
                className={`flex-1 text-xs font-semibold px-3 py-2 rounded-xl cursor-pointer transition-all flex items-center justify-center gap-1.5 ${
                  rightTab === "proposal" ? "bg-[var(--accent)] text-white shadow-md shadow-indigo-500/20" : "text-[var(--muted)] hover:bg-[var(--surface-elevated)]"
                }`}
              >
                <FileText size={12} />
                方案
              </button>
              <button
                onClick={() => setRightTab("plan")}
                className={`flex-1 text-xs font-semibold px-3 py-2 rounded-xl cursor-pointer transition-all flex items-center justify-center gap-1.5 ${
                  rightTab === "plan" ? "bg-[var(--accent)] text-white shadow-md shadow-indigo-500/20" : "text-[var(--muted)] hover:bg-[var(--surface-elevated)]"
                }`}
              >
                <ListTodo size={12} />
                执行计划
              </button>
            </div>
          </div>

          <div className="flex-1 overflow-y-auto px-4 pb-4 sidebar-prose">
            {rightTab === "proposal" ? (
              <>
                <div className="flex items-center justify-between mb-3">
                  <h2 className="text-sm font-bold">技术方案</h2>
                  {proposalMsg && (
                    <button onClick={handleExportProposal}
                      className="inline-flex items-center gap-1 text-[10px] px-2.5 py-1.5 rounded-lg border border-[var(--card-border)] text-[var(--muted)] hover:text-[var(--foreground)] hover:bg-[var(--surface-elevated)] cursor-pointer transition-all">
                      <Download size={10} />
                      下载 .md
                    </button>
                  )}
                </div>
                {proposalMsg ? (
                  <div className="rounded-xl border border-[var(--card-border)] bg-[var(--surface-elevated)]/50 p-4 text-xs">
                    <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>{proposalMsg.content}</ReactMarkdown>
                  </div>
                ) : (
                  <div className="rounded-xl border border-dashed border-[var(--card-border)] p-6 text-center">
                    <Sparkles size={20} className="mx-auto mb-2 text-[var(--muted)]" />
                    <p className="text-xs text-[var(--muted)]">方案将在 Agent 团队分析完成后显示在这里</p>
                  </div>
                )}
              </>
            ) : (
              <>
                <div className="flex items-center justify-between mb-3">
                  <h2 className="text-sm font-bold">执行计划</h2>
                  {planMsg && (
                    <button onClick={handleExportPlan}
                      className="inline-flex items-center gap-1 text-[10px] px-2.5 py-1.5 rounded-lg border border-[var(--card-border)] text-[var(--muted)] hover:text-[var(--foreground)] hover:bg-[var(--surface-elevated)] cursor-pointer transition-all">
                      <Download size={10} />
                      下载 .json
                    </button>
                  )}
                </div>
                {planMsg ? (
                  <div className="rounded-xl border border-[var(--card-border)] bg-[var(--surface-elevated)]/50 p-4 text-xs">
                    <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>{planMsg.content}</ReactMarkdown>
                  </div>
                ) : (
                  <div className="rounded-xl border border-dashed border-[var(--card-border)] p-6 text-center">
                    <ListTodo size={20} className="mx-auto mb-2 text-[var(--muted)]" />
                    <p className="text-xs text-[var(--muted)]">审批方案后将生成执行计划</p>
                  </div>
                )}
                {tasks.length > 0 && (
                  <div className="mt-4 space-y-2">
                    <h3 className="text-xs font-bold flex items-center gap-1.5">
                      <ListTodo size={12} className="text-[var(--accent)]" />
                      任务列表
                    </h3>
                    {tasks.map((t, i) => (
                      <div key={t.id} className="rounded-xl border border-[var(--card-border)] bg-[var(--surface-elevated)]/50 p-3 transition-all hover:border-[var(--card-border-hover)]">
                        <div className="flex items-center justify-between">
                          <span className="text-xs font-medium">{i + 1}. {t.title}</span>
                          <span className="rounded-md px-1.5 py-0.5 text-[10px] font-medium" style={{ color: TASK_STATUS_COLORS[t.status], background: TASK_STATUS_BG[t.status] }}>{t.status}</span>
                        </div>
                        {t.description && <p className="mt-1.5 text-[10px] text-[var(--muted)] line-clamp-2">{t.description}</p>}
                        {t.assigned_agent && <span className="mt-1.5 inline-flex items-center gap-1 text-[10px] text-[var(--accent)]"><Bot size={10} />{t.assigned_agent}</span>}
                      </div>
                    ))}
                  </div>
                )}
                {session.status === "completed" && (
                  <div className="mt-4 space-y-3">
                    {/* Header */}
                    <div className="rounded-xl border border-[var(--accent)]/20 bg-[var(--accent-soft)] p-4">
                      <h3 className="text-xs font-bold text-[var(--accent)] mb-1.5 flex items-center gap-1.5">
                        <Terminal size={12} />
                        在本地执行此计划
                      </h3>
                      <p className="text-[10px] text-[var(--muted)] leading-relaxed">
                        执行计划需要通过 CLI 工具在本地项目目录中运行。AI 会根据任务自动生成代码并写入你的项目。
                      </p>
                    </div>

                    {/* Step 1: Install CLI (one-time) */}
                    <div className="rounded-xl border border-[var(--card-border)] bg-[var(--surface-elevated)]/50 p-3.5">
                      <div className="flex items-center gap-2 mb-2">
                        <span className="flex size-5 shrink-0 items-center justify-center rounded-md bg-[var(--accent)] text-[10px] font-bold text-white">1</span>
                        <span className="text-xs font-semibold">安装 CLI 工具（仅需一次）</span>
                      </div>
                      <p className="text-[10px] text-[var(--muted)] mb-2 leading-relaxed">
                        安装后 <code className="font-mono text-[var(--accent)]">agent-team</code> 即为全局可用命令，任何目录下都能直接使用。
                      </p>
                      {/* Official install */}
                      <div className="flex items-center justify-between rounded-lg bg-[var(--code-bg)] p-2">
                        <div className="min-w-0 mr-2">
                          <span className="text-[9px] text-[var(--muted)] block mb-0.5 font-mono"># 正式环境：从 PyPI 安装</span>
                          <code className="text-[10px] text-[var(--code-fg)] font-mono">pip3 install agent-team</code>
                        </div>
                        <CopyButton text="pip3 install agent-team" />
                      </div>
                      {/* Dev install */}
                      <div className="mt-2 rounded-lg border border-dashed border-[var(--card-border)] p-2.5">
                        <p className="text-[10px] font-medium text-[var(--foreground-secondary)] mb-1.5">开发模式（从源码安装）</p>
                        <p className="text-[10px] text-[var(--muted)] mb-1.5 leading-relaxed">
                          如果你是在本地开发 agent-team，需要从源码安装。进入项目根目录后执行：
                        </p>
                        <div className="space-y-1.5">
                          <div className="flex items-center justify-between rounded-lg bg-[var(--code-bg)] p-2">
                            <div className="min-w-0 mr-2">
                              <code className="text-[10px] text-[var(--code-fg)] font-mono">cd /path/to/agent-team && pip3 install -e ./cli</code>
                            </div>
                            <CopyButton text="cd /path/to/agent-team && pip3 install -e ./cli" />
                          </div>
                        </div>
                      </div>
                      <p className="mt-2 text-[9px] text-[var(--muted)] leading-relaxed">
                        安装成功后，运行 <code className="font-mono text-[var(--accent)]">agent-team --help</code> 验证。看到帮助信息说明安装 OK。
                      </p>
                    </div>

                    {/* Step 2: cd to YOUR project + optional init */}
                    <div className="rounded-xl border border-[var(--card-border)] bg-[var(--surface-elevated)]/50 p-3.5">
                      <div className="flex items-center gap-2 mb-2">
                        <span className="flex size-5 shrink-0 items-center justify-center rounded-md bg-[var(--accent)] text-[10px] font-bold text-white">2</span>
                        <span className="text-xs font-semibold">进入你的目标项目目录</span>
                      </div>
                      <p className="text-[10px] text-[var(--muted)] mb-2 leading-relaxed">
                        <span className="font-medium text-[var(--foreground)]">切到你想让 AI 生成代码的目标项目目录</span>（不是 agent-team 本身），后续所有代码都会写入这个目录。
                      </p>
                      <div className="flex items-center justify-between rounded-lg bg-[var(--code-bg)] p-2">
                        <div className="min-w-0 mr-2">
                          <span className="text-[9px] text-[var(--muted)] block mb-0.5 font-mono"># 切到你的项目目录</span>
                          <code className="text-[10px] text-[var(--code-fg)] font-mono">cd /your/project</code>
                        </div>
                        <CopyButton text="cd /your/project" />
                      </div>
                      <div className="mt-2.5 rounded-lg border border-[var(--accent)]/20 bg-[var(--accent-soft)]/30 p-2.5">
                        <p className="text-[10px] font-medium text-[var(--accent)] mb-1 flex items-center gap-1">
                          <Zap size={10} />
                          LLM 配置自动获取
                        </p>
                        <p className="text-[10px] text-[var(--muted)] leading-relaxed">
                          只要步骤 3 的命令带上了 <code className="font-mono text-[var(--accent)]">--token</code>，CLI 会自动从服务器获取你在网页端配置的 LLM API Key，无需本地重复配置。
                        </p>
                      </div>
                    </div>

                    {/* Step 3: Execute */}
                    <div className="rounded-xl border border-[var(--accent)]/20 bg-[var(--accent-soft)]/50 p-3.5">
                      <div className="flex items-center gap-2 mb-2">
                        <span className="flex size-5 shrink-0 items-center justify-center rounded-md bg-[var(--accent)] text-[10px] font-bold text-white">3</span>
                        <span className="text-xs font-semibold">一键执行计划</span>
                      </div>
                      <p className="text-[10px] text-[var(--muted)] mb-2 leading-relaxed">
                        在你的项目目录下运行此命令。带上 Token 后会自动从服务器拉取 LLM 配置，无需手动设置 API Key。
                      </p>
                      {authToken && (
                        <div className="mb-2 rounded-lg border border-[var(--warning)]/20 bg-[var(--warning-soft)]/50 p-2">
                          <p className="text-[10px] text-[var(--muted)] leading-relaxed">
                            <span className="font-medium text-[var(--foreground)]">认证 Token：</span>点击下方命令会自动带上你的 Token。也可手动复制：
                          </p>
                          <div className="mt-1 flex items-center justify-between rounded bg-[var(--code-bg)] px-2 py-1">
                            <code className="text-[9px] text-[var(--code-fg)] font-mono break-all select-all">{authToken}</code>
                            <CopyButton text={authToken} />
                          </div>
                        </div>
                      )}
                      <div className="flex items-center justify-between rounded-lg bg-[var(--code-bg)] p-2.5">
                        <code className="text-[10px] text-[var(--code-fg)] font-mono break-all select-all">
                          agent-team{authToken ? ` --token ${authToken.slice(0, 8)}...` : ""} execute --plan-id plan_{id} --server http://localhost:{process.env.NEXT_PUBLIC_BACKEND_PORT || '8200'}
                        </code>
                        <CopyButton text={`agent-team${authToken ? ` --token ${authToken}` : ""} execute --plan-id plan_${id} --server http://localhost:${process.env.NEXT_PUBLIC_BACKEND_PORT || '8200'}`} />
                      </div>
                    </div>

                    {/* Step 4: After execution */}
                    <div className="rounded-xl border border-[var(--card-border)] bg-[var(--surface-elevated)]/50 p-3.5">
                      <div className="flex items-center gap-2 mb-2">
                        <span className="flex size-5 shrink-0 items-center justify-center rounded-md bg-[var(--success)] text-[10px] font-bold text-white">4</span>
                        <span className="text-xs font-semibold">查看结果</span>
                      </div>
                      <p className="text-[10px] text-[var(--muted)] mb-2 leading-relaxed">
                        执行完成后，结果会保存在项目目录下的 JSON 文件中。你可以把结果推送到服务器以便在 Web 端查看：
                      </p>
                      <div className="flex items-center justify-between rounded-lg bg-[var(--code-bg)] p-2">
                        <code className="text-[10px] text-[var(--code-fg)] font-mono break-all">agent-team push-result --result-file execution_result_xxx.json --server http://localhost:{process.env.NEXT_PUBLIC_BACKEND_PORT || '8200'}</code>
                        <CopyButton text={`agent-team push-result --result-file execution_result_xxx.json --server http://localhost:${process.env.NEXT_PUBLIC_BACKEND_PORT || '8200'}`} />
                      </div>
                    </div>

                    {/* Quick reference */}
                    <div className="rounded-xl border border-dashed border-[var(--card-border)] p-3">
                      <p className="text-[10px] font-medium text-[var(--foreground-secondary)] mb-1.5">其他选项：</p>
                      <div className="space-y-1 text-[10px] text-[var(--muted)]">
                        <p>• <code className="font-mono text-[var(--accent)]">--step-by-step</code> 逐步执行（每个任务前需确认）</p>
                        <p>• <code className="font-mono text-[var(--accent)]">--safe-mode</code> 安全模式（仅执行只读/验证操作）</p>
                        <p>• <code className="font-mono text-[var(--accent)]">--project ./my-app</code> 指定项目目录（默认当前目录）</p>
                      </div>
                    </div>

                    {/* Interactive chat mode */}
                    <div className="rounded-xl border border-[var(--accent)]/20 bg-[var(--accent-soft)]/30 p-3.5">
                      <h4 className="text-xs font-bold text-[var(--accent)] mb-1.5 flex items-center gap-1.5">
                        <MessageSquare size={12} />
                        有 Bug？进聊天模式修
                      </h4>
                      <p className="text-[10px] text-[var(--muted)] mb-2 leading-relaxed">
                        执行完发现 bug？在项目目录下进入交互模式，跟 Agent 直接对话：
                      </p>
                      <div className="flex items-center justify-between rounded-lg bg-[var(--code-bg)] p-2">
                        <code className="text-[10px] text-[var(--code-fg)] font-mono break-all select-all">
                          agent-team{authToken ? ` --token ${authToken.slice(0, 8)}...` : ""} chat
                        </code>
                        <CopyButton text={`agent-team${authToken ? ` --token ${authToken}` : ""} chat`} />
                      </div>
                      <p className="mt-2 text-[10px] text-[var(--muted)] leading-relaxed">
                        进入后直接打字聊天就行，比如「登录按钮点了没反应，帮我修一下」。
                      </p>
                    </div>

                    {/* View execution result button */}
                    <button
                      onClick={() => router.push(`/executions/plan_${id}`)}
                      className="w-full rounded-xl bg-[var(--accent)] px-3 py-2.5 text-xs font-semibold text-white cursor-pointer hover:bg-[var(--accent-hover)] transition-all shadow-md shadow-indigo-500/20 flex items-center justify-center gap-1.5"
                    >
                      <Zap size={12} />
                      查看详细记录
                    </button>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}
      {ConfirmDialog}
    </div>
  );
}
