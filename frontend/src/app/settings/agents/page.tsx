"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import {
  Pencil, Trash2, Plus, Loader2, Shield, Zap, X, Bot, ChevronRight
} from "lucide-react";
import { TopNav } from "../../components/topnav";

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

type NewAgentForm = {
  name: string; display_name: string; role: string;
  goal: string; system_prompt: string; model: string; provider: string;
  constraints: string; participation_modes: string[];
  risk_level: string; skills: SkillRef[];
};

const ROLE_OPTIONS = [
  { value: "coordinator", label: "协调者" },
  { value: "architect", label: "架构师" },
  { value: "developer", label: "开发者" },
  { value: "reviewer", label: "审查者" },
  { value: "tester", label: "测试者" },
  { value: "analyst", label: "分析师" },
  { value: "custom", label: "自定义" },
];

const RISK_COLORS: Record<string, string> = { low: "var(--success)", medium: "var(--warning)", high: "var(--danger)" };
const RISK_LABELS: Record<string, string> = { low: "低风险", medium: "中风险", high: "高风险" };
const MODE_LABELS: Record<string, string> = { planning: "Planning", execution: "Execution", roundtable: "圆桌讨论" };

export default function AgentsPage() {
  const [agents, setAgents] = useState<AgentTemplate[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [editingAgent, setEditingAgent] = useState<string | null>(null);
  const [editForm, setEditForm] = useState<Partial<AgentTemplate> & { skills?: SkillRef[] }>({});
  const [saving, setSaving] = useState(false);
  const [newAgent, setNewAgent] = useState<NewAgentForm>({
    name: "", display_name: "", role: "custom", goal: "", system_prompt: "",
    model: "", provider: "", constraints: "", participation_modes: ["planning"],
    risk_level: "low", skills: [],
  });

  const fetchAgents = useCallback(async () => {
    try { const res = await fetch("/api/settings/agents"); setAgents(await res.json() || []); } catch {} finally { setLoading(false); }
  }, []);
  const fetchSkills = useCallback(async () => {
    try { const res = await fetch("/api/settings/skills"); setSkills(await res.json() || []); } catch {}
  }, []);

  useEffect(() => { fetchAgents(); fetchSkills(); }, [fetchAgents, fetchSkills]);

  const toggleSkillInNew = (skill: Skill) => {
    const exists = newAgent.skills.some((s) => s.name === skill.name);
    setNewAgent({ ...newAgent, skills: exists ? newAgent.skills.filter((s) => s.name !== skill.name) : [...newAgent.skills, { name: skill.name, display_name: skill.display_name }] });
  };
  const toggleSkillInEdit = (skill: Skill) => {
    const current = editForm.skills || [];
    const exists = current.some((s) => s.name === skill.name);
    setEditForm({ ...editForm, skills: exists ? current.filter((s) => s.name !== skill.name) : [...current, { name: skill.name, display_name: skill.display_name }] });
  };

  const createAgent = async () => {
    if (!newAgent.name || !newAgent.display_name || !newAgent.system_prompt) return;
    setSaving(true);
    try {
      await fetch("/api/settings/agents", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...newAgent, constraints: newAgent.constraints.split("\n").filter(Boolean), capabilities: [], allowed_tools: [] }),
      });
      setShowCreate(false);
      setNewAgent({ name: "", display_name: "", role: "custom", goal: "", system_prompt: "", model: "", provider: "", constraints: "", participation_modes: ["planning"], risk_level: "low", skills: [] });
      await fetchAgents();
    } catch {} finally { setSaving(false); }
  };

  const deleteAgent = async (name: string) => {
    if (!confirm(`确定删除 Agent "${name}"？`)) return;
    try { await fetch(`/api/settings/agents/${name}`, { method: "DELETE" }); await fetchAgents(); } catch {}
  };

  const saveEdit = async (agentName: string) => {
    setSaving(true);
    try {
      await fetch(`/api/settings/agents/${agentName}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(editForm) });
      setEditingAgent(null); setEditForm({}); await fetchAgents();
    } catch {} finally { setSaving(false); }
  };

  const startEdit = (agent: AgentTemplate) => {
    setEditingAgent(agent.name);
    setEditForm({
      display_name: agent.display_name, role: agent.role, goal: agent.goal || "",
      system_prompt: agent.system_prompt || "", model: agent.model || "",
      constraints: agent.constraints, participation_modes: agent.participation_modes,
      risk_level: agent.risk_level, skills: agent.skills || [],
    });
  };

  if (loading) {
    return (
      <div className="min-h-screen">
        <TopNav />
        <main className="flex items-center justify-center py-20"><div className="flex gap-1.5"><span className="typing-dot" /><span className="typing-dot" /><span className="typing-dot" /></div></main>
      </div>
    );
  }

  const renderSkillSelector = (selected: SkillRef[], toggle: (skill: Skill) => void) => (
    <div>
      <label className="mb-1.5 block text-xs font-medium text-[var(--muted)]">配备 Skill</label>
      {skills.length === 0 ? (
        <p className="text-xs text-[var(--muted)]">暂无可选 Skill</p>
      ) : (
        <div className="flex flex-wrap gap-1.5 max-h-32 overflow-y-auto rounded-md border border-[var(--card-border)] bg-[var(--surface)] p-2">
          {skills.map((skill) => {
            const active = selected.some((s) => s.name === skill.name);
            return (
              <button key={skill.id} onClick={() => toggle(skill)}
                className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-[11px] font-medium transition cursor-pointer ${
                  active ? "bg-[var(--accent)] text-white" : "border border-[var(--card-border)] text-[var(--muted)] hover:border-[var(--accent)] hover:text-[var(--foreground)]"
                }`}>
                <Zap size={8} />{skill.display_name}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );

  return (
    <div className="min-h-screen">
      <TopNav />
      <main className="min-w-0 pt-14">
        <div className="mx-auto max-w-4xl px-6 py-8">
          {/* Header */}
          <div className="mb-6 flex items-center justify-between">
            <div>
              <h1 className="text-2xl font-semibold tracking-tight">Agent 团队</h1>
              <p className="mt-1 text-sm text-[var(--muted)]">管理 AI Agent 团队成员</p>
            </div>
            <span className="rounded-md bg-[var(--accent-soft)] px-2 py-1 text-xs font-medium text-[var(--accent)]">{agents.length} 个 Agent</span>
          </div>

          {/* Agent List */}
          <div className="space-y-3">
            {agents.map((agent) => (
              <div key={agent.id} className="card p-4">
                {editingAgent === agent.name ? (
                  <div className="space-y-3">
                    <div className="flex items-center justify-between">
                      <h3 className="text-sm font-semibold">编辑 {agent.display_name}</h3>
                      <div className="flex gap-2">
                        <button onClick={() => saveEdit(agent.name)} disabled={saving}
                          className="flex items-center gap-1 rounded-md bg-[var(--accent)] px-3 py-1.5 text-xs text-white cursor-pointer hover:bg-[var(--accent-hover)] disabled:opacity-30">
                          {saving ? <Loader2 size={11} className="animate-spin" /> : null}{saving ? "保存中..." : "保存"}
                        </button>
                        <button onClick={() => { setEditingAgent(null); setEditForm({}); }}
                          className="rounded-md border border-[var(--card-border)] px-3 py-1.5 text-xs cursor-pointer hover:bg-[var(--surface-elevated)]">取消</button>
                      </div>
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      <div><label className="mb-1 block text-xs font-medium text-[var(--muted)]">显示名称</label>
                        <input type="text" value={editForm.display_name || ""} onChange={(e) => setEditForm({ ...editForm, display_name: e.target.value })}
                          className="w-full rounded-md border border-[var(--card-border)] bg-[var(--surface)] px-3 py-2 text-xs outline-none focus:border-[var(--accent)]" /></div>
                      <div><label className="mb-1 block text-xs font-medium text-[var(--muted)]">风险等级</label>
                        <select value={editForm.risk_level || "low"} onChange={(e) => setEditForm({ ...editForm, risk_level: e.target.value })}
                          className="w-full rounded-md border border-[var(--card-border)] bg-[var(--surface)] px-3 py-2 text-xs outline-none focus:border-[var(--accent)]">
                          <option value="low">低风险</option><option value="medium">中风险</option><option value="high">高风险</option></select></div>
                    </div>
                    <div><label className="mb-1 block text-xs font-medium text-[var(--muted)]">目标</label>
                      <textarea value={editForm.goal || ""} onChange={(e) => setEditForm({ ...editForm, goal: e.target.value })} rows={2}
                        className="w-full rounded-md border border-[var(--card-border)] bg-[var(--surface)] px-3 py-2 text-xs outline-none focus:border-[var(--accent)] resize-y" /></div>
                    <div><label className="mb-1 block text-xs font-medium text-[var(--muted)]">System Prompt</label>
                      <textarea value={editForm.system_prompt || ""} onChange={(e) => setEditForm({ ...editForm, system_prompt: e.target.value })} rows={4}
                        className="w-full rounded-md border border-[var(--card-border)] bg-[var(--surface)] px-3 py-2 text-xs outline-none focus:border-[var(--accent)] resize-y font-mono" /></div>
                    <div><label className="mb-1 block text-xs font-medium text-[var(--muted)]">指定模型</label>
                      <input type="text" value={editForm.model || ""} onChange={(e) => setEditForm({ ...editForm, model: e.target.value || null })} placeholder="留空使用全局默认"
                        className="w-full rounded-md border border-[var(--card-border)] bg-[var(--surface)] px-3 py-2 text-xs outline-none focus:border-[var(--accent)]" /></div>
                    {renderSkillSelector(editForm.skills || [], toggleSkillInEdit)}
                  </div>
                ) : (
                  <>
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex items-start gap-3 min-w-0">
                        <div className="flex size-9 shrink-0 items-center justify-center rounded-md bg-[var(--accent-soft)] text-[var(--accent)]">
                          <Bot size={16} />
                        </div>
                        <div className="min-w-0">
                          <div className="flex items-center gap-2">
                            <h3 className="text-sm font-semibold">{agent.display_name}</h3>
                            {agent.is_builtin && <span className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--accent-soft)] text-[var(--accent)] font-medium">内置</span>}
                          </div>
                          <div className="flex items-center gap-2 mt-0.5 flex-wrap">
                            <span className="text-[11px] text-[var(--muted)]">{agent.role}</span>
                            <span className="text-[11px]" style={{ color: RISK_COLORS[agent.risk_level] || "var(--muted)" }}>
                              <Shield size={9} className="inline mr-0.5" />{RISK_LABELS[agent.risk_level] || agent.risk_level}
                            </span>
                          </div>
                          {agent.goal && <p className="mt-1.5 text-xs text-[var(--muted)] leading-relaxed">{agent.goal}</p>}
                          {agent.skills && agent.skills.length > 0 && (
                            <div className="flex flex-wrap items-center gap-1 mt-2">
                              {agent.skills.map((s, i) => (
                                <span key={i} className="inline-flex items-center gap-0.5 rounded bg-[var(--accent-soft)] px-1.5 py-0.5 text-[10px] font-medium text-[var(--accent)]">
                                  <Zap size={7} />{s.display_name || s.name}</span>
                              ))}
                            </div>
                          )}
                          {agent.capabilities.length > 0 && (
                            <div className="flex flex-wrap gap-1 mt-2">
                              {agent.capabilities.map((c, i) => (
                                <span key={i} className="rounded border border-[var(--card-border)] bg-[var(--surface)] px-1.5 py-0.5 text-[10px] text-[var(--muted)]">{c.description || c.name}</span>
                              ))}
                            </div>
                          )}
                          <div className="flex items-center gap-1.5 mt-2">
                            {agent.participation_modes.map((m) => (
                              <span key={m} className="rounded bg-[var(--accent-soft)] px-1.5 py-0.5 text-[10px] text-[var(--accent)]">{MODE_LABELS[m] || m}</span>
                            ))}
                            {agent.model && <span className="text-[10px] text-[var(--muted)]">模型：{agent.model}</span>}
                          </div>
                        </div>
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        <button onClick={() => startEdit(agent)} className="flex items-center gap-1 text-xs text-[var(--muted)] hover:text-[var(--foreground)] cursor-pointer transition-colors">
                          <Pencil size={10} />编辑</button>
                        {!agent.is_builtin && (
                          <button onClick={() => deleteAgent(agent.name)} className="flex items-center gap-1 text-xs text-[var(--danger)] hover:underline cursor-pointer">
                            <Trash2 size={10} />删除</button>
                        )}
                      </div>
                    </div>
                  </>
                )}
              </div>
            ))}
          </div>

          {/* Create Agent */}
          <div className="mt-4">
            {showCreate ? (
              <div className="card p-5 space-y-3">
                <h3 className="text-sm font-semibold">创建自定义 Agent</h3>
                <div className="grid grid-cols-2 gap-3">
                  <div><label className="mb-1 block text-xs font-medium text-[var(--muted)]">名称（英文标识）</label>
                    <input type="text" value={newAgent.name} onChange={(e) => setNewAgent({ ...newAgent, name: e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, "") })} placeholder="my-agent"
                      className="w-full rounded-md border border-[var(--card-border)] bg-[var(--surface)] px-3 py-2 text-xs outline-none focus:border-[var(--accent)]" /></div>
                  <div><label className="mb-1 block text-xs font-medium text-[var(--muted)]">显示名称</label>
                    <input type="text" value={newAgent.display_name} onChange={(e) => setNewAgent({ ...newAgent, display_name: e.target.value })} placeholder="我的 Agent"
                      className="w-full rounded-md border border-[var(--card-border)] bg-[var(--surface)] px-3 py-2 text-xs outline-none focus:border-[var(--accent)]" /></div>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div><label className="mb-1 block text-xs font-medium text-[var(--muted)]">角色</label>
                    <select value={newAgent.role} onChange={(e) => setNewAgent({ ...newAgent, role: e.target.value })}
                      className="w-full rounded-md border border-[var(--card-border)] bg-[var(--surface)] px-3 py-2 text-xs outline-none focus:border-[var(--accent)]">
                      {ROLE_OPTIONS.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}</select></div>
                  <div><label className="mb-1 block text-xs font-medium text-[var(--muted)]">风险等级</label>
                    <select value={newAgent.risk_level} onChange={(e) => setNewAgent({ ...newAgent, risk_level: e.target.value })}
                      className="w-full rounded-md border border-[var(--card-border)] bg-[var(--surface)] px-3 py-2 text-xs outline-none focus:border-[var(--accent)]">
                      <option value="low">低风险</option><option value="medium">中风险</option><option value="high">高风险</option></select></div>
                </div>
                <div><label className="mb-1 block text-xs font-medium text-[var(--muted)]">目标</label>
                  <input type="text" value={newAgent.goal} onChange={(e) => setNewAgent({ ...newAgent, goal: e.target.value })} placeholder="负责什么工作？"
                    className="w-full rounded-md border border-[var(--card-border)] bg-[var(--surface)] px-3 py-2 text-xs outline-none focus:border-[var(--accent)]" /></div>
                <div><label className="mb-1 block text-xs font-medium text-[var(--muted)]">System Prompt</label>
                  <textarea value={newAgent.system_prompt} onChange={(e) => setNewAgent({ ...newAgent, system_prompt: e.target.value })} rows={4} placeholder="你是一个..."
                    className="w-full rounded-md border border-[var(--card-border)] bg-[var(--surface)] px-3 py-2 text-xs outline-none focus:border-[var(--accent)] resize-y font-mono" /></div>
                <div><label className="mb-1 block text-xs font-medium text-[var(--muted)]">指定模型（留空使用全局默认）</label>
                  <input type="text" value={newAgent.model} onChange={(e) => setNewAgent({ ...newAgent, model: e.target.value })} placeholder="gpt-4o"
                    className="w-full rounded-md border border-[var(--card-border)] bg-[var(--surface)] px-3 py-2 text-xs outline-none focus:border-[var(--accent)]" /></div>
                <div><label className="mb-1 block text-xs font-medium text-[var(--muted)]">约束条件（每行一条）</label>
                  <textarea value={newAgent.constraints} onChange={(e) => setNewAgent({ ...newAgent, constraints: e.target.value })} rows={2} placeholder="必须先确认技术方案再编码"
                    className="w-full rounded-md border border-[var(--card-border)] bg-[var(--surface)] px-3 py-2 text-xs outline-none focus:border-[var(--accent)] resize-y" /></div>
                <div><label className="mb-1 block text-xs font-medium text-[var(--muted)]">参与模式</label>
                  <div className="flex gap-2">
                    {(["planning", "execution", "roundtable"] as const).map((mode) => (
                      <button key={mode} onClick={() => {
                        const modes = newAgent.participation_modes.includes(mode) ? newAgent.participation_modes.filter((m) => m !== mode) : [...newAgent.participation_modes, mode];
                        setNewAgent({ ...newAgent, participation_modes: modes });
                      }} className={`rounded-md px-3 py-1.5 text-xs cursor-pointer transition ${newAgent.participation_modes.includes(mode) ? "bg-[var(--accent)] text-white" : "border border-[var(--card-border)] hover:border-[var(--accent)]"}`}>
                        {MODE_LABELS[mode]}</button>
                    ))}
                  </div>
                </div>
                {renderSkillSelector(newAgent.skills, toggleSkillInNew)}
                <div className="flex gap-2 pt-1">
                  <button onClick={createAgent} disabled={saving || !newAgent.name || !newAgent.display_name || !newAgent.system_prompt}
                    className="flex items-center gap-1.5 rounded-md bg-[var(--accent)] px-4 py-2 text-xs font-medium text-white cursor-pointer hover:bg-[var(--accent-hover)] disabled:opacity-30 transition">
                    {saving ? <Loader2 size={11} className="animate-spin" /> : <Plus size={11} />}{saving ? "创建中..." : "创建 Agent"}
                  </button>
                  <button onClick={() => setShowCreate(false)} className="rounded-md border border-[var(--card-border)] px-4 py-2 text-xs cursor-pointer hover:bg-[var(--surface-elevated)] transition">取消</button>
                </div>
              </div>
            ) : (
              <button onClick={() => setShowCreate(true)}
                className="flex items-center gap-2 rounded-md border border-dashed border-[var(--card-border)] p-3 w-full text-left cursor-pointer hover:border-[var(--accent)] hover:bg-[var(--accent-soft)] transition-all">
                <Plus size={14} className="text-[var(--accent)]" /><span className="text-sm font-medium">创建自定义 Agent</span>
              </button>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
