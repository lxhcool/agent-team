"use client";

import { useEffect, useState, useCallback } from "react";
import { Plus, Trash2, Pencil, Loader2, Zap, X, Wrench } from "lucide-react";
import { TopNav } from "../../components/topnav";
import { Button } from "@/components/ui/button";
import {
  Dialog, DialogContent, DialogTitle, DialogDescription,
} from "@/components/ui/dialog";
import { useConfirm } from "@/components/ui/confirm-dialog";

type Skill = {
  id: string;
  name: string;
  display_name: string;
  description: string | null;
  version: string;
  source_type: string;
  content: string | null;
  tools: string[];
};

type SkillForm = {
  name: string;
  display_name: string;
  description: string;
  version: string;
  source_type: string;
  content: string;
};

const emptyForm: SkillForm = {
  name: "", display_name: "", description: "", version: "1.0.0", source_type: "custom", content: "",
};

export default function SkillsPage() {
  const { confirm, ConfirmDialog } = useConfirm();
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);

  // Dialog state
  const [dialogOpen, setDialogOpen] = useState(false);
  const [dialogMode, setDialogMode] = useState<"create" | "edit">("create");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<SkillForm>(emptyForm);
  const [saving, setSaving] = useState(false);

  const fetchSkills = useCallback(async () => {
    try { const res = await fetch("/api/settings/skills"); setSkills(await res.json() || []); } catch {} finally { setLoading(false); }
  }, []);

  useEffect(() => { fetchSkills(); }, [fetchSkills]);

  const openCreate = () => {
    setForm(emptyForm);
    setDialogMode("create");
    setEditingId(null);
    setDialogOpen(true);
  };

  const openEdit = (skill: Skill) => {
    setForm({
      name: skill.name,
      display_name: skill.display_name,
      description: skill.description || "",
      version: skill.version,
      source_type: skill.source_type,
      content: skill.content || "",
    });
    setDialogMode("edit");
    setEditingId(skill.id);
    setDialogOpen(true);
  };

  const handleSave = async () => {
    if (dialogMode === "create" && (!form.name || !form.display_name || !form.content)) return;
    if (dialogMode === "edit" && !editingId) return;
    setSaving(true);
    try {
      if (dialogMode === "create") {
        await fetch("/api/settings/skills", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(form),
        });
      } else {
        await fetch(`/api/settings/skills/${editingId}`, {
          method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ display_name: form.display_name, description: form.description, content: form.content }),
        });
      }
      setDialogOpen(false);
      await fetchSkills();
    } catch {} finally { setSaving(false); }
  };

  const deleteSkill = async (skill: Skill) => {
    if (skill.source_type === "builtin") return;
    const label = skill.display_name || skill.name;
    if (!await confirm({ description: `确定删除方法 "${label}"？`, variant: "destructive" })) return;
    try { await fetch(`/api/settings/skills/${skill.id}`, { method: "DELETE" }); await fetchSkills(); } catch {}
  };

  const deleteEditingSkill = async () => {
    const skill = skills.find((item) => item.id === editingId);
    if (!skill) return;
    if (!await confirm({ description: `确定删除方法 "${skill.display_name || skill.name}"？`, variant: "destructive" })) return;
    try {
      await fetch(`/api/settings/skills/${skill.id}`, { method: "DELETE" });
      setDialogOpen(false);
      await fetchSkills();
    } catch {}
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-violet-50/30 dark:from-slate-950 dark:via-slate-950 dark:to-violet-950/20">
        <TopNav />
        <main className="flex items-center justify-center py-20"><div className="flex gap-1.5"><span className="typing-dot" /><span className="typing-dot" /><span className="typing-dot" /></div></main>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-violet-50/30 dark:from-slate-950 dark:via-slate-950 dark:to-violet-950/20">
      <TopNav />
      <main className="min-w-0 pt-14">
        <div className="mx-auto max-w-5xl px-6 py-8">
          {/* Header */}
          <div className="mb-6 flex items-center justify-between">
            <div>
              <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight text-slate-900 dark:text-slate-100">
                <div className="flex size-8 items-center justify-center rounded-lg bg-violet-100 dark:bg-violet-500/15">
                  <Zap size={16} className="text-violet-600 dark:text-violet-400" />
                </div>
                方法库
              </h1>
              <p className="mt-1.5 text-sm text-slate-500 dark:text-slate-400">管理专家可用的专业方法</p>
            </div>
            <div className="flex items-center gap-3">
              <span className="inline-flex items-center gap-1.5 rounded-full bg-violet-50 dark:bg-violet-500/10 px-3 py-1.5 text-xs font-semibold text-violet-600 dark:text-violet-400">
                <Zap size={11} />
                {skills.length} 个方法
              </span>
              <Button onClick={openCreate} size="sm" className="gap-1.5 cursor-pointer">
                <Plus size={14} />
                创建方法
              </Button>
            </div>
          </div>

          {/* Method Grid */}
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {skills.map((skill) => {
              const isBuiltin = skill.source_type === "builtin";
              return (
                <div key={skill.name} className="group relative rounded-xl bg-white/70 dark:bg-slate-900/70 backdrop-blur-sm ring-1 ring-slate-200/60 dark:ring-slate-700/40 transition-all duration-200 hover:ring-violet-300/80 dark:hover:ring-violet-500/30 hover:shadow-md hover:shadow-violet-500/5 flex flex-col">
                  {/* Card body */}
                  <div className="px-4 pt-3.5 pb-2">
                    <div className="flex items-start gap-3">
                      {/* Avatar */}
                      <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-violet-50 dark:bg-violet-500/10 text-violet-600 dark:text-violet-400">
                        <Zap size={16} />
                      </div>

                      {/* Info */}
                      <div className="min-w-0 flex-1 pr-5">
                        {/* Line 1: Name + Builtin */}
                        <div className="flex items-center gap-1.5">
                          <h3 className="text-[13px] font-semibold text-slate-800 dark:text-slate-100 truncate">{skill.display_name}</h3>
                          {isBuiltin && (
                            <span className="shrink-0 rounded bg-slate-100 dark:bg-slate-800 px-1.5 py-0.5 text-[9px] font-semibold text-slate-500 dark:text-slate-400">内置</span>
                          )}
                        </div>

                        {/* Line 2: Name · Version · Tools */}
                        <div className="mt-1 flex items-center gap-1.5 text-[11px] text-slate-500 dark:text-slate-400">
                          <span className="font-mono truncate">{skill.name}</span>
                          <span className="text-slate-300 dark:text-slate-600">·</span>
                          <span>v{skill.version}</span>
                          {skill.tools && skill.tools.length > 0 && (
                            <>
                              <span className="text-slate-300 dark:text-slate-600">·</span>
                              <span className="flex items-center gap-0.5 text-violet-600 dark:text-violet-400">
                                <Wrench size={9} />{skill.tools.length}
                              </span>
                            </>
                          )}
                        </div>

                        {/* Line 3: Description */}
                        {skill.description && (
                          <p className="mt-1 text-[11px] text-slate-400 dark:text-slate-500 truncate">{skill.description}</p>
                        )}
                      </div>
                    </div>
                  </div>

                  {/* Card bottom actions */}
                  <div className="mt-auto px-4 py-2 border-t border-slate-100 dark:border-slate-800/60 flex items-center gap-2">
                    <button type="button" onClick={() => openEdit(skill)}
                      className="inline-flex items-center gap-1 rounded-md bg-violet-50 dark:bg-violet-500/10 hover:bg-violet-100 dark:hover:bg-violet-500/20 px-2 py-1 text-[10px] font-medium text-violet-600 dark:text-violet-400 transition-colors duration-200 cursor-pointer">
                      <Pencil size={10} />编辑
                    </button>
                    <button type="button" onClick={() => deleteSkill(skill)}
                      className={`inline-flex items-center gap-1 rounded-md px-2 py-1 text-[10px] font-medium transition-colors duration-200 cursor-pointer ${
                        isBuiltin
                          ? "bg-slate-50 dark:bg-slate-800 text-slate-300 dark:text-slate-600 cursor-not-allowed"
                          : "bg-red-50 dark:bg-red-500/10 hover:bg-red-100 dark:hover:bg-red-500/20 text-red-600 dark:text-red-400"
                      }`}
                      disabled={isBuiltin}>
                      <Trash2 size={10} />删除
                    </button>
                  </div>
                </div>
              );
            })}

            {/* Empty state */}
            {skills.length === 0 && (
              <div className="sm:col-span-2 lg:col-span-3 flex items-center gap-3 rounded-xl bg-white/70 dark:bg-slate-900/70 backdrop-blur-sm ring-1 ring-slate-200/60 dark:ring-slate-700/40 px-4 py-3.5">
                <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-slate-100 dark:bg-slate-800 text-slate-300 dark:text-slate-600">
                  <Zap size={15} strokeWidth={1.5} />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="text-[13px] font-medium text-slate-400 dark:text-slate-500">还没有自定义方法</div>
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
          <div className="flex w-full h-full min-h-0">
            {/* Left: Preview Panel */}
            <div className="hidden md:flex w-[360px] shrink-0 flex-col items-center justify-center overflow-y-auto bg-gradient-to-br from-violet-600 via-violet-700 to-purple-900 dark:from-violet-800 dark:via-violet-900 dark:to-purple-950 text-white p-8">
              <div className="flex flex-col items-center">
                <div className="flex size-20 items-center justify-center rounded-2xl bg-white/15 backdrop-blur-sm mb-5 ring-1 ring-white/20">
                  <Zap size={36} className="text-white" />
                </div>
                <h3 className="text-lg font-semibold text-center mb-2">
                  {form.display_name || "新方法"}
                </h3>
                <p className="text-sm text-violet-100 text-center leading-relaxed max-w-[240px]">
                  {form.description || "配置方法的适用场景与工作规则，让专家获得更稳定的判断方式"}
                </p>

                {form.content && (
                  <div className="mt-6 rounded-xl bg-white/10 p-4 ring-1 ring-white/10 max-w-[260px]">
                    <p className="text-[11px] text-violet-100/80 font-mono line-clamp-4 whitespace-pre-wrap">{form.content.slice(0, 120)}{form.content.length > 120 ? "..." : ""}</p>
                  </div>
                )}
              </div>

              {/* Left bottom info */}
              <div className="mt-8 pt-4 border-t border-white/10 space-y-1.5 w-full">
                <div className="flex items-center justify-between text-xs">
                  <span className="text-violet-200">标识</span>
                  <span className="font-mono font-medium">{form.name || "—"}</span>
                </div>
                <div className="flex items-center justify-between text-xs">
                  <span className="text-violet-200">版本</span>
                  <span className="font-medium">v{form.version}</span>
                </div>
                <div className="flex items-center justify-between text-xs">
                  <span className="text-violet-200">类型</span>
                  <span className="font-medium">{form.source_type === "builtin" ? "内置" : "自定义"}</span>
                </div>
              </div>
            </div>

            {/* Right: Form */}
            <div className="flex-1 min-h-0 flex flex-col">
              {/* Header */}
              <div className="px-6 pt-5 pb-3 border-b border-slate-100 dark:border-slate-800/60">
                <div className="flex items-center justify-between">
                  <DialogTitle className="flex items-center gap-2 text-base">
                    {dialogMode === "create" ? "创建自定义方法" : `编辑 ${form.display_name}`}
                  </DialogTitle>
                  <Button variant="ghost" size="icon-sm" onClick={() => setDialogOpen(false)} className="cursor-pointer text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 -mr-1.5">
                    <X size={16} />
                  </Button>
                </div>
                <DialogDescription className="text-slate-500 dark:text-slate-400 mt-1">
                  {dialogMode === "create" ? "配置一个新的专业方法" : "修改方法的配置信息"}
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
                      placeholder="my-method" disabled={dialogMode === "edit"}
                      className="h-9 w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/50 px-3 text-sm text-slate-700 dark:text-slate-200 placeholder:text-slate-400 outline-none focus:border-violet-300 dark:focus:border-violet-500/40 focus:ring-2 focus:ring-violet-500/10 transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed"
                    />
                  </div>
                  <div>
                    <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-400">显示名称</label>
                    <input
                      type="text" value={form.display_name}
                      onChange={(e) => setForm({ ...form, display_name: e.target.value })}
                      placeholder="我的方法"
                      className="h-9 w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/50 px-3 text-sm text-slate-700 dark:text-slate-200 placeholder:text-slate-400 outline-none focus:border-violet-300 dark:focus:border-violet-500/40 focus:ring-2 focus:ring-violet-500/10 transition-all duration-200"
                    />
                  </div>
                </div>

                {/* Description */}
                <div>
                  <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-400">描述</label>
                  <input
                    type="text" value={form.description}
                    onChange={(e) => setForm({ ...form, description: e.target.value })}
                    placeholder="描述这个方法适合解决什么问题"
                    className="h-9 w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/50 px-3 text-sm text-slate-700 dark:text-slate-200 placeholder:text-slate-400 outline-none focus:border-violet-300 dark:focus:border-violet-500/40 focus:ring-2 focus:ring-violet-500/10 transition-all duration-200"
                  />
                </div>

                {/* Version + Source Type */}
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-400">版本</label>
                    <input
                      type="text" value={form.version}
                      onChange={(e) => setForm({ ...form, version: e.target.value })}
                      placeholder="1.0.0"
                      className="h-9 w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/50 px-3 text-sm text-slate-700 dark:text-slate-200 placeholder:text-slate-400 outline-none focus:border-violet-300 dark:focus:border-violet-500/40 focus:ring-2 focus:ring-violet-500/10 transition-all duration-200"
                    />
                  </div>
                  <div>
                    <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-400">类型</label>
                    <select
                      value={form.source_type}
                      onChange={(e) => setForm({ ...form, source_type: e.target.value })}
                      disabled={dialogMode === "edit"}
                      className="h-9 w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/50 px-3 text-sm text-slate-700 dark:text-slate-200 outline-none focus:border-violet-300 dark:focus:border-violet-500/40 focus:ring-2 focus:ring-violet-500/10 transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      <option value="custom">自定义</option>
                      <option value="builtin">内置</option>
                    </select>
                  </div>
                </div>

                {/* Content / System Prompt */}
                <div>
                  <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-400">方法规则</label>
                  <textarea
                    value={form.content}
                    onChange={(e) => setForm({ ...form, content: e.target.value })}
                    rows={10} placeholder="描述这个方法如何影响专家判断..."
                    className="w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/50 px-3 py-2 text-sm text-slate-700 dark:text-slate-200 placeholder:text-slate-400 outline-none focus:border-violet-300 dark:focus:border-violet-500/40 focus:ring-2 focus:ring-violet-500/10 resize-y font-mono transition-all duration-200"
                  />
                </div>
              </div>

              {/* Footer */}
              <div className="px-6 py-4 border-t border-slate-100 dark:border-slate-800/60 flex items-center justify-between bg-slate-50/50 dark:bg-slate-900/50">
                <div className="flex items-center gap-2">
                  <Button variant="outline" size="sm" onClick={deleteEditingSkill}
                    className={`gap-1.5 cursor-pointer h-8 ${
                      dialogMode === "edit" && editingId
                        ? "text-red-600 hover:text-red-700 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-500/10 border-red-200 dark:border-red-500/30"
                        : "opacity-0 pointer-events-none"
                    }`}>
                    <Trash2 size={13} />删除
                  </Button>
                </div>
                <div className="flex items-center gap-2">
                  <Button variant="outline" size="sm" onClick={() => setDialogOpen(false)} className="cursor-pointer h-8">取消</Button>
                  <Button size="sm" onClick={handleSave} disabled={saving || (dialogMode === "create" && (!form.name || !form.display_name || !form.content))} className="gap-1.5 cursor-pointer h-8">
                    {saving ? <Loader2 size={13} className="animate-spin" /> : dialogMode === "create" ? <Plus size={13} /> : null}
                    {saving ? "保存中..." : dialogMode === "create" ? "创建方法" : "保存修改"}
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
