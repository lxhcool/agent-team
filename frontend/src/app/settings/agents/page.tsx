"use client";

import { useEffect, useState, useCallback } from "react";
import {
  Pencil, Trash2, Plus, Loader2, Shield, Zap, X, Bot, RefreshCw, ImagePlus
} from "lucide-react";
import { TopNav } from "../../components/topnav";
import { Button } from "@/components/ui/button";
import {
  Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog";
import { useConfirm } from "@/components/ui/confirm-dialog";

type SkillRef = { name: string; display_name?: string };

type AgentTemplate = {
  id: string; name: string; display_name: string; role: string;
  goal: string | null; system_prompt: string | null;
  model: string | null; provider: string | null;
  skills: SkillRef[];
  capabilities: { name: string; description?: string }[];
  constraints: string[];
  participation_modes: string[];
  risk_level: string;
  is_builtin: boolean; version: string;
};

type Skill = {
  id: string; name: string; display_name: string;
  description: string | null; version: string;
  source_type: string; recommended_for: string[];
};

type AgentForm = {
  name: string; display_name: string; role: string;
  goal: string; system_prompt: string; model: string; provider: string;
  constraints: string; participation_modes: string[];
  risk_level: string; skills: SkillRef[];
  avatar_seed: string;
};

const ROLE_OPTIONS = [
  { value: "delivery_lead", label: "交付负责人" },
  { value: "analyst", label: "需求分析师" },
  { value: "strategist", label: "范围策略师" },
  { value: "reviewer", label: "审查者" },
  { value: "spec_writer", label: "文档整理者" },
  { value: "custom", label: "自定义" },
];

const RISK_CONFIG: Record<string, { label: string; color: string; bg: string }> = {
  low: { label: "低风险", color: "#16a34a", bg: "rgba(22,163,74,0.06)" },
  medium: { label: "中风险", color: "#d97706", bg: "rgba(217,119,6,0.06)" },
  high: { label: "高风险", color: "#dc2626", bg: "rgba(220,38,38,0.06)" },
};

const MODE_LABELS: Record<string, string> = { planning: "流程专家" };

const emptyForm: AgentForm = {
  name: "", display_name: "", role: "custom", goal: "", system_prompt: "",
  model: "", provider: "", constraints: "", participation_modes: ["planning"],
  risk_level: "low", skills: [], avatar_seed: "",
};

const generateSeed = () => Math.random().toString(36).slice(2, 10);

const avatarUrl = (seed: string) =>
  seed ? `https://api.dicebear.com/7.x/bottts/svg?seed=${encodeURIComponent(seed)}` : "";

const ROLE_COLORS: Record<string, string> = {
  delivery_lead: "#6366f1", strategist: "#8b5cf6", spec_writer: "#3b82f6",
  reviewer: "#f59e0b", analyst: "#10b981", custom: "#64748b",
};

export default function AgentsPage() {
  const { confirm, ConfirmDialog } = useConfirm();
  const [agents, setAgents] = useState<AgentTemplate[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);

  // Dialog state
  const [dialogOpen, setDialogOpen] = useState(false);
  const [dialogMode, setDialogMode] = useState<"create" | "edit">("create");
  const [editingName, setEditingName] = useState<string | null>(null);
  const [form, setForm] = useState<AgentForm>(emptyForm);
  const [saving, setSaving] = useState(false);

  const fetchAgents = useCallback(async () => {
    try { const res = await fetch("/api/settings/agents"); setAgents(await res.json() || []); } catch {} finally { setLoading(false); }
  }, []);
  const fetchSkills = useCallback(async () => {
    try { const res = await fetch("/api/settings/skills"); setSkills(await res.json() || []); } catch {}
  }, []);

  useEffect(() => { fetchAgents(); fetchSkills(); }, [fetchAgents, fetchSkills]);

  const openCreate = () => {
    setForm({ ...emptyForm, avatar_seed: generateSeed() });
    setDialogMode("create");
    setEditingName(null);
    setDialogOpen(true);
  };

  const openEdit = (agent: AgentTemplate) => {
    setForm({
      name: agent.name,
      display_name: agent.display_name,
      role: agent.role,
      goal: agent.goal || "",
      system_prompt: agent.system_prompt || "",
      model: agent.model || "",
      provider: agent.provider || "",
      constraints: agent.constraints.join("\n"),
      participation_modes: agent.participation_modes,
      risk_level: agent.risk_level,
      skills: agent.skills || [],
      avatar_seed: agent.name,
    });
    setDialogMode("edit");
    setEditingName(agent.name);
    setDialogOpen(true);
  };

  const toggleSkill = (skill: Skill) => {
    const exists = form.skills.some((s) => s.name === skill.name);
    setForm({ ...form, skills: exists ? form.skills.filter((s) => s.name !== skill.name) : [...form.skills, { name: skill.name, display_name: skill.display_name }] });
  };

  const toggleMode = (mode: string) => {
    const modes = form.participation_modes.includes(mode) ? form.participation_modes.filter((m) => m !== mode) : [...form.participation_modes, mode];
    setForm({ ...form, participation_modes: modes });
  };

  const handleSave = async () => {
    if (dialogMode === "create" && (!form.name || !form.display_name || !form.system_prompt)) return;
    setSaving(true);
    try {
      if (dialogMode === "create") {
        await fetch("/api/settings/agents", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...form, constraints: form.constraints.split("\n").filter(Boolean), capabilities: [], allowed_tools: [] }),
        });
      } else {
        await fetch(`/api/settings/agents/${editingName}`, {
          method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            display_name: form.display_name, role: form.role, goal: form.goal,
            system_prompt: form.system_prompt, model: form.model || null,
            constraints: form.constraints.split("\n").filter(Boolean),
            participation_modes: form.participation_modes,
            risk_level: form.risk_level, skills: form.skills,
          }),
        });
      }
      setDialogOpen(false);
      await fetchAgents();
    } catch {} finally { setSaving(false); }
  };

  const deleteAgent = async (name: string) => {
    if (!await confirm({ description: `确定删除专家 "${name}"？`, variant: "destructive" })) return;
    try { await fetch(`/api/settings/agents/${name}`, { method: "DELETE" }); await fetchAgents(); } catch {}
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-indigo-50/30 dark:from-slate-950 dark:via-slate-950 dark:to-indigo-950/20">
        <TopNav />
        <main className="flex items-center justify-center py-20"><div className="flex gap-1.5"><span className="typing-dot" /><span className="typing-dot" /><span className="typing-dot" /></div></main>
      </div>
    );
  }

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
                  <Bot size={16} className="text-indigo-600 dark:text-indigo-400" />
                </div>
                专家库
              </h1>
              <p className="mt-1.5 text-sm text-slate-500 dark:text-slate-400">管理流程里可调用的专业角色</p>
            </div>
            <div className="flex items-center gap-3">
              <span className="inline-flex items-center gap-1.5 rounded-full bg-indigo-50 dark:bg-indigo-500/10 px-3 py-1.5 text-xs font-semibold text-indigo-600 dark:text-indigo-400">
                <Bot size={11} />
                {agents.length} 个专家
              </span>
              <Button onClick={openCreate} size="sm" className="gap-1.5 cursor-pointer">
                <Plus size={14} />
                创建专家
              </Button>
            </div>
          </div>

          {/* Expert Grid */}
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {agents.map((agent) => {
              const risk = RISK_CONFIG[agent.risk_level] || RISK_CONFIG.low;
              const roleColor = ROLE_COLORS[agent.role] || "#64748b";
              const roleLabel = ROLE_OPTIONS.find((r) => r.value === agent.role)?.label || agent.role;
              const agentAvatar = avatarUrl(agent.name);
              return (
                <div key={agent.id} className="group relative rounded-xl bg-white/70 dark:bg-slate-900/70 backdrop-blur-sm ring-1 ring-slate-200/60 dark:ring-slate-700/40 transition-all duration-200 hover:ring-indigo-300/80 dark:hover:ring-indigo-500/30 hover:shadow-md hover:shadow-indigo-500/5 flex flex-col">
                  {/* Card body */}
                  <div className="px-4 pt-3.5 pb-2">
                    <div className="flex items-start gap-3">
                      {/* Avatar */}
                      {agentAvatar ? (
                        <img src={agentAvatar} alt={agent.display_name} className="size-9 shrink-0 rounded-lg bg-slate-100 dark:bg-slate-800 object-cover" />
                      ) : (
                        <div className="flex size-9 shrink-0 items-center justify-center rounded-lg transition-colors duration-200" style={{ background: `${roleColor}12`, color: roleColor }}>
                          <Bot size={16} />
                        </div>
                      )}

                      {/* Info */}
                      <div className="min-w-0 flex-1 pr-5">
                        {/* Line 1: Name + Builtin */}
                        <div className="flex items-center gap-1.5">
                          <h3 className="text-[13px] font-semibold text-slate-800 dark:text-slate-100 truncate">{agent.display_name}</h3>
                          {agent.is_builtin && (
                            <span className="shrink-0 rounded bg-slate-100 dark:bg-slate-800 px-1.5 py-0.5 text-[9px] font-semibold text-slate-500 dark:text-slate-400">内置</span>
                          )}
                        </div>

                        {/* Line 2: Role · Risk · Model */}
                        <div className="mt-1 flex items-center gap-1.5 text-[11px] text-slate-500 dark:text-slate-400">
                          <span style={{ color: roleColor }} className="font-medium">{roleLabel}</span>
                          <span className="text-slate-300 dark:text-slate-600">·</span>
                          <span className="flex items-center gap-0.5" style={{ color: risk.color }}>
                            <Shield size={9} />{risk.label}
                          </span>
                          {agent.model && (
                            <>
                              <span className="text-slate-300 dark:text-slate-600">·</span>
                              <span className="truncate">{agent.model}</span>
                            </>
                          )}
                        </div>

                        {/* Line 3: Methods */}
                        <div className="mt-1.5 flex items-center gap-1 flex-wrap">
                          {agent.skills && agent.skills.slice(0, 1).map((s, i) => (
                            <span key={i} className="inline-flex items-center gap-0.5 rounded bg-amber-50 dark:bg-amber-500/10 px-1.5 py-0.5 text-[9px] font-medium text-amber-600 dark:text-amber-400">
                              <Zap size={7} />{s.display_name || s.name}
                            </span>
                          ))}
                          {agent.skills && agent.skills.length > 1 && (
                            <span className="text-[9px] text-slate-400 dark:text-slate-500">+{agent.skills.length - 1}</span>
                          )}
                        </div>

                        {/* Line 4: Goal (1 line) */}
                        {agent.goal && (
                          <p className="mt-1 text-[11px] text-slate-400 dark:text-slate-500 truncate">{agent.goal}</p>
                        )}
                      </div>
                    </div>
                  </div>

                  {/* Card bottom actions */}
                  <div className="mt-auto px-4 py-2 border-t border-slate-100 dark:border-slate-800/60 flex items-center gap-2">
                    <button type="button" onClick={() => openEdit(agent)}
                      className="inline-flex items-center gap-1 rounded-md bg-indigo-50 dark:bg-indigo-500/10 hover:bg-indigo-100 dark:hover:bg-indigo-500/20 px-2 py-1 text-[10px] font-medium text-indigo-600 dark:text-indigo-400 transition-colors duration-200 cursor-pointer">
                      <Pencil size={10} />编辑
                    </button>
                    <button type="button" onClick={() => openEdit(agent)}
                      className="inline-flex items-center gap-1 rounded-md bg-violet-50 dark:bg-violet-500/10 hover:bg-violet-100 dark:hover:bg-violet-500/20 px-2 py-1 text-[10px] font-medium text-violet-600 dark:text-violet-400 transition-colors duration-200 cursor-pointer">
                      <ImagePlus size={10} />生成头像
                    </button>
                    <button type="button" onClick={() => deleteAgent(agent.name)}
                      className={`inline-flex items-center gap-1 rounded-md px-2 py-1 text-[10px] font-medium transition-colors duration-200 cursor-pointer ${
                        agent.is_builtin
                          ? "bg-slate-50 dark:bg-slate-800 text-slate-300 dark:text-slate-600 cursor-not-allowed"
                          : "bg-red-50 dark:bg-red-500/10 hover:bg-red-100 dark:hover:bg-red-500/20 text-red-600 dark:text-red-400"
                      }`}
                      disabled={agent.is_builtin}>
                      <Trash2 size={10} />删除
                    </button>
                  </div>
                </div>
              );
            })}

            {/* Empty state */}
            {agents.length === 0 && (
              <div className="sm:col-span-2 lg:col-span-3 flex items-center gap-3 rounded-xl bg-white/70 dark:bg-slate-900/70 backdrop-blur-sm ring-1 ring-slate-200/60 dark:ring-slate-700/40 px-4 py-3.5">
                <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-slate-100 dark:bg-slate-800 text-slate-300 dark:text-slate-600">
                  <Bot size={15} strokeWidth={1.5} />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="text-[13px] font-medium text-slate-400 dark:text-slate-500">还没有自定义专家</div>
                  <div className="text-[11px] text-slate-300 dark:text-slate-600 mt-1">点击上方按钮创建第一个</div>
                </div>
              </div>
            )}
          </div>
        </div>
      </main>

      {/* Create / Edit Dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="p-0 gap-0 overflow-hidden [&>button]:hidden" style={{ display: 'flex', width: 900, maxWidth: 'calc(100% - 2rem)', height: 600 }} showCloseButton={false}>
          <div className="flex h-full min-h-0">
            {/* Left: Preview Panel */}
            <div className="hidden md:flex w-[360px] shrink-0 flex-col items-center justify-center overflow-y-auto bg-gradient-to-br from-indigo-600 via-indigo-700 to-violet-800 dark:from-indigo-800 dark:via-indigo-900 dark:to-violet-950 text-white p-8">
              <div className="flex flex-col items-center">
                {avatarUrl(form.avatar_seed) ? (
                  <img src={avatarUrl(form.avatar_seed)} alt={form.display_name || "专家"} className="size-20 rounded-2xl bg-white/15 ring-1 ring-white/20 mb-5 object-cover" />
                ) : (
                  <div className="flex size-20 items-center justify-center rounded-2xl bg-white/15 backdrop-blur-sm mb-5 ring-1 ring-white/20">
                    <Bot size={36} className="text-white" />
                  </div>
                )}
                <h3 className="text-lg font-semibold text-center mb-2">
                  {form.display_name || "新专家"}
                </h3>
                <p className="text-sm text-indigo-200 text-center leading-relaxed max-w-[240px]">
                  {form.goal || "配置专家的角色与方法，让它参与流程里的专业判断"}
                </p>

                {form.skills.length > 0 && (
                  <div className="mt-6 flex flex-wrap justify-center gap-1.5">
                    {form.skills.map((s, i) => (
                      <span key={i} className="inline-flex items-center gap-1 rounded-full bg-white/15 px-2.5 py-1 text-[11px] font-medium text-indigo-100 ring-1 ring-white/10">
                        <Zap size={9} />{s.display_name || s.name}
                      </span>
                    ))}
                  </div>
                )}
              </div>

              {/* Left bottom info */}
              <div className="mt-8 pt-4 border-t border-white/10 space-y-1.5">
                <div className="flex items-center justify-between text-xs">
                  <span className="text-indigo-200">角色</span>
                  <span className="font-medium">{ROLE_OPTIONS.find((r) => r.value === form.role)?.label || form.role}</span>
                </div>
                <div className="flex items-center justify-between text-xs">
                  <span className="text-indigo-200">风险等级</span>
                  <span className="font-medium">{RISK_CONFIG[form.risk_level]?.label || form.risk_level}</span>
                </div>
                <div className="flex items-center justify-between text-xs">
                  <span className="text-indigo-200">参与流程</span>
                  <span className="font-medium">{form.participation_modes.map((m) => MODE_LABELS[m] || m).join("、")}</span>
                </div>
                {form.model && (
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-indigo-200">模型</span>
                    <span className="font-medium">{form.model}</span>
                  </div>
                )}
              </div>
            </div>

            {/* Right: Form */}
            <div className="flex-1 min-h-0 flex flex-col">
              {/* Header */}
              <div className="px-6 pt-5 pb-3 border-b border-slate-100 dark:border-slate-800/60">
                <div className="flex items-center justify-between">
                  <DialogTitle className="flex items-center gap-2 text-base">
                    {dialogMode === "create" ? "创建自定义专家" : `编辑 ${form.display_name}`}
                  </DialogTitle>
                  <Button variant="ghost" size="icon-sm" onClick={() => setDialogOpen(false)} className="cursor-pointer text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 -mr-1.5">
                    <X size={16} />
                  </Button>
                </div>
                <DialogDescription className="text-slate-500 dark:text-slate-400 mt-1">
                  {dialogMode === "create" ? "配置一个新的流程专家" : "修改专家的配置信息"}
                </DialogDescription>
              </div>

              {/* Scrollable form */}
              <div className="flex-1 min-h-0 overflow-y-auto px-6 py-5 space-y-4">
                {/* Name + Display Name */}
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-400">名称（英文标识）</label>
                    <input
                      type="text" value={form.name}
                      onChange={(e) => dialogMode === "create" && setForm({ ...form, name: e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, "") })}
                      placeholder="my-expert" disabled={dialogMode === "edit"}
                      className="h-9 w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/50 px-3 text-sm text-slate-700 dark:text-slate-200 placeholder:text-slate-400 outline-none focus:border-indigo-300 dark:focus:border-indigo-500/40 focus:ring-2 focus:ring-indigo-500/10 transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed"
                    />
                  </div>
                  <div>
                    <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-400">显示名称</label>
                    <input
                      type="text" value={form.display_name}
                      onChange={(e) => setForm({ ...form, display_name: e.target.value })}
                      placeholder="我的专家"
                      className="h-9 w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/50 px-3 text-sm text-slate-700 dark:text-slate-200 placeholder:text-slate-400 outline-none focus:border-indigo-300 dark:focus:border-indigo-500/40 focus:ring-2 focus:ring-indigo-500/10 transition-all duration-200"
                    />
                  </div>
                </div>

                {/* Role + Risk */}
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-400">角色</label>
                    <Select value={form.role} onValueChange={(v) => v != null && setForm({ ...form, role: v })}>
                      <SelectTrigger className="w-full h-9 text-sm">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectGroup>
                          {ROLE_OPTIONS.map((r) => (
                            <SelectItem key={r.value} value={r.value}>{r.label}</SelectItem>
                          ))}
                        </SelectGroup>
                      </SelectContent>
                    </Select>
                  </div>
                  <div>
                    <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-400">风险等级</label>
                    <Select value={form.risk_level} onValueChange={(v) => v != null && setForm({ ...form, risk_level: v })}>
                      <SelectTrigger className="w-full h-9 text-sm">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectGroup>
                          <SelectItem value="low">低风险</SelectItem>
                          <SelectItem value="medium">中风险</SelectItem>
                          <SelectItem value="high">高风险</SelectItem>
                        </SelectGroup>
                      </SelectContent>
                    </Select>
                  </div>
                </div>

                {/* Goal */}
                <div>
                  <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-400">目标</label>
                  <input
                    type="text" value={form.goal}
                    onChange={(e) => setForm({ ...form, goal: e.target.value })}
                    placeholder="这个专家负责什么判断？"
                    className="h-9 w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/50 px-3 text-sm text-slate-700 dark:text-slate-200 placeholder:text-slate-400 outline-none focus:border-indigo-300 dark:focus:border-indigo-500/40 focus:ring-2 focus:ring-indigo-500/10 transition-all duration-200"
                  />
                </div>

                {/* System Prompt */}
                <div>
                  <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-400">专家工作指令</label>
                  <textarea
                    value={form.system_prompt}
                    onChange={(e) => setForm({ ...form, system_prompt: e.target.value })}
                    rows={5} placeholder="你是一个..."
                    className="w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/50 px-3 py-2 text-sm text-slate-700 dark:text-slate-200 placeholder:text-slate-400 outline-none focus:border-indigo-300 dark:focus:border-indigo-500/40 focus:ring-2 focus:ring-indigo-500/10 resize-y font-mono transition-all duration-200"
                  />
                </div>

                {/* Model */}
                <div>
                  <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-400">指定模型（留空使用全局默认）</label>
                  <input
                    type="text" value={form.model}
                    onChange={(e) => setForm({ ...form, model: e.target.value })}
                    placeholder="gpt-4o"
                    className="h-9 w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/50 px-3 text-sm text-slate-700 dark:text-slate-200 placeholder:text-slate-400 outline-none focus:border-indigo-300 dark:focus:border-indigo-500/40 focus:ring-2 focus:ring-indigo-500/10 transition-all duration-200"
                  />
                </div>

                {/* Constraints */}
                <div>
                  <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-400">约束条件（每行一条）</label>
                  <textarea
                    value={form.constraints}
                    onChange={(e) => setForm({ ...form, constraints: e.target.value })}
                    rows={2} placeholder="必须先确认技术方案再编码"
                    className="w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/50 px-3 py-2 text-sm text-slate-700 dark:text-slate-200 placeholder:text-slate-400 outline-none focus:border-indigo-300 dark:focus:border-indigo-500/40 focus:ring-2 focus:ring-indigo-500/10 resize-y transition-all duration-200"
                  />
                </div>

                {/* Participation Modes */}
                <div>
                  <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-400">参与流程</label>
                  <div className="flex gap-2">
                    {(["planning"] as const).map((mode) => {
                      const active = form.participation_modes.includes(mode);
                      return (
                        <button key={mode} type="button" onClick={() => toggleMode(mode)}
                          className={`inline-flex items-center rounded-lg px-3 py-1.5 text-xs font-medium transition-all duration-200 cursor-pointer ${
                            active
                              ? "bg-indigo-600 text-white shadow-sm"
                              : "bg-white dark:bg-slate-800/50 text-slate-600 dark:text-slate-400 border border-slate-200 dark:border-slate-700 hover:border-indigo-300 dark:hover:border-indigo-500/40"
                          }`}>
                          {MODE_LABELS[mode]}
                        </button>
                      );
                    })}
                  </div>
                </div>

                {/* Methods */}
                <div>
                  <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-400">配备方法</label>
                  {skills.length === 0 ? (
                    <p className="text-xs text-slate-400 dark:text-slate-500">暂无可选方法</p>
                  ) : (
                    <div className="flex flex-wrap gap-1.5 max-h-32 overflow-y-auto rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/30 p-2.5">
                      {skills.map((skill) => {
                        const active = form.skills.some((s) => s.name === skill.name);
                        return (
                          <button key={skill.id} type="button" onClick={() => toggleSkill(skill)}
                            className={`inline-flex items-center gap-1 rounded-lg px-2.5 py-1 text-[11px] font-medium transition-all duration-200 cursor-pointer ${
                              active
                                ? "bg-amber-100 dark:bg-amber-500/15 text-amber-700 dark:text-amber-400 ring-1 ring-amber-200 dark:ring-amber-500/30"
                                : "bg-slate-50 dark:bg-slate-800 text-slate-500 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-700"
                            }`}>
                            <Zap size={9} />{skill.display_name}
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>
              </div>

              {/* Footer */}
              <div className="px-6 py-4 border-t border-slate-100 dark:border-slate-800/60 flex items-center justify-between bg-slate-50/50 dark:bg-slate-900/50">
                <div className="flex items-center gap-2">
                  <Button variant="outline" size="sm" onClick={() => setForm({ ...form, avatar_seed: generateSeed() })} className="gap-1.5 cursor-pointer h-8">
                    <ImagePlus size={13} />随机头像
                  </Button>
                  {dialogMode === "edit" && !agents.find(a => a.name === editingName)?.is_builtin && (
                    <Button variant="outline" size="sm" onClick={() => { deleteAgent(editingName!); setDialogOpen(false); }} className="gap-1.5 cursor-pointer h-8 text-red-600 hover:text-red-700 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-500/10 border-red-200 dark:border-red-500/30">
                      <Trash2 size={13} />删除
                    </Button>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <Button variant="outline" size="sm" onClick={() => setDialogOpen(false)} className="cursor-pointer h-8">取消</Button>
                  <Button size="sm" onClick={handleSave} disabled={saving || (dialogMode === "create" && (!form.name || !form.display_name || !form.system_prompt))} className="gap-1.5 cursor-pointer h-8">
                    {saving ? <Loader2 size={13} className="animate-spin" /> : dialogMode === "create" ? <Plus size={13} /> : null}
                    {saving ? "保存中..." : dialogMode === "create" ? "创建专家" : "保存修改"}
                  </Button>
                </div>
              </div>
            </div>
          </div>
        </DialogContent>
      </Dialog>
      {ConfirmDialog}
    </div>
  );
}
