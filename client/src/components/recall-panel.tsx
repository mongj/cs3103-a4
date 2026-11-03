import { clockTime } from "@/components/live-captions";
import { Button } from "@/components/ui/button";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import type { Recall } from "@/hooks/use-live-recall";
import { cn } from "@/lib/utils";
import { HistoryIcon } from "lucide-react";

const WINDOWS = [15, 30, 60];
const DEFAULT_WINDOW = 30;
const NOTE_COLORS = ["bg-note-yellow", "bg-note-pink", "bg-note-blue", "bg-note-green", "bg-note-purple"];

type RecallPanelProps = {
  recall: Recall | null;
  onRecall: (seconds: number) => void;
};

export function RecallPanel({ recall, onRecall }: RecallPanelProps) {
  return (
    <aside className="flex min-h-0 shrink-0 flex-col bg-sidebar max-md:max-h-[45%] md:h-full md:w-96">
      <div className="flex flex-col gap-3 px-5 py-4">
        <h2 className="font-heading text-2xl tracking-wide">What did I miss?</h2>
        <div className="flex gap-2">
          {WINDOWS.map((seconds) => (
            <Button
              className="flex-1"
              key={seconds}
              onClick={() => onRecall(seconds)}
              size="lg"
              type="button"
              variant={seconds === DEFAULT_WINDOW ? "default" : "secondary"}
            >
              {seconds} s
            </Button>
          ))}
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-5">
        {recall === null ? (
          <Empty>
            <EmptyHeader>
              <EmptyMedia className="rounded-full bg-note-yellow text-note-foreground" variant="icon">
                <HistoryIcon />
              </EmptyMedia>
              <EmptyTitle className="font-heading text-xl tracking-wide">Catch up</EmptyTitle>
              <EmptyDescription>Pick a window to see the finalized captions from it.</EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : (
          <>
            <p className="mb-3 text-xs font-semibold text-muted-foreground">
              Last {recall.seconds} s, as of {clockTime(recall.at)}
            </p>
            {recall.captions.length === 0 ? (
              <p className="text-sm text-muted-foreground">Nothing in the last {recall.seconds} seconds.</p>
            ) : (
              <div className="flex flex-col gap-3 p-1">
                {recall.captions.map((caption) => (
                  <div
                    className={cn(
                      "rounded-xl px-4 py-3 text-note-foreground shadow-sm",
                      NOTE_COLORS[caption.segment % NOTE_COLORS.length],
                      caption.segment % 2 ? "rotate-1" : "-rotate-1",
                    )}
                    key={caption.segment}
                  >
                    <time className="mb-1 block text-xs opacity-60 tabular-nums">{clockTime(caption.source_ts)}</time>
                    <p className="text-sm font-medium leading-relaxed">{caption.text}</p>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </aside>
  );
}
