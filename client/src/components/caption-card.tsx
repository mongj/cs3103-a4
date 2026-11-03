import type { Caption } from "@/hooks/use-live-recall";
import { cn } from "@/lib/utils";

export function clockTime(seconds: number) {
  return new Date(seconds * 1000).toLocaleTimeString([], { hour12: false });
}

// Same box for both states; only the text style marks a provisional caption.
export function CaptionCard({ caption, className }: { caption: Caption; className?: string }) {
  return (
    <div className={cn("rounded-xl border-2 border-transparent bg-card px-4 py-3", className)}>
      <time className="mb-1 block text-xs text-muted-foreground tabular-nums">{clockTime(caption.source_ts)}</time>
      <p className={cn("leading-relaxed", caption.final ? "font-medium" : "italic text-muted-foreground")}>
        {caption.text}
      </p>
    </div>
  );
}
