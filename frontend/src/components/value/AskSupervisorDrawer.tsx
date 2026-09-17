import { X } from "lucide-react";

/**
 * 问投研主管抽屉（2026-09-14）：右侧滑出，内嵌 /ai/agent 主管对话。
 * 在浮窗内直接对话，不跳转页面。iframe 方案零侵入主管页面代码。
 */
export function AskSupervisorDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label="问投研主管">
      <button
        type="button"
        aria-label="点击遮罩关闭"
        className="absolute inset-0 bg-black/50"
        onClick={onClose}
      />
      <aside className="relative flex h-full w-full max-w-3xl flex-col border-l bg-background shadow-2xl">
        <header className="flex items-center justify-between gap-3 border-b px-4 py-3">
          <div>
            <div className="text-sm font-semibold">问投研主管</div>
            <p className="text-xs text-muted-foreground">直接提问，不离开当前页面。</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭"
            className="rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </header>
        <iframe
          title="问投研主管"
          src="/ai/agent"
          className="min-h-0 w-full flex-1 border-0"
        />
      </aside>
    </div>
  );
}
