"use client";

import { useEffect, useState, useRef, useCallback } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import {
  ArrowLeft, Sun, Moon, CheckCircle, AlertTriangle,
  Loader2, Download, Globe, Star, Sparkles, Zap, MessageCircle,
  ChevronRight, Users, Play, StopCircle,
} from "lucide-react";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { ChatInputBar } from "@/components/chat-input-bar";

// ===== Types =====
type RoundtableSession = {
  id: string;
  user_id: string;
  topic: string;
  status: "active" | "completed" | "converted";
  max_rounds: number;
  current_round: number;
  summary: string | null;
  created_at: string | null;
  updated_at: string | null;
};

type RoundtableMessage = {
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
        {expanded ? "收起" : `展开全部 (${Math.ceil(content.length / 100) / 10}K 字)`}
      </button>
    </div>
  );
}

const AGENT_COLORS: Record<string, { bubble: string; name: string }> = {
  leader: { bubble: "bubble-p0", name: "pname-0" },
  analyst: { bubble: "bubble-p1", name: "pname-1" },
  researcher: { bubble: "bubble-p1", name: "pname-1" },
  architect: { bubble: "bubble-p2", name: "pname-2" },
  planner: { bubble: "bubble-p2", name: "pname-2" },
  developer: { bubble: "bubble-p3", name: "pname-3" },
  reviewer: { bubble: "bubble-p4", name: "pname-4" },
  tester: { bubble: "bubble-p4", name: "pname-4" },
  user: { bubble: "bubble-human", name: "pname-human" },
  system: { bubble: "", name: "" },
  moderator: { bubble: "bubble-p5", name: "pname-5" },
};

const avatarUrl = (seed: string) =>
  `https://api.dicebear.com/7.x/bottts/svg?seed=${encodeURIComponent(seed)}`;

const SESSION_STATUS_MAP: Record<string, { label: string; color: string; bg: string; icon: React.ReactNode }> = {
  active: { label: "讨论中", color: "var(--accent)", bg: "var(--accent-soft)", icon: <Loader2 size={10} className="animate-spin" /> },
  completed: { label: "已结束", color: "var(--success)", bg: "var(--success-soft)", icon: <CheckCircle size={10} /> },
  converted: { label: "已转 Planning", color: "var(--warning)", bg: "var(--warning-soft)", icon: <Zap size={10} /> },
};

