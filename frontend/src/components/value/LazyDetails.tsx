import { useState, type ReactNode } from "react";

/** Native disclosure styling with deliberate, once-per-page child mounting. */
export function LazyDetails({ summary, children, className = "", open = false }: { summary: ReactNode; children: ReactNode; className?: string; open?: boolean }) {
  const [activated, setActivated] = useState(open);
  return <details className={className} open={open} onToggle={(event) => {
    if (event.currentTarget.open) setActivated(true);
  }}>
    <summary className="cursor-pointer px-5 py-4 font-semibold">{summary}</summary>
    {activated ? children : null}
  </details>;
}
