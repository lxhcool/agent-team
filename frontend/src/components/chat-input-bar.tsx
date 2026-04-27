"use client";

import { useState, useRef, useCallback } from "react";
import {
  Send, Bot, Paperclip, X, ImagePlus,
  ChevronDown as ChevronDownIcon,
} from "lucide-react";
import { useAvailableModels, type ModelOption } from "@/hooks/use-available-models";

export type ChatInputBarProps = {
  /** Placeholder text */
  placeholder?: string;
  /** Whether the input is disabled */
  disabled?: boolean;
  /** Whether a message is being sent */
  sending?: boolean;
  /** Callback when user sends a message */
  onSend: (text: string, files: File[], model: string | null) => void;
  /** File accept string for the file upload button */
  fileAccept?: string;
  /** Max file size in bytes */
  maxFileSize?: number;
};

export function ChatInputBar({
  placeholder = "输入消息...",
  disabled = false,
  sending = false,
  onSend,
  fileAccept = ".md,.txt,.json,.yaml,.yml,.py,.js,.ts,.tsx,.jsx,.css,.html,.sql,.sh,.toml,.xml,.csv,.env,.gitignore,.dockerfile,.makefile,.png,.jpg,.jpeg,.gif,.webp,.bmp,.svg",
  maxFileSize = 10 * 1024 * 1024,
}: ChatInputBarProps) {
  const [inputText, setInputText] = useState("");
  const [uploadedFiles, setUploadedFiles] = useState<File[]>([]);
  const [imagePreviews, setImagePreviews] = useState<{ name: string; url: string }[]>([]);
  const [selectedModel, setSelectedModel] = useState<string>("");
  const [showModelPicker, setShowModelPicker] = useState(false);
  const { models: availableModels, defaultModel } = useAvailableModels();
  const inputRef = useRef<HTMLInputElement>(null);

  const canSend = (inputText.trim() || uploadedFiles.length > 0) && !sending && !disabled;

  const handleSend = useCallback(() => {
    if (!canSend) return;
    onSend(inputText, uploadedFiles, selectedModel || null);
    setInputText("");
    setUploadedFiles([]);
    setImagePreviews([]);
    inputRef.current?.focus();
  }, [canSend, inputText, uploadedFiles, selectedModel, onSend]);

  const handleFileAdd = useCallback((files: File[]) => {
    const valid = files.filter(f => {
      if (f.size > maxFileSize) {
        alert(`文件 ${f.name} 超过 ${maxFileSize / 1024 / 1024}MB 限制`);
        return false;
      }
      return true;
    });
    if (valid.length > 0) {
      setUploadedFiles(prev => [...prev, ...valid]);
      valid.forEach(f => {
        if (f.type.startsWith("image/")) {
          const url = URL.createObjectURL(f);
          setImagePreviews(prev => [...prev, { name: f.name, url }]);
        }
      });
    }
  }, [maxFileSize]);

  const removeImage = useCallback((index: number) => {
    const img = imagePreviews[index];
    setImagePreviews(prev => prev.filter((_, idx) => idx !== index));
    if (img) {
      const fileIdx = uploadedFiles.findIndex(f => f.name === img.name);
      if (fileIdx >= 0) setUploadedFiles(prev => prev.filter((_, idx) => idx !== fileIdx));
    }
  }, [imagePreviews, uploadedFiles]);

  const removeFile = useCallback((index: number) => {
    setUploadedFiles(prev => prev.filter((_, idx) => idx !== index));
  }, []);

  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    const files: File[] = [];
    for (const item of Array.from(items)) {
      if (item.kind === "file") {
        const f = item.getAsFile();
        if (f) files.push(f);
      }
    }
    if (files.length > 0) handleFileAdd(files);
  }, [handleFileAdd]);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const files = Array.from(e.dataTransfer.files);
    if (files.length > 0) handleFileAdd(files);
  }, [handleFileAdd]);

  const nonImageFiles = uploadedFiles.filter(f => !f.type.startsWith("image/"));
  const hasAttachments = uploadedFiles.length > 0 || imagePreviews.length > 0;

  return (
    <div
      className="shrink-0 border-t border-[var(--card-border)] bg-[var(--card)]/80 backdrop-blur-xl"
      onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); }}
      onDrop={handleDrop}
      onPaste={handlePaste}
    >
      {/* Attachments preview */}
      {hasAttachments && (
        <div className="px-4 pt-3 pb-0 flex flex-wrap gap-2">
          {imagePreviews.map((img, i) => (
            <div key={`img-${i}`} className="relative group rounded-xl overflow-hidden border border-[var(--card-border)] shadow-sm ring-1 ring-white/5">
              <img src={img.url} alt={img.name} className="size-14 object-cover" />
              <button
                onClick={() => removeImage(i)}
                className="absolute top-0.5 right-0.5 flex size-4 items-center justify-center rounded-full bg-black/60 text-white opacity-0 group-hover:opacity-100 transition-opacity cursor-pointer"
              >
                <X size={9} />
              </button>
            </div>
          ))}
          {nonImageFiles.map((f, i) => {
            const realIndex = uploadedFiles.indexOf(f);
            return (
              <span
                key={`file-${i}`}
                className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--card-border)] bg-[var(--surface-elevated)] px-2.5 py-1 text-xs shadow-sm ring-1 ring-white/5"
              >
                <Paperclip size={10} className="text-[var(--accent)]" />
                <span className="max-w-[120px] truncate">{f.name}</span>
                <span className="text-[var(--muted)] text-[10px]">({(f.size / 1024).toFixed(1)}K)</span>
                <button
                  onClick={() => removeFile(realIndex)}
                  className="text-[var(--muted)] hover:text-[var(--danger)] cursor-pointer transition-colors"
                >
                  <X size={11} />
                </button>
              </span>
            );
          })}
        </div>
      )}

      {/* Main input row */}
      <div className="flex items-center gap-2 px-4 py-3">
        {/* Model picker */}
        <ModelPicker
          models={availableModels}
          defaultModel={defaultModel}
          selectedModel={selectedModel}
          onSelect={setSelectedModel}
          open={showModelPicker}
          onToggle={() => setShowModelPicker(v => !v)}
          onClose={() => setShowModelPicker(false)}
        />

        {/* File upload */}
        <ToolbarButton title="上传文件">
          <Paperclip size={14} />
          <input
            type="file"
            multiple
            className="hidden"
            accept={fileAccept}
            onChange={(e) => {
              const files = Array.from(e.target.files || []);
              if (files.length > 0) handleFileAdd(files);
              e.target.value = "";
            }}
          />
        </ToolbarButton>

        {/* Image upload */}
        <ToolbarButton title="上传图片">
          <ImagePlus size={14} />
          <input
            type="file"
            multiple
            className="hidden"
            accept="image/png,image/jpeg,image/gif,image/webp,image/bmp,image/svg+xml"
            onChange={(e) => {
              const files = Array.from(e.target.files || []);
              if (files.length > 0) handleFileAdd(files);
              e.target.value = "";
            }}
          />
        </ToolbarButton>

        {/* Text input + send */}
        <div className="flex-1 flex items-center rounded-xl border border-[var(--card-border)] bg-[var(--surface-elevated)] shadow-sm ring-1 ring-white/5 transition-all duration-200 focus-within:border-[var(--accent)] focus-within:shadow-[var(--shadow-glow)] focus-within:ring-[var(--accent)]/10">
          <input
            ref={inputRef}
            type="text"
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && canSend) handleSend(); }}
            placeholder={placeholder}
            disabled={disabled}
            className="h-10 flex-1 bg-transparent px-4 text-sm outline-none placeholder:text-[var(--muted)] disabled:opacity-50"
          />
          <button
            onClick={handleSend}
            disabled={!canSend}
            className="flex size-9 mr-0.5 items-center justify-center rounded-lg bg-[var(--accent)] text-white cursor-pointer hover:bg-[var(--accent-hover)] disabled:opacity-30 disabled:cursor-not-allowed transition-all duration-200 shadow-sm hover:shadow-md hover:shadow-indigo-500/20"
          >
            <Send size={14} />
          </button>
        </div>
      </div>
    </div>
  );
}

