"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { Bot, Loader2, ArrowRight, Mail, Lock, User, Sparkles } from "lucide-react";

export default function LoginPage() {
  const router = useRouter();
  const { login, register } = useAuth();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      if (mode === "login") {
        await login(username, password);
      } else {
        if (!email) {
          setError("请输入邮箱");
          setSubmitting(false);
          return;
        }
        await register(username, email, password, displayName);
      }
      router.replace("/");
    } catch (err: any) {
      setError(err.message || "操作失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#f8f9fc]">
      <div className="w-full max-w-md px-6">
        {/* Logo */}
        <div className="mb-8 text-center">
          <div className="mx-auto mb-4 flex size-14 items-center justify-center rounded-2xl bg-[#6366f1] shadow-lg shadow-indigo-200">
            <Bot size={28} className="text-white" strokeWidth={2.5} />
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-[#1a1a2e]">Agent Team</h1>
          <p className="mt-1 text-sm text-[#6b7280]">多 Agent 协作系统</p>
        </div>

        {/* Card */}
        <div className="rounded-2xl border border-[#e5e7eb] bg-white p-8 shadow-sm">
          {/* Tabs */}
          <div className="mb-6 flex rounded-xl bg-[#f3f4f6] p-1">
            <button
              onClick={() => { setMode("login"); setError(""); }}
              className={`flex-1 rounded-lg py-2 text-sm font-medium transition cursor-pointer ${
                mode === "login" ? "bg-white text-[#1f2937] shadow-sm" : "text-[#6b7280] hover:text-[#374151]"
              }`}
            >
              登录
            </button>
            <button
              onClick={() => { setMode("register"); setError(""); }}
              className={`flex-1 rounded-lg py-2 text-sm font-medium transition cursor-pointer ${
                mode === "register" ? "bg-white text-[#1f2937] shadow-sm" : "text-[#6b7280] hover:text-[#374151]"
              }`}
            >
              注册
            </button>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            {/* Username */}
            <div>
              <label className="mb-1.5 block text-xs font-medium text-[#374151]">用户名</label>
              <div className="relative">
                <User size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#9ca3af]" />
                <input
                  type="text"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="输入用户名"
                  required
                  minLength={3}
                  className="w-full rounded-lg border border-[#e5e7eb] bg-[#f9fafb] py-2.5 pl-10 pr-4 text-sm outline-none transition focus:border-[#6366f1] focus:bg-white focus:ring-2 focus:ring-indigo-100"
                />
              </div>
            </div>

            {/* Email (register only) */}
            {mode === "register" && (
              <div>
                <label className="mb-1.5 block text-xs font-medium text-[#374151]">邮箱</label>
                <div className="relative">
                  <Mail size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#9ca3af]" />
                  <input
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="your@email.com"
                    required
                    className="w-full rounded-lg border border-[#e5e7eb] bg-[#f9fafb] py-2.5 pl-10 pr-4 text-sm outline-none transition focus:border-[#6366f1] focus:bg-white focus:ring-2 focus:ring-indigo-100"
                  />
                </div>
              </div>
            )}

            {/* Display Name (register only) */}
            {mode === "register" && (
              <div>
                <label className="mb-1.5 block text-xs font-medium text-[#374151]">显示名称（可选）</label>
                <div className="relative">
                  <Sparkles size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#9ca3af]" />
                  <input
                    type="text"
                    value={displayName}
                    onChange={(e) => setDisplayName(e.target.value)}
                    placeholder="你的昵称"
                    className="w-full rounded-lg border border-[#e5e7eb] bg-[#f9fafb] py-2.5 pl-10 pr-4 text-sm outline-none transition focus:border-[#6366f1] focus:bg-white focus:ring-2 focus:ring-indigo-100"
                  />
                </div>
              </div>
            )}

            {/* Password */}
            <div>
              <label className="mb-1.5 block text-xs font-medium text-[#374151]">密码</label>
              <div className="relative">
                <Lock size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#9ca3af]" />
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder={mode === "login" ? "输入密码" : "至少 6 位"}
                  required
                  minLength={6}
                  className="w-full rounded-lg border border-[#e5e7eb] bg-[#f9fafb] py-2.5 pl-10 pr-4 text-sm outline-none transition focus:border-[#6366f1] focus:bg-white focus:ring-2 focus:ring-indigo-100"
                />
              </div>
            </div>

            {/* Error */}
            {error && (
              <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-2.5 text-xs text-red-600">
                {error}
              </div>
            )}

            {/* Submit */}
            <button
              type="submit"
              disabled={submitting || !username || !password || (mode === "register" && !email)}
              className="flex w-full items-center justify-center gap-2 rounded-lg bg-[#6366f1] py-2.5 text-sm font-medium text-white transition hover:bg-[#4f46e5] disabled:opacity-40 cursor-pointer shadow-sm"
            >
              {submitting ? (
                <Loader2 size={16} className="animate-spin" />
              ) : (
                <ArrowRight size={16} />
              )}
              {mode === "login" ? "登录" : "注册"}
            </button>
          </form>
        </div>

        <p className="mt-6 text-center text-xs text-[#9ca3af]">
          Agent Team &copy; {new Date().getFullYear()}
        </p>
      </div>
    </div>
  );
}