export default function RoundtablePage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();
  const presetParticipants = searchParams.get("participants");
  const { confirm, ConfirmDialog } = useConfirm();
  const [session, setSession] = useState<RoundtableSession | null>(null);
  const [messages, setMessages] = useState<RoundtableMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [streamingAgent, setStreamingAgent] = useState<string | null>(null);
  const [streamingContent, setStreamingContent] = useState("");
  const [statusDetail, setStatusDetail] = useState("");

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const streamingBufferRef = useRef<string>("");
  const streamingRafRef = useRef<number | null>(null);

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
    if (saved === "light" || saved === "dark") setTheme(saved as "dark" | "light");
  }, []);

  // Initial data load
  useEffect(() => {
    if (!id) return;
    Promise.all([
      fetch(`/api/roundtable-sessions/${id}`).then((r) => r.json()),
      fetch(`/api/roundtable-sessions/${id}/messages`).then((r) => r.json()),
    ]).then(([s, msgs]) => {
      setSession(s);
      setMessages(msgs || []);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, [id]);

  // SSE streaming
  useEffect(() => {
    if (!id) return;
    const backendPort = process.env.NEXT_PUBLIC_BACKEND_PORT || "8200";
    const sseUrl = `http://localhost:${backendPort}/api/roundtable-sessions/${id}/stream?token=${encodeURIComponent(localStorage.getItem("agent_team_token") || "")}`;
    const es = new EventSource(sseUrl);

    es.onerror = () => {};

    es.addEventListener("message", (e) => {
      try {
        const msg: RoundtableMessage = JSON.parse(e.data);
        setMessages((prev) => {
          if (prev.some((m) => m.id === msg.id)) return prev;
          return [...prev, msg];
        });
      } catch {}
    });

    es.addEventListener("status", (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data.detail) setStatusDetail(data.detail);
      } catch {}
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

    es.addEventListener("stream", (e) => {
      try {
        const data = JSON.parse(e.data);
        if (!data.chunk) return;
        setStreamingAgent(data.display_name || data.agent);
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

      const loadAndClear = (retry = false) => {
        fetch(`/api/roundtable-sessions/${id}/messages`)
          .then((r) => r.json())
          .then((msgs: RoundtableMessage[]) => {
            if (msgs?.length > 0 || !finalContent || retry) {
              setMessages(msgs || []);
              clearStreaming();
            } else {
              setTimeout(() => loadAndClear(true), 500);
            }
          })
          .catch(() => {
            setTimeout(clearStreaming, 300);
          });
      };
      loadAndClear();
    });

    es.addEventListener("new_round", (e) => {
      try {
        const data = JSON.parse(e.data);
        setSession((prev) => prev ? { ...prev, current_round: data.current_round, max_rounds: data.max_rounds } : prev);
        fetch(`/api/roundtable-sessions/${id}/messages`)
          .then((r) => r.json())
          .then((msgs) => { if (msgs) setMessages(msgs); })
          .catch(() => {});
      } catch {}
    });

    es.addEventListener("roundtable_completed", () => {
      setSession((prev) => prev ? { ...prev, status: "completed" as const } : prev);
      setStreamingAgent(null);
      setStreamingContent("");
      setStatusDetail("");
      streamingBufferRef.current = "";
      fetch(`/api/roundtable-sessions/${id}`)
        .then((r) => r.json())
        .then((s) => { if (s) setSession(s); })
        .catch(() => {});
    });

    es.addEventListener("error", (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        if (data.message) {
          setStreamingAgent(null);
          setStreamingContent("");
          setStatusDetail(data.message);
          setTimeout(() => setStatusDetail(""), 8000);
        }
      } catch {}
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

  // Auto-start discussion when preset participants are provided via URL params
  useEffect(() => {
    if (!id || !presetParticipants || !session || session.status !== "active") return;
    const participants = presetParticipants.split(",").filter(Boolean);
    if (participants.length === 0) return;
    setActionLoading("discuss");
    setStatusDetail("正在启动讨论...");
    fetch(`/api/roundtable-sessions/${id}/auto-discuss`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ participants, rounds: 4 }),
    }).then((res) => {
      if (!res.ok) {
        res.json().then((err) => alert(`启动讨论失败: ${err.detail || "未知错误"}`)).catch(() => alert("启动讨论失败"));
      }
    }).catch(() => alert("无法连接后端服务")).finally(() => {
      setActionLoading(null);
      setStatusDetail("");
      // Clear URL params so it doesn't re-trigger on re-render
      router.replace(`/roundtable/${id}`);
    });
    // Only run once when session loads with preset participants
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, presetParticipants, session?.status]);

  const clearStreaming = useCallback(() => {
    setStreamingAgent(null);
    setStreamingContent("");
    streamingBufferRef.current = "";
  }, []);

  const toggleTheme = useCallback(() => {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    localStorage.setItem("theme", next);
    document.documentElement.setAttribute("data-theme", next);
  }, [theme]);

  const reloadSession = useCallback(async () => {
    if (!id) return;
    try {
      const s = await fetch(`/api/roundtable-sessions/${id}`).then((r) => r.json());
      setSession(s);
    } catch {}
  }, [id]);

  const reloadMessages = useCallback(async () => {
    if (!id) return;
    try {
      const msgs = await fetch(`/api/roundtable-sessions/${id}/messages`).then((r) => r.json());
      setMessages(msgs || []);
    } catch {}
  }, [id]);

  const sendMessage = useCallback(async (text: string, files: File[], _model: string | null) => {
    if (!text.trim() || sending) return;
    setSending(true);
    setStatusDetail("");
    try {
      await fetch(`/api/roundtable-sessions/${id}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content: text.trim(),
          sender: "user",
          message_type: "chat",
          category: null,
        }),
      });
      // SSE will deliver the message, no manual reload needed
    } catch {}
    finally {
      setSending(false);
    }
  }, [id, sending]);

  // Start auto-discussion (main action for roundtable)
  const handleAutoDiscuss = useCallback(async () => {
    setActionLoading("discuss");
    setStatusDetail("正在启动讨论...");
    try {
      const res = await fetch(`/api/roundtable-sessions/${id}/auto-discuss`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          participants: ["architect", "developer", "reviewer"],
          rounds: session?.max_rounds || 3,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        alert(`启动讨论失败: ${err.detail || "未知错误"}`);
      }
    } catch {
      alert("无法连接后端服务");
    }
    finally {
      setActionLoading(null);
      setStatusDetail("");
    }
  }, [id, session?.max_rounds]);

  const handleInterrupt = useCallback(async () => {
    if (!await confirm({ description: "确认中断当前讨论？所有 Agent 的回复将被终止。", variant: "destructive" })) return;
    try {
      await fetch(`/api/roundtable-sessions/${id}/interrupt`, { method: "POST" });
      setStreamingAgent(null);
      setStreamingContent("");
      setStatusDetail("");
      streamingBufferRef.current = "";
      if (streamingRafRef.current !== null) {
        clearTimeout(streamingRafRef.current);
        streamingRafRef.current = null;
      }
      // Reload session state
      const s = await fetch(`/api/roundtable-sessions/${id}`).then((r) => r.json());
      if (s) setSession(s);
    } catch {}
  }, [id]);

  const handleNextRound = useCallback(async () => {
    if (!await confirm({ description: "确认开始下一轮讨论？" })) return;
    setActionLoading("round");
    try {
      await fetch(`/api/roundtable-sessions/${id}/round`, { method: "POST" });
      await reloadSession();
      // Messages will come via SSE
    } catch {}
    finally { setActionLoading(null); }
  }, [id, reloadSession]);

  const handleComplete = useCallback(async () => {
    if (!await confirm({ description: "确认结束本次讨论？结束后将无法继续发言。" })) return;
    setActionLoading("complete");
    try {
      await fetch(`/api/roundtable-sessions/${id}/complete`, { method: "POST" });
      await reloadSession();
    } catch {}
    finally { setActionLoading(null); }
  }, [id, reloadSession]);

  const handlePromote = useCallback(async () => {
    if (!await confirm({ title: "结束讨论并回到首页", description: "确认结束本次讨论？结束后你可以直接回首页输入需求，进入正式流程。" })) return;
    setActionLoading("promote");
    try {
      await fetch(`/api/roundtable-sessions/${id}/complete`, { method: "POST" });
      await reloadSession();
      alert("讨论已结束。请回首页继续输入需求并进入流程。");
      router.push("/");
    } catch {}
    finally { setActionLoading(null); }
  }, [id, reloadSession, router]);

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
        <p className="text-[var(--muted)]">圆桌不存在</p>
      </div>
    );
  }

  const st = SESSION_STATUS_MAP[session.status] || { label: session.status, color: "var(--muted)", bg: "var(--accent-soft)", icon: null };
  const agentConfig = (sender: string) => AGENT_COLORS[sender] || AGENT_COLORS.system;
  const isActive = session.status === "active";
  const hasNoMessages = messages.length === 0 && !streamingContent;

  let currentMsgRound = 1;
  const messageElements: React.ReactNode[] = [];

  messages.forEach((msg) => {
    if (msg.message_type === "round_start" || (msg.sender === "system" && msg.content.includes("Round") && msg.content.includes("started"))) {
      const match = msg.content.match(/Round\s*(\d+)/i);
      if (match) currentMsgRound = parseInt(match[1]);
      messageElements.push(
        <div key={`round-${msg.id}`} className="flex items-center gap-3 py-3">
          <div className="flex-1 h-px bg-[var(--card-border)]" />
          <span className="text-[11px] font-semibold text-[var(--accent)] whitespace-nowrap flex items-center gap-1.5">
            <Sparkles size={10} />
            第 {currentMsgRound} 轮开始
          </span>
          <div className="flex-1 h-px bg-[var(--card-border)]" />
        </div>
      );
      return;
    }

    const ac = agentConfig(msg.sender);
    const isUser = msg.sender === "user";
    const isSystem = msg.sender === "system";

    if (isSystem && msg.message_type !== "round_start") {
      messageElements.push(
        <div key={msg.id} className="flex items-center gap-3 py-1">
          <div className="flex-1 h-px bg-[var(--card-border)]" />
          <span className="text-[11px] text-[var(--muted)] whitespace-nowrap">{msg.content}</span>
          <div className="flex-1 h-px bg-[var(--card-border)]" />
        </div>
      );
      return;
    }

    messageElements.push(
      <div key={msg.id} className={`flex gap-3 items-start msg-appear ${isUser ? "flex-row-reverse" : ""}`}>
        <span className="flex size-9 shrink-0 items-center justify-center rounded-lg overflow-hidden">
          <img src={avatarUrl(msg.sender)} alt={msg.sender_display || msg.sender} className="size-9 rounded-lg" />
        </span>
        <div className={isUser ? "text-right" : ""}>
          <div className={`mb-1.5 ${isUser ? "text-right" : ""}`}>
            <span className={`inline-flex items-center gap-1 text-xs font-semibold px-2.5 py-1 rounded-lg ${ac.name}`} style={{ background: "var(--accent-soft)" }}>
              {msg.sender_display || msg.sender}
            </span>
            {msg.category && (
              <span className="ml-1.5 text-[10px] text-[var(--muted)]">#{msg.category}</span>
            )}
          </div>
          <div className={`chat-bubble ${ac.bubble} text-sm leading-relaxed`}>
            <CollapsibleContent content={msg.content} />
          </div>
        </div>
      </div>
    );
  });

  return (
    <div className="flex h-screen">
      {/* Main chat area */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-[var(--card-border)] bg-[var(--card)]/50 backdrop-blur-sm px-5 py-3 shrink-0">
          <div className="flex items-center gap-3 min-w-0">
            <Link href="/" className="flex size-8 items-center justify-center rounded-xl text-[var(--muted)] hover:text-[var(--foreground)] hover:bg-[var(--surface-elevated)] cursor-pointer transition-all">
              <ArrowLeft size={16} />
            </Link>
            <div className="min-w-0">
              <h1 className="text-sm font-bold truncate">{session.topic}</h1>
              <div className="flex items-center gap-2 mt-0.5">
                <span className="inline-flex items-center gap-1 text-[11px] font-medium text-[var(--accent)]">
                  <Users size={10} />
                  第 {session.current_round}/{session.max_rounds} 轮
                </span>
                <span className="inline-flex items-center gap-1 text-[11px] font-medium" style={{ color: st.color }}>
                  {st.icon} {st.label}
                </span>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {streamingAgent && isActive && (
              <button onClick={handleInterrupt}
                className="rounded-xl bg-[var(--danger)] px-3.5 py-2 text-xs font-semibold text-white cursor-pointer hover:opacity-90 transition-all flex items-center gap-1.5 animate-pulse">
                <StopCircle size={12} />
                中断
              </button>
            )}
            {isActive && (
              <button onClick={handlePromote}
                className="rounded-xl bg-[var(--warning)] px-3.5 py-2 text-xs font-semibold text-white cursor-pointer hover:opacity-90 transition-all flex items-center gap-1.5">
                <Zap size={12} />
                转为 Planning
              </button>
            )}
            <button onClick={toggleTheme}
              className="flex size-8 cursor-pointer items-center justify-center rounded-xl text-[var(--muted)] transition-all hover:bg-[var(--surface-elevated)] hover:text-[var(--foreground)]">
              {theme === "dark" ? <Sun size={14} /> : <Moon size={14} />}
            </button>
          </div>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-5 py-5 space-y-5">
          {/* Empty state with start button */}
          {hasNoMessages && isActive && !streamingAgent && (
            <div className="flex flex-col items-center justify-center py-20">
              <div className="flex size-16 items-center justify-center rounded-2xl bg-[var(--accent-soft)] text-[var(--accent)] mb-5">
                <Globe size={28} />
              </div>
              <h2 className="text-lg font-semibold mb-2">开始圆桌讨论</h2>
              <p className="text-sm text-[var(--muted)] mb-6 max-w-md text-center">
                Agent 团队将围绕主题自动展开多轮讨论，每位 Agent 从自己的专业角度发表观点
              </p>
              <button
                onClick={handleAutoDiscuss}
                disabled={actionLoading !== null}
                className="rounded-xl bg-[var(--accent)] px-5 py-3 text-sm font-semibold text-white cursor-pointer hover:bg-[var(--accent-hover)] disabled:opacity-30 transition-all shadow-md shadow-indigo-500/20 flex items-center gap-2"
              >
                {actionLoading === "discuss" ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} fill="currentColor" />}
                {actionLoading === "discuss" ? "正在启动..." : "开始讨论"}
              </button>
            </div>
          )}

          {/* Existing messages */}
          {messageElements}

          {/* Error status detail */}
          {statusDetail && !streamingAgent && (
            <div className="flex items-center gap-2 rounded-xl border border-red-200 dark:border-red-900/30 bg-red-50/80 dark:bg-red-500/10 backdrop-blur-sm px-4 py-3 text-xs text-red-600 dark:text-red-400 msg-appear">
              <AlertTriangle size={14} />
              <span>{statusDetail}</span>
            </div>
          )}

          {/* Streaming content */}
          {streamingContent && streamingAgent && (
            <div className="flex gap-3 items-start msg-appear">
              <span className="flex size-9 shrink-0 items-center justify-center rounded-lg overflow-hidden">
                <img src={avatarUrl(streamingAgent.toLowerCase())} alt={streamingAgent} className="size-9 rounded-lg" />
              </span>
              <div className="min-w-0 flex-1">
                <div className="mb-1.5">
                  <span className="inline-flex items-center gap-1 text-xs font-semibold px-2.5 py-1 rounded-lg pname-0" style={{ background: "var(--accent-soft)" }}>
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
            <div className="flex gap-3 items-center msg-appear">
              <span className="flex size-9 shrink-0 items-center justify-center rounded-lg overflow-hidden">
                <img src={avatarUrl(streamingAgent.toLowerCase())} alt={streamingAgent} className="size-9 rounded-lg" />
              </span>
              <div className="flex items-center gap-2">
                <span className={`inline-flex items-center gap-1 text-xs font-semibold px-2.5 py-1 rounded-lg ${agentConfig(streamingAgent.toLowerCase()).name}`} style={{ background: "var(--accent-soft)" }}>
                  {streamingAgent}
                </span>
                <span className="inline-flex items-center gap-1.5 text-xs text-[var(--muted)]">
                  <Loader2 size={12} className="animate-spin text-[var(--accent)]" />
                  {statusDetail || "正在思考..."}
                </span>
                {statusDetail?.includes("模型正在") && (
                  <span className="text-[11px] text-[var(--muted)]/70">显示进度摘要，不展示完整思考链路</span>
                )}
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* Input bar */}
        <ChatInputBar
          placeholder={isActive ? "输入你的观点..." : "讨论已结束"}
          disabled={!isActive}
          sending={sending}
          onSend={sendMessage}
        />
      </div>

      {/* Right sidebar - Roundtable Info */}
      <div className="w-80 shrink-0 border-l border-[var(--card-border)] bg-[var(--card)] p-5 overflow-y-auto hidden lg:block">
        {/* Session info card */}
        <div className="rounded-2xl border border-[var(--card-border)] bg-[var(--surface-elevated)]/30 p-5 mb-4">
          <h2 className="text-sm font-bold mb-4 flex items-center gap-2">
            <Globe size={14} className="text-[var(--accent)]" />
            圆桌信息
          </h2>
          <div className="space-y-3">
            <div>
              <span className="text-[10px] text-[var(--muted)] uppercase tracking-wider font-medium">主题</span>
              <p className="text-xs font-semibold mt-1 leading-relaxed">{session.topic}</p>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="rounded-xl bg-[var(--surface-elevated)]/50 p-3">
                <span className="text-[10px] text-[var(--muted)] uppercase tracking-wider font-medium">当前轮次</span>
                <p className="text-lg font-bold mt-1 text-[var(--accent)]">{session.current_round}</p>
              </div>
              <div className="rounded-xl bg-[var(--surface-elevated)]/50 p-3">
                <span className="text-[10px] text-[var(--muted)] uppercase tracking-wider font-medium">最大轮次</span>
                <p className="text-lg font-bold mt-1">{session.max_rounds}</p>
              </div>
            </div>
            <div>
              <span className="text-[10px] text-[var(--muted)] uppercase tracking-wider font-medium">状态</span>
              <p className="mt-1">
                <span className="inline-flex items-center gap-1 rounded-lg px-2.5 py-1 text-xs font-medium" style={{ background: st.bg, color: st.color }}>
                  {st.icon} {st.label}
                </span>
              </p>
            </div>
            {session.summary && (
              <div>
                <span className="text-[10px] text-[var(--muted)] uppercase tracking-wider font-medium">摘要</span>
                <p className="text-xs mt-1 leading-relaxed text-[var(--foreground-secondary)]">{session.summary}</p>
              </div>
            )}
            {session.created_at && (
              <div>
                <span className="text-[10px] text-[var(--muted)] uppercase tracking-wider font-medium">创建时间</span>
                <p className="text-xs mt-1 text-[var(--muted)]">
                  {new Date(session.created_at).toLocaleString("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                </p>
              </div>
            )}
          </div>
        </div>

        {/* Round progress */}
        <div className="rounded-2xl border border-[var(--card-border)] bg-[var(--surface-elevated)]/30 p-5 mb-4">
          <h2 className="text-sm font-bold mb-3 flex items-center gap-2">
            <Sparkles size={14} className="text-[var(--accent)]" />
            讨论进度
          </h2>
          <div className="w-full bg-[var(--surface-elevated)] rounded-full h-2.5 mb-3 overflow-hidden">
            <div
              className="bg-gradient-to-r from-[var(--accent)] to-violet-500 h-2.5 rounded-full transition-all duration-500"
              style={{ width: `${Math.min((session.current_round / session.max_rounds) * 100, 100)}%` }}
            />
          </div>
          <p className="text-[11px] text-[var(--muted)]">
            {session.current_round >= session.max_rounds ? "已达最大轮次" : `还剩 ${session.max_rounds - session.current_round} 轮`}
          </p>
        </div>

        {/* Action buttons */}
        <div className="space-y-2">
          {isActive && hasNoMessages && (
            <button
              onClick={handleAutoDiscuss}
              disabled={actionLoading !== null}
              className="w-full rounded-xl bg-[var(--accent)] px-4 py-3 text-xs font-semibold text-white cursor-pointer hover:bg-[var(--accent-hover)] disabled:opacity-30 transition-all shadow-md shadow-indigo-500/20 flex items-center justify-center gap-2"
            >
              {actionLoading === "discuss" ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} fill="currentColor" />}
              {actionLoading === "discuss" ? "启动中..." : "开始讨论"}
            </button>
          )}
          {isActive && (
            <button
              onClick={handleNextRound}
              disabled={actionLoading !== null || session.current_round >= session.max_rounds}
              className="w-full rounded-xl bg-[var(--accent)] px-4 py-3 text-xs font-semibold text-white cursor-pointer hover:bg-[var(--accent-hover)] disabled:opacity-30 transition-all shadow-md shadow-indigo-500/20 flex items-center justify-center gap-2"
            >
              {actionLoading === "round" ? <Loader2 size={14} className="animate-spin" /> : <ChevronRight size={14} />}
              {actionLoading === "round" ? "处理中..." : "开始下一轮"}
            </button>
          )}
          {isActive && (
            <button
              onClick={handleComplete}
              disabled={actionLoading !== null}
              className="w-full rounded-xl border border-[var(--card-border)] px-4 py-3 text-xs font-semibold text-[var(--foreground)] cursor-pointer hover:bg-[var(--surface-elevated)] disabled:opacity-30 transition-all flex items-center justify-center gap-2"
            >
              {actionLoading === "complete" ? <Loader2 size={14} className="animate-spin" /> : <CheckCircle size={14} />}
              {actionLoading === "complete" ? "处理中..." : "完成讨论"}
            </button>
          )}
          {(isActive || session.status === "completed" || session.status === "converted") && (
            <button
              onClick={handlePromote}
              disabled={actionLoading !== null}
              className="w-full rounded-xl bg-[var(--warning)] px-4 py-3 text-xs font-semibold text-white cursor-pointer hover:opacity-90 disabled:opacity-30 transition-all flex items-center justify-center gap-2"
            >
              {actionLoading === "promote" ? <Loader2 size={14} className="animate-spin" /> : <Zap size={14} />}
              {session.status === "converted" ? "重新转为 Planning Session" : actionLoading === "promote" ? "转换中..." : "转为 Planning Session"}
            </button>
          )}
        </div>
      </div>
      {ConfirmDialog}
    </div>
  );
}
