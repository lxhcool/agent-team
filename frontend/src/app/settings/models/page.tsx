"use client";

import { useEffect, useState, useCallback } from "react";
import { Plus } from "lucide-react";
import { TopNav } from "../../components/topnav";
import { useAuth } from "@/lib/auth";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

type ModelConfig = {
  model_id: string; display_name: string; context_window: number;
  pricing?: { prompt_per_million: number; completion_per_million: number; currency: string };
};

type Provider = {
  provider_name: string; display_name: string; api_type: string;
  base_url: string | null; has_api_key: boolean; masked_api_key: string | null;
  models: ModelConfig[]; default_model: string | null;
  is_builtin: boolean; enabled: boolean;
};

const BASE_URL_PRESETS = [
  { label: "OpenAI", value: "https://api.openai.com/v1" },
  { label: "DeepSeek", value: "https://api.deepseek.com/v1" },
  { label: "硅基流动", value: "https://api.siliconflow.cn/v1" },
  { label: "Groq", value: "https://api.groq.com/openai/v1" },
  { label: "OpenRouter", value: "https://openrouter.ai/api/v1" },
  { label: "月之暗面", value: "https://api.moonshot.cn/v1" },
  { label: "Ollama (本地)", value: "http://localhost:11434/v1" },
];

export default function ModelSettingsPage() {
  const { loading: authLoading } = useAuth();
  const [providers, setProviders] = useState<Provider[]>([]);
  const [loading, setLoading] = useState(true);
  const [addingProvider, setAddingProvider] = useState(false);
  const [newProvider, setNewProvider] = useState({ provider_name: "", display_name: "", base_url: "", api_key: "" });
  const [saving, setSaving] = useState(false);
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [keyInput, setKeyInput] = useState("");
  const [testResult, setTestResult] = useState<Record<string, string>>({});
  const [fetchingModels, setFetchingModels] = useState<string | null>(null);

  const fetchConfig = useCallback(async () => {
    try {
      const res = await fetch("/api/settings/models");
      const data = await res.json();
      setProviders(data.providers || []);
    } catch {} finally { setLoading(false); }
  }, []);

  useEffect(() => { fetchConfig(); }, [fetchConfig]);

  const setApiKey = async (providerName: string) => {
    if (!keyInput.trim()) return;
    setSaving(true);
    try {
      await fetch(`/api/settings/models/providers/${providerName}/api-key`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ api_key: keyInput.trim() }) });
      setEditingKey(null); setKeyInput(""); await fetchConfig();
    } catch {} finally { setSaving(false); }
  };

  const addCustomProvider = async () => {
    if (!newProvider.provider_name || !newProvider.display_name || !newProvider.base_url) return;
    setSaving(true);
    try {
      await fetch("/api/settings/models/providers", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(newProvider) });
      setNewProvider({ provider_name: "", display_name: "", base_url: "", api_key: "" }); setAddingProvider(false); await fetchConfig();
    } catch {} finally { setSaving(false); }
  };

  const deleteProvider = async (providerName: string) => {
    if (!confirm("确定删除此 Provider？")) return;
    try { await fetch(`/api/settings/models/providers/${providerName}`, { method: "DELETE" }); await fetchConfig(); } catch {}
  };

  const testProvider = async (providerName: string) => {
    setTestResult((prev) => ({ ...prev, [providerName]: "testing..." }));
    try {
      const res = await fetch(`/api/settings/models/test?provider_name=${providerName}`, { method: "POST" });
      const data = await res.json();
      setTestResult((prev) => ({ ...prev, [providerName]: data.success ? `✓ ${data.message || "连接成功"}` : `✗ ${data.error}` }));
    } catch { setTestResult((prev) => ({ ...prev, [providerName]: "✗ 请求失败" })); }
    setTimeout(() => setTestResult((prev) => { const n = { ...prev }; delete n[providerName]; return n; }), 5000);
  };

  const fetchRemoteModels = async (providerName: string) => {
    setFetchingModels(providerName);
    try {
      const res = await fetch(`/api/settings/models/providers/${providerName}/models`);
      if (res.ok) {
        const data = await res.json();
        const models = data.models || [];
        if (models.length > 0) {
          const modelConfigs = models.map((m: { id: string; name: string }) => ({ model_id: m.id, display_name: m.name || m.id }));
          const provider = providers.find((p) => p.provider_name === providerName);
          const defaultModel = provider?.default_model || modelConfigs[0].model_id;
          await fetch(`/api/settings/models/providers/${providerName}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ models: modelConfigs, default_model: defaultModel }) });
          await fetchConfig();
        }
      }
    } catch {} finally { setFetchingModels(null); }
  };

  const updateDefaultModel = async (providerName: string, modelId: string) => {
    await fetch(`/api/settings/models/providers/${providerName}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ default_model: modelId }),
    });
    await fetchConfig();
  };

  if (authLoading || loading) {
    return (
      <div className="min-h-screen">
        <TopNav />
        <main className="flex items-center justify-center py-20"><div className="flex gap-1.5"><span className="typing-dot" /><span className="typing-dot" /><span className="typing-dot" /></div></main>
      </div>
    );
  }

  const hasAnyKey = providers.some((p) => p.has_api_key);

  return (
    <div className="min-h-screen">
      <TopNav />
      <main className="min-w-0 pt-14 overflow-x-auto">
        <div className="mx-auto max-w-4xl px-6 py-8">
          <div className="mb-6 flex items-center justify-between">
            <div>
              <h1 className="text-2xl font-semibold tracking-tight">模型配置</h1>
              <p className="mt-1 text-sm text-[var(--muted)]">配置 API Key 后即可使用，系统会自动使用对应 Provider 的默认模型</p>
            </div>
            {hasAnyKey ? <span className="text-xs text-[var(--success)]">✓ 已配置</span> : <span className="text-xs text-[var(--warning)]">⚠ 未配置 API Key</span>}
          </div>

          <div className="space-y-3">
            {providers.map((p) => (
              <div key={p.provider_name} className="card p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <h3 className="text-sm font-semibold">{p.display_name}</h3>
                    {p.has_api_key ? (
                      <span className="text-xs text-[var(--success)]">✓ {p.masked_api_key}</span>
                    ) : (
                      <span className="rounded-full bg-[var(--warning)]/10 px-2 py-0.5 text-xs text-[var(--warning)]">未配置</span>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    {testResult[p.provider_name] && <span className="text-xs">{testResult[p.provider_name]}</span>}
                    {!p.is_builtin && <button onClick={() => deleteProvider(p.provider_name)} className="text-xs text-[var(--danger)] hover:underline cursor-pointer">删除</button>}
                  </div>
                </div>

                <div className="flex flex-wrap items-center gap-2">
                  {editingKey === p.provider_name ? (
                    <>
                      <input type="password" value={keyInput} onChange={(e) => setKeyInput(e.target.value)} placeholder="输入 API Key"
                        className="w-64 rounded-md border border-[var(--card-border)] bg-[var(--surface)] px-3 py-2 text-xs outline-none focus:border-[var(--accent)]"
                        onKeyDown={(e) => { if (e.key === "Enter") setApiKey(p.provider_name); if (e.key === "Escape") { setEditingKey(null); setKeyInput(""); } }} autoFocus />
                      <button onClick={() => setApiKey(p.provider_name)} disabled={saving}
                        className="rounded-md bg-[var(--accent)] px-3 py-2 text-xs text-white cursor-pointer hover:bg-[var(--accent-hover)] disabled:opacity-30">保存</button>
                      <button onClick={() => { setEditingKey(null); setKeyInput(""); }}
                        className="rounded-md border border-[var(--card-border)] px-3 py-2 text-xs cursor-pointer">取消</button>
                    </>
                  ) : (
                    <>
                      <button onClick={() => { setEditingKey(p.provider_name); setKeyInput(""); }}
                        className="rounded-md border border-[var(--card-border)] px-3 py-2 text-xs cursor-pointer hover:border-[var(--accent)] transition">
                        {p.has_api_key ? "更换 Key" : "设置 Key"}
                      </button>
                      {p.has_api_key && (
                        <>
                          <button onClick={() => testProvider(p.provider_name)}
                            className="rounded-md border border-[var(--card-border)] px-3 py-2 text-xs cursor-pointer hover:border-[var(--accent)] transition">测试</button>
                          <button onClick={() => fetchRemoteModels(p.provider_name)} disabled={fetchingModels === p.provider_name}
                            className="rounded-md border border-[var(--card-border)] px-3 py-2 text-xs cursor-pointer hover:border-[var(--accent)] disabled:opacity-30 transition">
                            {fetchingModels === p.provider_name ? "获取中..." : "拉取模型"}
                          </button>
                        </>
                      )}
                    </>
                  )}
                </div>

                {p.models.length > 0 && (
                  <div className="flex items-center gap-3">
                    <label className="text-xs font-medium text-[var(--muted)] whitespace-nowrap">使用模型</label>
                    <Select
                      value={p.default_model || ""}
                      onValueChange={(modelId) => updateDefaultModel(p.provider_name, modelId)}
                    >
                      <SelectTrigger className="flex-1 h-8 text-xs bg-[var(--surface)] border-[var(--card-border)]">
                        <SelectValue placeholder="选择模型" />
                      </SelectTrigger>
                      <SelectContent>
                        {p.models.map((m) => (
                          <SelectItem key={m.model_id} value={m.model_id} className="text-xs">{m.display_name}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                )}
              </div>
            ))}
          </div>

          <div className="mt-4">
            {addingProvider ? (
              <div className="card p-5 space-y-3">
                <h3 className="text-sm font-semibold">添加自定义 Provider</h3>
                <div><label className="mb-1 block text-xs font-medium text-[var(--muted)]">名称（英文标识）</label>
                  <input type="text" value={newProvider.provider_name} onChange={(e) => setNewProvider({ ...newProvider, provider_name: e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, "") })} placeholder="siliconflow"
                    className="w-full rounded-md border border-[var(--card-border)] bg-[var(--surface)] px-3 py-2 text-xs outline-none focus:border-[var(--accent)]" /></div>
                <div><label className="mb-1 block text-xs font-medium text-[var(--muted)]">显示名称</label>
                  <input type="text" value={newProvider.display_name} onChange={(e) => setNewProvider({ ...newProvider, display_name: e.target.value })} placeholder="硅基流动"
                    className="w-full rounded-md border border-[var(--card-border)] bg-[var(--surface)] px-3 py-2 text-xs outline-none focus:border-[var(--accent)]" /></div>
                <div><label className="mb-1 block text-xs font-medium text-[var(--muted)]">Base URL</label>
                  <div className="flex flex-wrap gap-1 mb-2">
                    {BASE_URL_PRESETS.map((b) => (
                      <button key={b.label} onClick={() => setNewProvider({ ...newProvider, base_url: b.value })}
                        className={`rounded-md px-2 py-1 text-xs cursor-pointer transition ${newProvider.base_url === b.value ? "bg-[var(--accent)] text-white" : "border border-[var(--card-border)] hover:border-[var(--accent)]"}`}>{b.label}</button>
                    ))}
                  </div>
                  <input type="text" value={newProvider.base_url} onChange={(e) => setNewProvider({ ...newProvider, base_url: e.target.value })} placeholder="https://api.example.com/v1"
                    className="w-full rounded-md border border-[var(--card-border)] bg-[var(--surface)] px-3 py-2 text-xs outline-none focus:border-[var(--accent)]" /></div>
                <div><label className="mb-1 block text-xs font-medium text-[var(--muted)]">API Key</label>
                  <input type="password" value={newProvider.api_key} onChange={(e) => setNewProvider({ ...newProvider, api_key: e.target.value })} placeholder="sk-..."
                    className="w-full rounded-md border border-[var(--card-border)] bg-[var(--surface)] px-3 py-2 text-xs outline-none focus:border-[var(--accent)]" /></div>
                <div className="flex gap-2">
                  <button onClick={addCustomProvider} disabled={saving || !newProvider.provider_name || !newProvider.base_url}
                    className="rounded-md bg-[var(--accent)] px-4 py-2 text-xs font-medium text-white cursor-pointer hover:bg-[var(--accent-hover)] disabled:opacity-30 transition">{saving ? "添加中..." : "添加"}</button>
                  <button onClick={() => setAddingProvider(false)} className="rounded-md border border-[var(--card-border)] px-4 py-2 text-xs cursor-pointer hover:bg-[var(--surface-elevated)] transition">取消</button>
                </div>
              </div>
            ) : (
              <button onClick={() => setAddingProvider(true)}
                className="flex items-center gap-2 rounded-md border border-dashed border-[var(--card-border)] p-3 w-full text-left cursor-pointer hover:border-[var(--accent)] hover:bg-[var(--accent-soft)] transition-all">
                <Plus size={14} className="text-[var(--accent)]" /><span className="text-sm font-medium">添加自定义 Provider</span>
              </button>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
