"use client";

import { useEffect, useState, useCallback } from "react";
import { Plus, Settings, CheckCircle2, AlertCircle, Loader2, Key, Trash2, Wifi, Download } from "lucide-react";
import { TopNav } from "../../components/topnav";
import { useAuth } from "@/lib/auth";
import { useConfirm } from "@/components/ui/confirm-dialog";
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

type ModelSettingsState = {
  default_model: string | null;
  default_model_resolved?: string | null;
  default_provider?: string | null;
  planning_model?: string | null;
  execution_model?: string | null;
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

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null;

const formatFetchModelsError = (status: number, payload: unknown) => {
  if (isRecord(payload)) {
    const detail = payload.detail;
    if (typeof detail === "string") return detail;
    if (isRecord(detail)) {
      const message = typeof detail.message === "string" ? detail.message : "Provider API 返回错误";
      const providerStatus = typeof detail.provider_status === "number" ? detail.provider_status : null;
      return providerStatus ? `${message} (${providerStatus})` : message;
    }
  }
  return `请求失败 (${status})`;
};

export default function ModelSettingsPage() {
  const { confirm, ConfirmDialog } = useConfirm();
  const { loading: authLoading } = useAuth();
  const [providers, setProviders] = useState<Provider[]>([]);
  const [modelSettings, setModelSettings] = useState<ModelSettingsState | null>(null);
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
      setModelSettings(data.settings || null);
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
    if (!await confirm({ description: "确定删除此 Provider？", variant: "destructive" })) return;
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
      const data = await res.json().catch(() => null);
      if (!res.ok) {
        setTestResult((prev) => ({ ...prev, [providerName]: `✗ ${formatFetchModelsError(res.status, data)}` }));
        return;
      }

      const models = Array.isArray(data?.models) ? data.models : [];
      if (models.length === 0) {
        setTestResult((prev) => ({ ...prev, [providerName]: "✗ 未返回模型列表" }));
        return;
      }

      const modelConfigs = models.map((m: { id: string; name: string }) => ({ model_id: m.id, display_name: m.name || m.id }));
      const provider = providers.find((p) => p.provider_name === providerName);
      const defaultModel = provider?.default_model || modelConfigs[0].model_id;
      await fetch(`/api/settings/models/providers/${providerName}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ models: modelConfigs, default_model: defaultModel }) });
      await fetchConfig();
      setTestResult((prev) => ({ ...prev, [providerName]: `✓ 已拉取 ${modelConfigs.length} 个模型` }));
    } catch {
      setTestResult((prev) => ({ ...prev, [providerName]: "✗ 请求失败" }));
    } finally {
      setFetchingModels(null);
      setTimeout(() => setTestResult((prev) => { const n = { ...prev }; delete n[providerName]; return n; }), 5000);
    }
  };

  const updateDefaultModel = async (providerName: string, modelId: string) => {
    await fetch(`/api/settings/models/providers/${providerName}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ default_model: modelId }),
    });
    await fetchConfig();
  };

  const modelOptions = providers.flatMap((p) => {
    if (!p.enabled || !p.has_api_key) return [];
    const models = p.models.length > 0
      ? p.models
      : p.default_model
        ? [{ model_id: p.default_model, display_name: p.default_model, context_window: 32768 }]
        : [];
    return models.map((m) => ({
      value: `${p.provider_name}/${m.model_id}`,
      providerName: p.provider_name,
      providerDisplay: p.display_name || p.provider_name,
      modelId: m.model_id,
      modelDisplay: m.display_name || m.model_id,
    }));
  });

  const activeDefaultValue = (() => {
    const raw = modelSettings?.default_model || "";
    if (raw && modelOptions.some((item) => item.value === raw)) return raw;
    const resolvedProvider = modelSettings?.default_provider;
    const resolvedModel = modelSettings?.default_model_resolved || raw;
    if (resolvedProvider && resolvedModel) {
      const value = `${resolvedProvider}/${resolvedModel}`;
      if (modelOptions.some((item) => item.value === value)) return value;
    }
    if (raw) {
      const matched = modelOptions.find((item) => item.modelId === raw);
      if (matched) return matched.value;
    }
    return "";
  })();

  const activeDefault = modelOptions.find((item) => item.value === activeDefaultValue);

  const updateGlobalDefaultModel = async (value: string) => {
    await fetch("/api/settings/models", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        default_model: value,
        planning_model: value,
        execution_model: value,
      }),
    });
    await fetchConfig();
  };

  if (authLoading || loading) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-indigo-50/30 dark:from-slate-950 dark:via-slate-950 dark:to-indigo-950/20">
        <TopNav />
        <main className="flex items-center justify-center py-20"><div className="flex gap-1.5"><span className="typing-dot" /><span className="typing-dot" /><span className="typing-dot" /></div></main>
      </div>
    );
  }

  const hasAnyKey = providers.some((p) => p.has_api_key);

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-indigo-50/30 dark:from-slate-950 dark:via-slate-950 dark:to-indigo-950/20">
      <TopNav />
      <main className="min-w-0 pt-14">
        <div className="mx-auto max-w-4xl px-6 py-8">
          {/* Header */}
          <div className="mb-6 flex items-center justify-between">
            <div>
              <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight text-slate-900 dark:text-slate-100">
                <div className="flex size-8 items-center justify-center rounded-lg bg-indigo-100 dark:bg-indigo-500/15">
                  <Settings size={16} className="text-indigo-600 dark:text-indigo-400" />
                </div>
                模型配置
              </h1>
              <p className="mt-1.5 text-sm text-slate-500 dark:text-slate-400">配置 API Key 后即可使用，系统会自动使用对应 Provider 的默认模型</p>
            </div>
            {hasAnyKey ? (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-green-50 dark:bg-green-500/10 px-3 py-1.5 text-xs font-semibold text-green-600 dark:text-green-400">
                <CheckCircle2 size={11} />已配置
              </span>
            ) : (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-50 dark:bg-amber-500/10 px-3 py-1.5 text-xs font-semibold text-amber-600 dark:text-amber-400">
                <AlertCircle size={11} />未配置 API Key
              </span>
            )}
          </div>

          <div className="mb-4 rounded-xl bg-white/80 dark:bg-slate-900/80 backdrop-blur-sm ring-1 ring-indigo-200/70 dark:ring-indigo-500/20 p-4">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h2 className="text-sm font-semibold text-slate-800 dark:text-slate-100">当前默认使用模型</h2>
                <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                  {activeDefault
                    ? `${activeDefault.providerDisplay} / ${activeDefault.modelDisplay}`
                    : "还没有选择全局默认模型。选择后规划、阶段生成和执行默认都会使用它。"}
                </p>
              </div>
              <Select
                value={activeDefaultValue}
                onValueChange={(value) => value && updateGlobalDefaultModel(value)}
                disabled={modelOptions.length === 0}
              >
                <SelectTrigger className="h-9 w-full text-xs sm:w-80">
                  <SelectValue placeholder="选择全局默认模型" />
                </SelectTrigger>
                <SelectContent>
                  {modelOptions.map((item) => (
                    <SelectItem key={item.value} value={item.value} className="text-xs">
                      {item.providerDisplay} / {item.modelDisplay}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <p className="mt-3 text-[11px] text-slate-400 dark:text-slate-500">
              Provider 自己的“默认模型”只决定该 Provider 内部优先模型；这里的全局默认决定系统当前实际优先调用哪一个 Provider 和模型。
            </p>
          </div>

          {/* Provider list */}
          <div className="space-y-3">
            {providers.map((p) => {
              const testRes = testResult[p.provider_name];
              const isTesting = testRes === "testing...";
              const testOk = testRes?.startsWith("✓");
              const testFail = testRes?.startsWith("✗");
              return (
                <div key={p.provider_name} className="group rounded-xl bg-white/70 dark:bg-slate-900/70 backdrop-blur-sm ring-1 ring-slate-200/60 dark:ring-slate-700/40 p-4 transition-all duration-200 hover:ring-indigo-300/60 dark:hover:ring-indigo-500/20">
                  {/* Provider header */}
                  <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-2.5">
                      <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-indigo-50 dark:bg-indigo-500/10 text-indigo-600 dark:text-indigo-400">
                        <Key size={14} />
                      </div>
                      <div>
                        <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100">{p.display_name}</h3>
                        {p.has_api_key ? (
                          <span className="text-[11px] text-green-600 dark:text-green-400 flex items-center gap-1"><CheckCircle2 size={9} />{p.masked_api_key}</span>
                        ) : (
                          <span className="rounded-full bg-amber-50 dark:bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400">未配置</span>
                        )}
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      {testRes && (
                        <span className={`text-[11px] font-medium flex items-center gap-1 ${testOk ? "text-green-600 dark:text-green-400" : testFail ? "text-red-500 dark:text-red-400" : "text-slate-500 dark:text-slate-400"}`}>
                          {isTesting && <Loader2 size={10} className="animate-spin" />}
                          {testRes}
                        </span>
                      )}
                      {!p.is_builtin && (
                        <button onClick={() => deleteProvider(p.provider_name)} className="text-[11px] text-slate-400 dark:text-slate-500 hover:text-red-500 dark:hover:text-red-400 cursor-pointer transition-colors">
                          删除
                        </button>
                      )}
                    </div>
                  </div>

                  {/* API Key actions */}
                  <div className="flex flex-wrap items-center gap-2">
                    {editingKey === p.provider_name ? (
                      <>
                        <input type="password" value={keyInput} onChange={(e) => setKeyInput(e.target.value)} placeholder="输入 API Key"
                          className="h-8 w-64 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/50 px-3 text-xs text-slate-700 dark:text-slate-200 outline-none focus:border-indigo-300 dark:focus:border-indigo-500/40 transition-all"
                          onKeyDown={(e) => { if (e.key === "Enter") setApiKey(p.provider_name); if (e.key === "Escape") { setEditingKey(null); setKeyInput(""); } }} autoFocus />
                        <button onClick={() => setApiKey(p.provider_name)} disabled={saving}
                          className="h-8 rounded-lg bg-indigo-600 dark:bg-indigo-500 px-3 text-xs font-medium text-white cursor-pointer hover:bg-indigo-700 dark:hover:bg-indigo-600 disabled:opacity-30 transition-colors">保存</button>
                        <button onClick={() => { setEditingKey(null); setKeyInput(""); }}
                          className="h-8 rounded-lg border border-slate-200 dark:border-slate-700 px-3 text-xs cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors">取消</button>
                      </>
                    ) : (
                      <>
                        <button onClick={() => { setEditingKey(p.provider_name); setKeyInput(""); }}
                          className="h-8 rounded-lg border border-slate-200 dark:border-slate-700 px-3 text-xs cursor-pointer hover:border-indigo-300 dark:hover:border-indigo-500/40 hover:bg-indigo-50 dark:hover:bg-indigo-500/5 transition-all">
                          {p.has_api_key ? "更换 Key" : "设置 Key"}
                        </button>
                        {p.has_api_key && (
                          <>
                            <button onClick={() => testProvider(p.provider_name)} disabled={isTesting}
                              className="h-8 inline-flex items-center gap-1.5 rounded-lg border border-slate-200 dark:border-slate-700 px-3 text-xs cursor-pointer hover:border-indigo-300 dark:hover:border-indigo-500/40 hover:bg-indigo-50 dark:hover:bg-indigo-500/5 disabled:opacity-30 transition-all">
                              <Wifi size={10} />测试
                            </button>
                            <button onClick={() => fetchRemoteModels(p.provider_name)} disabled={fetchingModels === p.provider_name}
                              className="h-8 inline-flex items-center gap-1.5 rounded-lg border border-slate-200 dark:border-slate-700 px-3 text-xs cursor-pointer hover:border-indigo-300 dark:hover:border-indigo-500/40 hover:bg-indigo-50 dark:hover:bg-indigo-500/5 disabled:opacity-30 transition-all">
                              <Download size={10} />{fetchingModels === p.provider_name ? "获取中..." : "拉取模型"}
                            </button>
                          </>
                        )}
                      </>
                    )}
                  </div>

                  {/* Model selector */}
                  {p.models.length > 0 && (
                    <div className="mt-3 flex items-center gap-3 pt-3 border-t border-slate-100 dark:border-slate-800/60">
                      <label className="text-[11px] font-medium text-slate-500 dark:text-slate-400 whitespace-nowrap">默认模型</label>
                      <Select
                        value={p.default_model || ""}
                        onValueChange={(modelId) => modelId != null && updateDefaultModel(p.provider_name, modelId)}
                      >
                        <SelectTrigger className="flex-1 h-8 text-xs">
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
              );
            })}
          </div>

          {/* Add Provider */}
          <div className="mt-4">
            {addingProvider ? (
              <div className="rounded-xl bg-white/70 dark:bg-slate-900/70 backdrop-blur-sm ring-1 ring-slate-200/60 dark:ring-slate-700/40 p-5 space-y-4">
                <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100">添加自定义 Provider</h3>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-400">名称（英文标识）</label>
                    <input type="text" value={newProvider.provider_name} onChange={(e) => setNewProvider({ ...newProvider, provider_name: e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, "") })} placeholder="siliconflow"
                      className="h-9 w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/50 px-3 text-sm text-slate-700 dark:text-slate-200 placeholder:text-slate-400 outline-none focus:border-indigo-300 dark:focus:border-indigo-500/40 transition-all" />
                  </div>
                  <div>
                    <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-400">显示名称</label>
                    <input type="text" value={newProvider.display_name} onChange={(e) => setNewProvider({ ...newProvider, display_name: e.target.value })} placeholder="硅基流动"
                      className="h-9 w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/50 px-3 text-sm text-slate-700 dark:text-slate-200 placeholder:text-slate-400 outline-none focus:border-indigo-300 dark:focus:border-indigo-500/40 transition-all" />
                  </div>
                </div>
                <div>
                  <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-400">Base URL</label>
                  <div className="flex flex-wrap gap-1.5 mb-2">
                    {BASE_URL_PRESETS.map((b) => (
                      <button key={b.label} onClick={() => setNewProvider({ ...newProvider, base_url: b.value })}
                        className={`rounded-lg px-2.5 py-1 text-xs cursor-pointer transition-all ${newProvider.base_url === b.value ? "bg-indigo-600 dark:bg-indigo-500 text-white" : "border border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-400 hover:border-indigo-300 dark:hover:border-indigo-500/40"}`}>{b.label}</button>
                    ))}
                  </div>
                  <input type="text" value={newProvider.base_url} onChange={(e) => setNewProvider({ ...newProvider, base_url: e.target.value })} placeholder="https://api.example.com/v1"
                    className="h-9 w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/50 px-3 text-sm text-slate-700 dark:text-slate-200 placeholder:text-slate-400 outline-none focus:border-indigo-300 dark:focus:border-indigo-500/40 transition-all" />
                </div>
                <div>
                  <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-400">API Key</label>
                  <input type="password" value={newProvider.api_key} onChange={(e) => setNewProvider({ ...newProvider, api_key: e.target.value })} placeholder="sk-..."
                    className="h-9 w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/50 px-3 text-sm text-slate-700 dark:text-slate-200 placeholder:text-slate-400 outline-none focus:border-indigo-300 dark:focus:border-indigo-500/40 transition-all" />
                </div>
                <div className="flex gap-2 pt-1">
                  <button onClick={addCustomProvider} disabled={saving || !newProvider.provider_name || !newProvider.base_url}
                    className="rounded-lg bg-indigo-600 dark:bg-indigo-500 px-4 py-2 text-xs font-medium text-white cursor-pointer hover:bg-indigo-700 dark:hover:bg-indigo-600 disabled:opacity-30 transition-colors">{saving ? "添加中..." : "添加"}</button>
                  <button onClick={() => setAddingProvider(false)} className="rounded-lg border border-slate-200 dark:border-slate-700 px-4 py-2 text-xs cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors">取消</button>
                </div>
              </div>
            ) : (
              <button onClick={() => setAddingProvider(true)}
                className="flex items-center gap-2 rounded-xl border border-dashed border-slate-300 dark:border-slate-700 bg-white/40 dark:bg-slate-900/40 backdrop-blur-sm p-3.5 w-full text-left cursor-pointer hover:border-indigo-300 dark:hover:border-indigo-500/40 hover:bg-indigo-50/50 dark:hover:bg-indigo-950/20 transition-all">
                <Plus size={14} className="text-indigo-500" /><span className="text-sm font-medium text-slate-600 dark:text-slate-300">添加自定义 Provider</span>
              </button>
            )}
          </div>
        </div>
      </main>
      {ConfirmDialog}
    </div>
  );
}