/* ========== Sub-components ========== */

function ToolbarButton({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <label
      className="flex size-9 items-center justify-center rounded-lg border border-[var(--card-border)] bg-[var(--surface-elevated)] text-[var(--muted)] cursor-pointer hover:border-[var(--accent)] hover:text-[var(--accent)] transition-all duration-200 shadow-sm ring-1 ring-white/5 hover:shadow-md hover:shadow-indigo-500/10"
      title={title}
    >
      {children}
    </label>
  );
}

function ModelPicker({
  models,
  defaultModel,
  selectedModel,
  onSelect,
  open,
  onToggle,
  onClose,
}: {
  models: ModelOption[];
  defaultModel: string | null;
  selectedModel: string;
  onSelect: (v: string) => void;
  open: boolean;
  onToggle: () => void;
  onClose: () => void;
}) {
  const displayLabel = selectedModel
    ? selectedModel.split("/").pop()
    : (defaultModel || "默认模型");

  return (
    <div className="relative">
      <button
        onClick={onToggle}
        className="flex h-9 items-center gap-1 rounded-lg border border-[var(--card-border)] bg-[var(--surface-elevated)] px-2.5 text-[11px] text-[var(--muted)] hover:border-[var(--accent)] hover:text-[var(--accent)] transition-all duration-200 shadow-sm ring-1 ring-white/5 max-w-[140px] hover:shadow-md hover:shadow-indigo-500/10"
        title="选择模型"
      >
        <Bot size={12} />
        <span className="truncate">{displayLabel}</span>
        <ChevronDownIcon size={10} className="shrink-0" />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={onClose} />
          <div className="absolute bottom-full left-0 mb-2 z-50 w-64 max-h-64 overflow-y-auto rounded-xl border border-[var(--card-border)] bg-[var(--card)] shadow-xl ring-1 ring-white/5">
            <div className="p-1.5">
              <div className="px-2.5 py-1.5 text-[10px] font-semibold text-[var(--muted)] uppercase tracking-wider">
                模型选择
              </div>
              <button
                onClick={() => { onSelect(""); onClose(); }}
                className={`w-full text-left rounded-lg px-3 py-2 text-xs transition-colors duration-150 cursor-pointer ${!selectedModel ? "bg-[var(--accent-soft)] text-[var(--accent)] font-medium" : "text-[var(--muted)] hover:bg-[var(--surface-elevated)]"}`}
              >
                默认模型 {defaultModel && <span className="text-[10px] ml-1 opacity-70">({defaultModel})</span>}
              </button>
              {models.map((m) => (
                <button
                  key={m.model_id}
                  onClick={() => { onSelect(m.model_id); onClose(); }}
                  className={`w-full text-left rounded-lg px-3 py-2 text-xs transition-colors duration-150 cursor-pointer ${selectedModel === m.model_id ? "bg-[var(--accent-soft)] text-[var(--accent)] font-medium" : "text-[var(--foreground)] hover:bg-[var(--surface-elevated)]"}`}
                >
                  <span className="font-medium">{m.model_name}</span>
                  <span className="ml-1.5 text-[10px] text-[var(--muted)]">{m.provider_display}</span>
                </button>
              ))}
              {models.length === 0 && (
                <div className="px-3 py-2.5 text-[11px] text-[var(--muted)] text-center">
                  暂无可用模型
                  <br />
                  <span className="text-[10px] opacity-70">请在 设置 → 模型 中配置</span>
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
