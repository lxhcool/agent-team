"use client";

import { useEffect, useState, useCallback } from "react";
import { TopNav } from "../../components/topnav";

type SecurityConfig = {
  safe_mode: boolean;
  command_blacklist: string[];
  protected_paths: string[];
  sensitive_file_patterns: string[];
  max_command_timeout: number;
};

export default function SecurityPage() {
  const [config, setConfig] = useState<SecurityConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  const [form, setForm] = useState({
    safe_mode: false,
    command_blacklist: "",
    protected_paths: "",
    sensitive_file_patterns: "",
    max_command_timeout: 300,
  });

  const fetchConfig = useCallback(async () => {
    try {
      const res = await fetch("/api/settings/security");
      const data = await res.json();
      setConfig(data);
      setForm({
        safe_mode: data.safe_mode ?? false,
        command_blacklist: (data.command_blacklist || []).join("\n"),
        protected_paths: (data.protected_paths || []).join("\n"),
        sensitive_file_patterns: (data.sensitive_file_patterns || []).join("\n"),
        max_command_timeout: data.max_command_timeout ?? 300,
      });
    } catch {} finally { setLoading(false); }
  }, []);

  useEffect(() => { fetchConfig(); }, [fetchConfig]);

  const saveConfig = async () => {
    setSaving(true); setSaved(false);
    try {
      await fetch("/api/settings/security", {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          safe_mode: form.safe_mode,
          command_blacklist: form.command_blacklist.split("\n").filter(Boolean),
          protected_paths: form.protected_paths.split("\n").filter(Boolean),
          sensitive_file_patterns: form.sensitive_file_patterns.split("\n").filter(Boolean),
          max_command_timeout: form.max_command_timeout,
        }),
      });
      setSaved(true); await fetchConfig(); setTimeout(() => setSaved(false), 3000);
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
          <div className="mb-6">
            <h1 className="text-2xl font-semibold tracking-tight">安全配置</h1>
            <p className="mt-1 text-sm text-[var(--muted)]">控制 CLI 执行安全策略</p>
          </div>

          <div className="card p-4 mb-3">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-sm font-semibold">安全模式</h3>
                <p className="text-xs text-[var(--muted)] mt-0.5">启用后，所有命令执行前需人工确认</p>
              </div>
              <button onClick={() => setForm({ ...form, safe_mode: !form.safe_mode })}
                className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors cursor-pointer ${form.safe_mode ? "bg-[var(--accent)]" : "bg-[var(--card-border)]"}`}>
                <span className={`inline-block size-3.5 transform rounded-full bg-white transition-transform ${form.safe_mode ? "translate-x-5" : "translate-x-0.5"}`} />
              </button>
            </div>
          </div>

          <div className="card p-4 mb-3 space-y-3">
            <div><h3 className="text-sm font-semibold">命令黑名单</h3><p className="text-xs text-[var(--muted)] mt-0.5">禁止执行的命令，每行一条</p></div>
            <textarea value={form.command_blacklist} onChange={(e) => setForm({ ...form, command_blacklist: e.target.value })} rows={4}
              placeholder={"rm -rf /\nformat\ndel /s /q"}
              className="w-full rounded-md border border-[var(--card-border)] bg-[var(--surface)] px-3 py-2 text-xs outline-none focus:border-[var(--accent)] resize-y font-mono" />
          </div>

          <div className="card p-4 mb-3 space-y-3">
            <div><h3 className="text-sm font-semibold">受保护路径</h3><p className="text-xs text-[var(--muted)] mt-0.5">禁止写入或删除的路径，每行一个</p></div>
            <textarea value={form.protected_paths} onChange={(e) => setForm({ ...form, protected_paths: e.target.value })} rows={4}
              placeholder={"/etc/passwd\n/home\n/env"}
              className="w-full rounded-md border border-[var(--card-border)] bg-[var(--surface)] px-3 py-2 text-xs outline-none focus:border-[var(--accent)] resize-y font-mono" />
          </div>

          <div className="card p-4 mb-3 space-y-3">
            <div><h3 className="text-sm font-semibold">敏感文件模式</h3><p className="text-xs text-[var(--muted)] mt-0.5">匹配到的文件不可读取或修改，每行一个 glob 模式</p></div>
            <textarea value={form.sensitive_file_patterns} onChange={(e) => setForm({ ...form, sensitive_file_patterns: e.target.value })} rows={4}
              placeholder={"*.env\n**/.ssh/*\n**/credentials*"}
              className="w-full rounded-md border border-[var(--card-border)] bg-[var(--surface)] px-3 py-2 text-xs outline-none focus:border-[var(--accent)] resize-y font-mono" />
          </div>

          <div className="card p-4 mb-5 space-y-3">
            <div><h3 className="text-sm font-semibold">命令超时上限</h3><p className="text-xs text-[var(--muted)] mt-0.5">单条命令最大执行时间（秒）</p></div>
            <input type="number" value={form.max_command_timeout} onChange={(e) => setForm({ ...form, max_command_timeout: parseInt(e.target.value) || 0 })} min={1} max={3600}
              className="w-full rounded-md border border-[var(--card-border)] bg-[var(--surface)] px-3 py-2 text-xs outline-none focus:border-[var(--accent)]" />
          </div>

          <div className="flex items-center gap-3">
            <button onClick={saveConfig} disabled={saving}
              className="rounded-md bg-[var(--accent)] px-5 py-2 text-xs font-medium text-white cursor-pointer hover:bg-[var(--accent-hover)] disabled:opacity-30 transition">{saving ? "保存中..." : "保存配置"}</button>
            {saved && <span className="text-xs text-[var(--success)]">✓ 已保存</span>}
          </div>
        </div>
      </main>
    </div>
  );
}
