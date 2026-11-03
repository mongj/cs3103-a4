import { CaptionCard } from "@/components/caption-card";
import { Button } from "@/components/ui/button";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { Caption } from "@/hooks/use-live-recall";
import { ArrowDownIcon, HistoryIcon } from "lucide-react";
import { useState } from "react";
import { StickToBottom, useStickToBottomContext } from "use-stick-to-bottom";

const WINDOWS = ["15", "30", "60"];

// Shown only once the student has scrolled up; following resumes at the bottom.
function ScrollToLatest() {
  const { isAtBottom, scrollToBottom } = useStickToBottomContext();
  if (isAtBottom) return null;
  return (
    <Button
      className="absolute bottom-4 left-1/2 -translate-x-1/2 shadow-lg"
      onClick={() => void scrollToBottom()}
      type="button"
    >
      <ArrowDownIcon />
      Latest
    </Button>
  );
}

export function RecallPanel({ captions }: { captions: Caption[] }) {
  const [seconds, setSeconds] = useState("30");
  // The window ends at the newest caption, not now, so it still has content
  // after the lecture stops.
  const end = Math.max(...captions.map((caption) => caption.source_ts));
  const shown = captions.filter((caption) => caption.source_ts >= end - Number(seconds));

  return (
    <aside className="flex min-h-0 shrink-0 flex-col bg-sidebar max-md:flex-1 md:h-full md:w-80 xl:w-[26rem]">
      <div className="flex items-center justify-between gap-3 px-5 py-4">
        <h2 className="font-heading text-2xl tracking-wide">What did I miss?</h2>
        <Select onValueChange={setSeconds} value={seconds}>
          <SelectTrigger aria-label="Recall window" className="rounded-full bg-card font-semibold">
            <SelectValue />
          </SelectTrigger>
          <SelectContent align="end" position="popper">
            {WINDOWS.map((value) => (
              <SelectItem key={value} value={value}>Last {value} s</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {shown.length === 0 ? (
        <Empty>
          <EmptyHeader>
            <EmptyMedia className="rounded-full bg-note-yellow text-note-foreground" variant="icon">
              <HistoryIcon />
            </EmptyMedia>
            <EmptyTitle className="font-heading text-xl tracking-wide">Catch up</EmptyTitle>
            <EmptyDescription>Recent captions collect here as the lecture goes on.</EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <StickToBottom className="relative min-h-0 flex-1 overflow-y-auto" initial="instant" resize="instant">
          <StickToBottom.Content className="flex flex-col gap-2 px-5 pb-5">
            {shown.map((caption, i) => (
              <CaptionCard
                caption={caption}
                // Dashed Padlet yellow marks the newest segment.
                className={i === shown.length - 1 ? "border-dashed border-highlight" : undefined}
                key={caption.segment}
              />
            ))}
          </StickToBottom.Content>
          <ScrollToLatest />
        </StickToBottom>
      )}
    </aside>
  );
}
