"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, useRef } from "react";
import {
  House, Users,
  Wrench, BarChart3, Settings, Sun, Moon, LogOut, UserCircle,
  ChevronDown, FolderKanban
} from "lucide-react";
import { useAuth } from "@/lib/auth";

const NAV = [
  { href: "/", label: "首页", icon: House, match: ["/"] },
  { href: "/flows", label: "项目流程", icon: FolderKanban, match: ["/flows", "/workspaces"] },
];

const SETTINGS_NAV = [
  { href: "/settings/agents", label: "专家库", icon: Users },
  { href: "/settings/skills", label: "方法库", icon: Wrench },
  { href: "/settings/models", label: "模型设置", icon: Settings },
  { href: "/usage", label: "用量统计", icon: BarChart3 },
];

const ICON = "size-[16px]";

function useClickOutside(refs: React.RefObject<HTMLElement | null>[], fn: () => void, on: boolean) {
  useEffect(() => {
    if (!on) return;
    const h = (e: MouseEvent) => {
      if (!refs.some(r => r.current?.contains(e.target as Node))) fn();
    };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [on, fn, refs]);
}

export function TopNav() {
  const pathname = usePathname();
  const { user, logout } = useAuth();
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [showUser, setShowUser] = useState(false);
  const [showSettings, setShowSettings] = useState(false);

  const sBtn = useRef<HTMLButtonElement>(null);
  const sMenu = useRef<HTMLDivElement>(null);
  const uBtn = useRef<HTMLButtonElement>(null);
  const uMenu = useRef<HTMLDivElement>(null);

  useClickOutside([sBtn, sMenu], () => setShowSettings(false), showSettings);
  useClickOutside([uBtn, uMenu], () => setShowUser(false), showUser);

  useEffect(() => {
    const s = localStorage.getItem("theme");
    const t = s === "light" || s === "dark" ? s : "dark";
    setTheme(t as "dark" | "light");
    document.documentElement.classList.toggle("dark", t === "dark");
    document.documentElement.classList.toggle("light", t === "light");
  }, []);

  const toggleTheme = () => {
    const n = theme === "dark" ? "light" : "dark";
    setTheme(n);
    localStorage.setItem("theme", n);
    document.documentElement.classList.toggle("dark", n === "dark");
    document.documentElement.classList.toggle("light", n === "light");
  };

  const active = (item: { href: string; match?: string[] }) => {
    if (item.match?.length) {
      return item.match.some((prefix) => prefix === "/" ? pathname === "/" : pathname.startsWith(prefix));
    }
    return item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
  };
  const settingsActive = SETTINGS_NAV.some(i => active({ href: i.href }));

  return (
    <header className="desktop-drag-region fixed top-0 left-0 right-0 z-50 border-b border-slate-200/60 dark:border-slate-700/40 bg-white/70 dark:bg-slate-900/70 backdrop-blur-xl">
      <div className="desktop-titlebar-content mx-auto flex h-14 max-w-[1600px] items-center px-6 sm:px-8">
        {/* Logo */}
        <Link href="/" className="flex items-center gap-2.5 shrink-0">
          <img src="/logo.png" alt="" className="size-7 rounded-lg object-cover" />
          <span className="hidden sm:inline text-sm font-bold text-slate-800 dark:text-slate-100">FlowPilot</span>
        </Link>

        {/* Main Nav */}
        <nav className="ml-8 flex items-center gap-1">
          {NAV.map(item => {
            const on = active(item);
            return (
              <Link
                key={`${item.href}-${item.label}`}
                href={item.href}
                className={`flex items-center gap-2 rounded-lg px-3 py-2 text-[13px] font-medium transition-colors duration-150 ${
                  on
                    ? "bg-indigo-50 dark:bg-indigo-500/15 text-indigo-600 dark:text-indigo-300"
                    : "text-slate-500 dark:text-slate-400 hover:bg-slate-100/60 dark:hover:bg-slate-800/60 hover:text-slate-700 dark:hover:text-slate-200"
                }`}
              >
                <item.icon className={ICON} strokeWidth={on ? 2.5 : 2} />
                <span className="hidden md:inline">{item.label}</span>
              </Link>
            );
          })}

          {/* Settings - dropdown */}
          <div className="relative">
            <button
              ref={sBtn}
              onClick={() => setShowSettings(!showSettings)}
              className={`flex items-center gap-1.5 rounded-lg px-3 py-2 text-[13px] font-medium transition-colors duration-150 cursor-pointer ${
                settingsActive || showSettings
                  ? "bg-indigo-50 dark:bg-indigo-500/15 text-indigo-600 dark:text-indigo-300"
                  : "text-slate-500 dark:text-slate-400 hover:bg-slate-100/60 dark:hover:bg-slate-800/60 hover:text-slate-700 dark:hover:text-slate-200"
              }`}
            >
              <Settings className={ICON} strokeWidth={settingsActive ? 2.5 : 2} />
              <span className="hidden md:inline">设置</span>
              <ChevronDown size={12} className={`hidden md:inline transition-transform ${showSettings ? "rotate-180" : ""}`} />
            </button>
            {showSettings && (
              <div
                ref={sMenu}
                className="absolute left-0 top-full mt-1.5 z-50 min-w-[160px] rounded-xl border p-1 bg-white/95 dark:bg-slate-900/95 border-slate-200 dark:border-slate-700 shadow-xl backdrop-blur-xl"
              >
                {SETTINGS_NAV.map(item => {
                  const on = active({ href: item.href });
                  return (
                    <Link
                      key={item.href}
                      href={item.href}
                      onClick={() => setShowSettings(false)}
                      className={`flex items-center gap-2.5 rounded-lg px-3 py-2 text-[13px] transition-colors ${
                        on
                          ? "bg-indigo-50 dark:bg-indigo-500/15 text-indigo-600 dark:text-indigo-300 font-medium"
                          : "text-slate-600 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-800"
                      }`}
                    >
                      <item.icon className={ICON} strokeWidth={on ? 2.5 : 2} />
                      {item.label}
                    </Link>
                  );
                })}
              </div>
            )}
          </div>
        </nav>

        <div className="flex-1" />

        {/* Right side: theme + user */}
        <div className="flex items-center gap-1">
          <button
            onClick={toggleTheme}
            className="flex items-center justify-center rounded-lg size-9 text-slate-500 dark:text-slate-400 hover:bg-slate-100/60 dark:hover:bg-slate-800/60 hover:text-slate-700 dark:hover:text-slate-200 transition-colors cursor-pointer"
          >
            {theme === "dark" ? <Sun className={ICON} /> : <Moon className={ICON} />}
          </button>

          {/* User menu */}
          <div className="relative">
            <button
              ref={uBtn}
              onClick={() => setShowUser(!showUser)}
              className={`flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-[13px] font-medium transition-colors cursor-pointer ${
                showUser
                  ? "bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-200"
                  : "text-slate-500 dark:text-slate-400 hover:bg-slate-100/60 dark:hover:bg-slate-800/60 hover:text-slate-700 dark:hover:text-slate-200"
              }`}
            >
              <UserCircle className={ICON} />
              <span className="hidden sm:inline max-w-[100px] truncate">{user?.display_name || user?.username}</span>
              <ChevronDown size={12} className={`transition-transform ${showUser ? "rotate-180" : ""}`} />
            </button>
            {showUser && (
              <div
                ref={uMenu}
                className="absolute right-0 top-full mt-1.5 z-50 min-w-[200px] rounded-xl border p-1.5 bg-white/95 dark:bg-slate-900/95 border-slate-200 dark:border-slate-700 shadow-xl backdrop-blur-xl"
              >
                <div className="px-3 py-2.5 border-b border-slate-100 dark:border-slate-800 mb-1">
                  <div className="text-sm font-semibold text-slate-800 dark:text-slate-100">{user?.display_name || user?.username}</div>
                  <div className="text-[11px] text-slate-400 mt-0.5">{user?.email}</div>
                </div>
                <button
                  onClick={() => { setShowUser(false); logout(); }}
                  className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-[13px] text-red-500 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-500/10 transition-colors cursor-pointer"
                >
                  <LogOut className={ICON} />
                  退出登录
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
    </header>
  );
}
