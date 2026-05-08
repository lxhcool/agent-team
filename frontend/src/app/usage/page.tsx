"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Activity, Box, DollarSign } from "lucide-react";
import { TopNav } from "../components/topnav";

type UsageStats = {
  total_calls: number;
  total_tokens: number;
  total_cost_usd: number;
  by_provider: { provider: string; calls: number; tokens: number; cost_usd: number }[];
  by_model: { model: string; calls: number; tokens: number; cost_usd: number }[];
  by_agent: { agent: string; calls: number; tokens: number; cost_usd: number }[];
  recent_sessions: { id: string; title: string; calls: number; tokens: number; cost_usd: number }[];
};

export default function UsagePage() {
  const [stats, setStats] = useState<UsageStats | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch("/api/usage").then((r) => r.json()).then((data) => setStats(data)).catch(() => {}).finally(() => setLoading(false));
  }, []);

  const fmtCost = (v: number) => v < 0.01 ? `$${v.toFixed(4)}` : `$${v.toFixed(2)}`;
  const fmtTokens = (v: number) => v >= 1_000_000 ? `${(v / 1_000_000).toFixed(1)}M` : v >= 1_000 ? `${(v / 1_000).toFixed(1)}K` : `${v}`;

  if (loading) {
    return (
      <div className="min-h-screen">
        <TopNav />
        <main className="flex items-center justify-center py-20"><div className="flex gap-1.5"><span className="typing-dot" /><span className="typing-dot" /><span className="typing-dot" /></div></main>
      </div>
    );
  }

  if (!stats) {
    return (
      <div className="min-h-screen">
        <TopNav />
        <main className="min-w-0 pt-14">
          <div className="mx-auto max-w-5xl px-6 py-8">
            <div className="mb-6">
              <h1 className="text-2xl font-semibold tracking-tight">用量统计</h1>
              <p className="mt-1 text-sm text-[var(--muted)]">查看 API 调用和费用概览</p>
            </div>
            <div className="card py-16 text-center"><p className="text-sm text-[var(--muted)]">暂无用量数据</p></div>
          </div>
        </main>
      </div>
    );
  }

  return (
    <div className="min-h-screen">
      <TopNav />
      <main className="min-w-0 pt-14">
        <div className="mx-auto max-w-5xl px-6 py-8">
          <div className="mb-6">
            <h1 className="text-2xl font-semibold tracking-tight">用量统计</h1>
            <p className="mt-1 text-sm text-[var(--muted)]">查看 API 调用和费用概览</p>
          </div>

          <div className="grid gap-3 sm:grid-cols-3 mb-6">
            <div className="card p-4">
              <div className="flex items-center gap-2 mb-2">
                <div className="flex size-7 items-center justify-center rounded-md bg-[var(--accent-soft)] text-[var(--accent)]"><Activity size={14} /></div>
                <span className="text-xs text-[var(--muted)]">总调用次数</span>
              </div>
              <p className="text-xl font-semibold">{stats.total_calls.toLocaleString()}</p>
            </div>
            <div className="card p-4">
              <div className="flex items-center gap-2 mb-2">
                <div className="flex size-7 items-center justify-center rounded-md bg-[var(--accent-soft)] text-[var(--accent)]"><Box size={14} /></div>
                <span className="text-xs text-[var(--muted)]">总 Token 数</span>
              </div>
              <p className="text-xl font-semibold">{fmtTokens(stats.total_tokens)}</p>
            </div>
            <div className="card p-4">
              <div className="flex items-center gap-2 mb-2">
                <div className="flex size-7 items-center justify-center rounded-md bg-[var(--accent-soft)] text-[var(--accent)]"><DollarSign size={14} /></div>
                <span className="text-xs text-[var(--muted)]">总费用</span>
              </div>
              <p className="text-xl font-semibold">{fmtCost(stats.total_cost_usd)}</p>
            </div>
          </div>

          {stats.by_provider && stats.by_provider.length > 0 && (
            <div className="mb-5">
              <h2 className="mb-2 text-sm font-semibold">按 Provider 分组</h2>
              <div className="card overflow-hidden">
                <table className="w-full text-xs">
                  <thead><tr className="border-b border-[var(--card-border)]">
                    <th className="text-left px-4 py-2.5 text-[var(--muted)] font-medium">Provider</th>
                    <th className="text-right px-4 py-2.5 text-[var(--muted)] font-medium">调用</th>
                    <th className="text-right px-4 py-2.5 text-[var(--muted)] font-medium">Token</th>
                    <th className="text-right px-4 py-2.5 text-[var(--muted)] font-medium">费用</th>
                  </tr></thead>
                  <tbody>{stats.by_provider.map((row, i) => (
                    <tr key={i} className={i < stats.by_provider.length - 1 ? "border-b border-[var(--card-border)]" : ""}>
                      <td className="px-4 py-2 font-medium">{row.provider}</td>
                      <td className="px-4 py-2 text-right text-[var(--muted)]">{row.calls.toLocaleString()}</td>
                      <td className="px-4 py-2 text-right text-[var(--muted)]">{fmtTokens(row.tokens)}</td>
                      <td className="px-4 py-2 text-right">{fmtCost(row.cost_usd)}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            </div>
          )}

          {stats.by_model && stats.by_model.length > 0 && (
            <div className="mb-5">
              <h2 className="mb-2 text-sm font-semibold">按 Model 分组</h2>
              <div className="card overflow-hidden">
                <table className="w-full text-xs">
                  <thead><tr className="border-b border-[var(--card-border)]">
                    <th className="text-left px-4 py-2.5 text-[var(--muted)] font-medium">Model</th>
                    <th className="text-right px-4 py-2.5 text-[var(--muted)] font-medium">调用</th>
                    <th className="text-right px-4 py-2.5 text-[var(--muted)] font-medium">Token</th>
                    <th className="text-right px-4 py-2.5 text-[var(--muted)] font-medium">费用</th>
                  </tr></thead>
                  <tbody>{stats.by_model.map((row, i) => (
                    <tr key={i} className={i < stats.by_model.length - 1 ? "border-b border-[var(--card-border)]" : ""}>
                      <td className="px-4 py-2 font-medium">{row.model}</td>
                      <td className="px-4 py-2 text-right text-[var(--muted)]">{row.calls.toLocaleString()}</td>
                      <td className="px-4 py-2 text-right text-[var(--muted)]">{fmtTokens(row.tokens)}</td>
                      <td className="px-4 py-2 text-right">{fmtCost(row.cost_usd)}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            </div>
          )}

          {stats.by_agent && stats.by_agent.length > 0 && (
            <div className="mb-5">
              <h2 className="mb-2 text-sm font-semibold">按 Agent 分组</h2>
              <div className="card overflow-hidden">
                <table className="w-full text-xs">
                  <thead><tr className="border-b border-[var(--card-border)]">
                    <th className="text-left px-4 py-2.5 text-[var(--muted)] font-medium">Agent</th>
                    <th className="text-right px-4 py-2.5 text-[var(--muted)] font-medium">调用</th>
                    <th className="text-right px-4 py-2.5 text-[var(--muted)] font-medium">Token</th>
                    <th className="text-right px-4 py-2.5 text-[var(--muted)] font-medium">费用</th>
                  </tr></thead>
                  <tbody>{stats.by_agent.map((row, i) => (
                    <tr key={i} className={i < stats.by_agent.length - 1 ? "border-b border-[var(--card-border)]" : ""}>
                      <td className="px-4 py-2 font-medium">{row.agent}</td>
                      <td className="px-4 py-2 text-right text-[var(--muted)]">{row.calls.toLocaleString()}</td>
                      <td className="px-4 py-2 text-right text-[var(--muted)]">{fmtTokens(row.tokens)}</td>
                      <td className="px-4 py-2 text-right">{fmtCost(row.cost_usd)}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            </div>
          )}

          {stats.recent_sessions && stats.recent_sessions.length > 0 && (
            <div className="mb-5">
              <h2 className="mb-2 text-sm font-semibold">近期会话用量</h2>
              <div className="card overflow-hidden">
                <table className="w-full text-xs">
                  <thead><tr className="border-b border-[var(--card-border)]">
                    <th className="text-left px-4 py-2.5 text-[var(--muted)] font-medium">会话</th>
                    <th className="text-right px-4 py-2.5 text-[var(--muted)] font-medium">调用</th>
                    <th className="text-right px-4 py-2.5 text-[var(--muted)] font-medium">Token</th>
                    <th className="text-right px-4 py-2.5 text-[var(--muted)] font-medium">费用</th>
                  </tr></thead>
                  <tbody>{stats.recent_sessions.map((row, i) => (
                    <tr key={i} className={i < stats.recent_sessions.length - 1 ? "border-b border-[var(--card-border)]" : ""}>
                      <td className="px-4 py-2"><span className="font-medium">{row.title || row.id}</span></td>
                      <td className="px-4 py-2 text-right text-[var(--muted)]">{row.calls.toLocaleString()}</td>
                      <td className="px-4 py-2 text-right text-[var(--muted)]">{fmtTokens(row.tokens)}</td>
                      <td className="px-4 py-2 text-right">{fmtCost(row.cost_usd)}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
