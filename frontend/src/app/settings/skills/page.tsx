"use client";

import { useEffect, useState, useCallback } from "react";
import { Plus, Trash2, Pencil, Loader2 } from "lucide-react";
import { TopNav } from "../../components/topnav";

type Skill = {
  name: string;
  display_name: string;
  description: string | null;
  version: string;
  source_type: string;
  content: string | null;
  tools: string[];
};

type NewSkillForm = {
  name: string;
  display_name: string;
  description: string;
  version: string;
  source_type: string;
  content: string;
};

export default function SkillsPage() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editingSkill, setEditingSkill] = useState<string | null>(null);
  const [editForm, setEditForm] = useState<Partial<Skill>>({});

  const [newSkill, setNewSkill] = useState<NewSkillForm>({
    name: "", display_name: "", description: "", version: "1.0.0", source_type: "custom", content: "",
  });

  const fetchSkills = useCallback(async () => {
    try { const res = await fetch("/api/settings/skills"); setSkills(await res.json() || []); } catch {} finally { setLoading(false); }
  }, []);

  useEffect(() => { fetchSkills(); }, [fetchSkills]);

  const createSkill = async () => {
    if (!newSkill.name || !newSkill.display_name || !newSkill.content) return;
    setSaving(true);
    try {
      await fetch("/api/settings/skills", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(newSkill) });
      setShowCreate(false);
      setNewSkill({ name: "", display_name: "", description: "", version: "1.0.0", source_type: "custom", content: "" });
      await fetchSkills();
    } catch {} finally { setSaving(false); }
  };

  const deleteSkill = async (name: string) => {
    if (!confirm(`确定删除 Skill "${name}"？`)) return;
    try { await fetch(`/api/settings/skills/${name}`, { method: "DELETE" }); await fetchSkills(); } catch {}
  };

  const startEdit = (skill: Skill) => {
    setEditingSkill(skill.name);
    setEditForm({ display_name: skill.display_name, description: skill.description || "", content: skill.content || "" });
  };

  const saveEdit = async (skillName: string) => {
    setSaving(true);
    try {
      await fetch(`/api/settings/skills/${skillName}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(editForm) });
      setEditingSkill(null); setEditForm({}); await fetchSkills();
    } catch {} finally { setSaving(false); }
  };

  if (loading) {
    return (
      <div className="min-h-screen">
        <TopNav />
        <main className="flex items-center justify-center py-20"><div className="flex gap-1.5"><span className="typing-dot" /><span className="typing-dot" /><span className="typing-dot" /></div></main>
      </div>
    );
  }

  return (
    <div className="min-h-screen">
      <TopNav />
      <main className="min-w-0 pt-14">
        <div className="mx-auto max-w-4xl px-6 py-8">
          <div className="mb-6 flex items-center justify-between">
            <div>
              <h1 className="text-2xl font-semibold tracking-tight">Skill 管理</h1>
              <p className="mt-1 text-sm text-[var(--muted)]">管理 Agent 可用的 Skill</p>
            </div>
            <span className="rounded-md bg-[var(--accent-soft)] px-2 py-1 text-xs font-medium text-[var(--accent)]">{skills.length} 个 Skill</span>
          </div>

          <div className="space-y-3">
            {skills.map((skill) => (
              <div key={skill.name} className="card p-4 space-y-3">
                {editingSkill === skill.name ? (
                  <div className="space-y-3">
                    <div className="flex items-center justify-between">
                      <h3 className="text-sm font-semibold">编辑 {skill.display_name}</h3>
                      <div className="flex gap-2">
                        <button onClick={() => saveEdit(skill.name)} disabled={saving}
                          className="rounded-md bg-[var(--accent)] px-3 py-1.5 text-xs text-white cursor-pointer hover:bg-[var(--accent-hover)] disabled:opacity-30">{saving ? "保存中..." : "保存"}</button>
                        <button onClick={() => { setEditingSkill(null); setEditForm({}); }}
                          className="rounded-md border border-[var(--card-border)] px-3 py-1.5 text-xs cursor-pointer hover:bg-[var(--surface-elevated)]">取消</button>
                      </div>
                    </div>
                    <div>
                      <label className="mb-1 block text-xs font-medium text-[var(--muted)]">显示名称</label>
                      <input type="text" value={editForm.display_name || ""} onChange={(e) => setEditForm({ ...editForm, display_name: e.target.value })}
                        className="w-full rounded-md border border-[var(--card-border)] bg-[var(--surface)] px-3 py-2 text-xs outline-none focus:border-[var(--accent)]" />
                    </div>
                    <div>
                      <label className="mb-1 block text-xs font-medium text-[var(--muted)]">描述</label>
                      <textarea value={editForm.description || ""} onChange={(e) => setEditForm({ ...editForm, description: e.target.value })} rows={2}
                        className="w-full rounded-md border border-[var(--card-border)] bg-[var(--surface)] px-3 py-2 text-xs outline-none focus:border-[var(--accent)] resize-y" />
                    </div>
                    <div>
                      <label className="mb-1 block text-xs font-medium text-[var(--muted)]">系统提示词</label>
                      <textarea value={editForm.content || ""} onChange={(e) => setEditForm({ ...editForm, content: e.target.value })} rows={5}
                        className="w-full rounded-md border border-[var(--card-border)] bg-[var(--surface)] px-3 py-2 text-xs outline-none focus:border-[var(--accent)] resize-y font-mono" />
                    </div>
                  </div>
                ) : (
                  <>
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-3">
                        <div className="flex size-9 items-center justify-center rounded-md bg-[var(--accent-soft)] text-sm font-bold text-[var(--accent)]">
                          {skill.display_name[0]}
                        </div>
                        <div>
                          <h3 className="text-sm font-semibold">{skill.display_name}</h3>
                          <div className="flex items-center gap-2 mt-0.5">
                            <span className="text-[11px] text-[var(--muted)]">{skill.name}</span>
                            <span className="text-[11px] text-[var(--muted)]">v{skill.version}</span>
                            <span className={`text-[10px] px-1.5 py-0.5 rounded ${skill.source_type === "builtin" ? "bg-[var(--accent-soft)] text-[var(--accent)]" : "bg-[var(--surface)] text-[var(--muted)]"}`}>
                              {skill.source_type === "builtin" ? "内置" : "自定义"}
                            </span>
                          </div>
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <button onClick={() => startEdit(skill)} className="text-xs text-[var(--muted)] hover:text-[var(--foreground)] cursor-pointer transition-colors">编辑</button>
                        {skill.source_type !== "builtin" && (
                          <button onClick={() => deleteSkill(skill.name)} className="text-xs text-[var(--danger)] hover:underline cursor-pointer">删除</button>
                        )}
                      </div>
                    </div>
                    {skill.description && <p className="text-xs text-[var(--muted)] leading-relaxed">{skill.description}</p>}
                    {skill.tools && skill.tools.length > 0 && (
                      <div className="flex flex-wrap gap-1">
                        <span className="text-[10px] text-[var(--muted)] mr-1">工具：</span>
                        {skill.tools.map((tool, i) => <span key={i} className="rounded-md border border-[var(--card-border)] bg-[var(--surface)] px-2 py-0.5 text-[10px]">{tool}</span>)}
                      </div>
                    )}
                  </>
                )}
              </div>
            ))}
          </div>

          <div className="mt-4">
            {showCreate ? (
              <div className="card p-5 space-y-3">
                <h3 className="text-sm font-semibold">创建自定义 Skill</h3>
                <div className="grid grid-cols-2 gap-3">
                  <div><label className="mb-1 block text-xs font-medium text-[var(--muted)]">名称（英文标识）</label>
                    <input type="text" value={newSkill.name} onChange={(e) => setNewSkill({ ...newSkill, name: e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, "") })} placeholder="my-skill"
                      className="w-full rounded-md border border-[var(--card-border)] bg-[var(--surface)] px-3 py-2 text-xs outline-none focus:border-[var(--accent)]" /></div>
                  <div><label className="mb-1 block text-xs font-medium text-[var(--muted)]">显示名称</label>
                    <input type="text" value={newSkill.display_name} onChange={(e) => setNewSkill({ ...newSkill, display_name: e.target.value })} placeholder="我的 Skill"
                      className="w-full rounded-md border border-[var(--card-border)] bg-[var(--surface)] px-3 py-2 text-xs outline-none focus:border-[var(--accent)]" /></div>
                </div>
                <div><label className="mb-1 block text-xs font-medium text-[var(--muted)]">描述</label>
                  <input type="text" value={newSkill.description} onChange={(e) => setNewSkill({ ...newSkill, description: e.target.value })} placeholder="描述此 Skill 的用途"
                    className="w-full rounded-md border border-[var(--card-border)] bg-[var(--surface)] px-3 py-2 text-xs outline-none focus:border-[var(--accent)]" /></div>
                <div className="grid grid-cols-2 gap-3">
                  <div><label className="mb-1 block text-xs font-medium text-[var(--muted)]">版本</label>
                    <input type="text" value={newSkill.version} onChange={(e) => setNewSkill({ ...newSkill, version: e.target.value })}
                      className="w-full rounded-md border border-[var(--card-border)] bg-[var(--surface)] px-3 py-2 text-xs outline-none focus:border-[var(--accent)]" /></div>
                  <div><label className="mb-1 block text-xs font-medium text-[var(--muted)]">类型</label>
                    <select value={newSkill.source_type} onChange={(e) => setNewSkill({ ...newSkill, source_type: e.target.value })}
                      className="w-full rounded-md border border-[var(--card-border)] bg-[var(--surface)] px-3 py-2 text-xs outline-none focus:border-[var(--accent)]">
                      <option value="custom">自定义</option><option value="builtin">内置</option></select></div>
                </div>
                <div><label className="mb-1 block text-xs font-medium text-[var(--muted)]">系统提示词</label>
                  <textarea value={newSkill.content} onChange={(e) => setNewSkill({ ...newSkill, content: e.target.value })} rows={5} placeholder="你是一个专门负责..."
                    className="w-full rounded-md border border-[var(--card-border)] bg-[var(--surface)] px-3 py-2 text-xs outline-none focus:border-[var(--accent)] resize-y font-mono" /></div>
                <div className="flex gap-2">
                  <button onClick={createSkill} disabled={saving || !newSkill.name || !newSkill.display_name || !newSkill.content}
                    className="rounded-md bg-[var(--accent)] px-4 py-2 text-xs font-medium text-white cursor-pointer hover:bg-[var(--accent-hover)] disabled:opacity-30 transition">{saving ? "创建中..." : "创建 Skill"}</button>
                  <button onClick={() => setShowCreate(false)} className="rounded-md border border-[var(--card-border)] px-4 py-2 text-xs cursor-pointer hover:bg-[var(--surface-elevated)] transition">取消</button>
                </div>
              </div>
            ) : (
              <button onClick={() => setShowCreate(true)}
                className="flex items-center gap-2 rounded-md border border-dashed border-[var(--card-border)] p-3 w-full text-left cursor-pointer hover:border-[var(--accent)] hover:bg-[var(--accent-soft)] transition-all">
                <Plus size={14} className="text-[var(--accent)]" /><span className="text-sm font-medium">创建自定义 Skill</span>
              </button>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
