"use client";

import { useState, useCallback, useRef } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { AlertTriangle, HelpCircle } from "lucide-react";

type ConfirmOptions = {
  title?: string;
  description: string;
  confirmText?: string;
  cancelText?: string;
  variant?: "default" | "destructive";
};

type ConfirmState = {
  open: boolean;
  options: ConfirmOptions;
  resolver: ((value: boolean) => void) | null;
};

const defaultState: ConfirmState = {
  open: false,
  options: { description: "" },
  resolver: null,
};

export function useConfirm() {
  const [state, setState] = useState<ConfirmState>(defaultState);
  const resolverRef = useRef<((value: boolean) => void) | null>(null);

  const confirm = useCallback((options: ConfirmOptions): Promise<boolean> => {
    return new Promise((resolve) => {
      resolverRef.current = resolve;
      setState({ open: true, options, resolver: resolve });
    });
  }, []);

  const handleConfirm = useCallback(() => {
    resolverRef.current?.(true);
    resolverRef.current = null;
    setState((prev) => ({ ...prev, open: false }));
  }, []);

  const handleCancel = useCallback(() => {
    resolverRef.current?.(false);
    resolverRef.current = null;
    setState((prev) => ({ ...prev, open: false }));
  }, []);

  const isDestructive = state.options.variant === "destructive";

  const ConfirmDialog = (
    <Dialog open={state.open} onOpenChange={(open) => { if (!open) handleCancel(); }}>
      <DialogContent
        showCloseButton={false}
        className="sm:max-w-[400px] p-0 overflow-hidden border-0 shadow-2xl"
      >
        {/* Glassmorphism card body */}
        <div className="bg-white/80 dark:bg-slate-900/80 backdrop-blur-xl">
          <div className="px-5 pt-5 pb-4">
            <DialogHeader className="flex flex-row items-start gap-3 space-y-0">
              {/* Icon with gradient background */}
              <div
                className={`flex size-9 shrink-0 items-center justify-center rounded-xl shadow-sm ${
                  isDestructive
                    ? "bg-gradient-to-br from-red-500 to-rose-600 text-white"
                    : "bg-gradient-to-br from-indigo-500 to-violet-600 text-white"
                }`}
              >
                {isDestructive ? (
                  <AlertTriangle size={18} strokeWidth={2.5} />
                ) : (
                  <HelpCircle size={18} strokeWidth={2.5} />
                )}
              </div>
              <div className="flex-1 min-w-0 pt-0.5">
                <DialogTitle className="text-[15px] font-bold leading-snug tracking-tight">
                  {state.options.title || "确认操作"}
                </DialogTitle>
                <DialogDescription className="mt-1 text-[13px] leading-relaxed text-slate-500 dark:text-slate-400">
                  {state.options.description}
                </DialogDescription>
              </div>
            </DialogHeader>
          </div>

          {/* Footer with subtle top border */}
          <DialogFooter className="border-t border-slate-200/60 dark:border-slate-700/40 bg-slate-50/50 dark:bg-slate-800/30 m-0 px-5 pb-3.5 pt-3.5 sm:flex-row sm:justify-end sm:gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={handleCancel}
              className="rounded-lg h-8 px-4 text-xs font-medium border-slate-200 dark:border-slate-700 hover:bg-slate-100 dark:hover:bg-slate-800 transition-all"
            >
              {state.options.cancelText || "取消"}
            </Button>
            <Button
              variant="default"
              size="sm"
              onClick={handleConfirm}
              className={`rounded-lg h-8 px-4 text-xs font-semibold text-white border-0 transition-all hover:opacity-90 hover:scale-[1.02] active:scale-[0.98] shadow-md ${
                isDestructive
                  ? "bg-gradient-to-r from-red-500 to-rose-600 shadow-red-500/25"
                  : "bg-gradient-to-r from-indigo-500 to-violet-600 shadow-indigo-500/25"
              }`}
            >
              {state.options.confirmText || "确认"}
            </Button>
          </DialogFooter>
        </div>
      </DialogContent>
    </Dialog>
  );

  return { confirm, ConfirmDialog };
}
