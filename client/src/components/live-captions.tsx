import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import type { Caption } from "@/hooks/use-live-recall";
import { cn } from "@/lib/utils";
import { ArrowDownIcon, CaptionsIcon } from "lucide-react";
import { StickToBottom, useStickToBottomContext } from "use-stick-to-bottom";

export function clockTime(seconds: number) {
  return new Date(seconds * 1000).toLocaleTimeString([], { hour12: false });
}

// A provisional caption is "being written": dashed Padlet-yellow outline, italic.
function CaptionCard({ caption }: { caption: Caption }) {
  return (
    <div
      className={cn(
        "rounded-xl border-2 px-4 py-3 transition-colors duration-300 animate-in fade-in slide-in-from-bottom-2",
        caption.final
          ? "border-transparent bg-card font-medium"
          : "border-dashed border-highlight italic text-muted-foreground",
      )}
    >
      <time className="mb-1 block text-xs text-muted-foreground not-italic tabular-nums">
        {clockTime(caption.source_ts)}
      </time>
      <p className="leading-relaxed">{caption.text}</p>
    </div>
  );
}

// Following stops while a student scrolls back to read, and resumes at the bottom.
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

export function LiveCaptions({ captions }: { captions: Caption[] }) {
  return (
    <section className="flex min-h-0 min-w-0 flex-1 flex-col">
      <div className="flex items-center justify-between gap-3 px-5 py-4">
        <h2 className="font-heading text-2xl tracking-wide">Live captions</h2>
        <div className="flex gap-1.5">
          <Badge className="border-2 border-dashed border-highlight italic" variant="outline">Provisional</Badge>
          <Badge className="border-2 bg-card text-foreground" variant="secondary">Final</Badge>
        </div>
      </div>
      <StickToBottom className="relative min-h-0 flex-1 overflow-y-auto" initial="instant" resize="smooth">
        <StickToBottom.Content className="mx-auto flex max-w-3xl flex-col gap-2 px-5 pb-5">
          {captions.length === 0 ? (
            <Empty>
              <EmptyHeader>
                <EmptyMedia className="rounded-full bg-note-blue text-note-foreground" variant="icon">
                  <CaptionsIcon />
                </EmptyMedia>
                <EmptyTitle className="font-heading text-xl tracking-wide">Waiting for captions</EmptyTitle>
                <EmptyDescription>Captions pop up here as the lecturer speaks.</EmptyDescription>
              </EmptyHeader>
            </Empty>
          ) : (
            captions.map((caption) => <CaptionCard caption={caption} key={caption.segment} />)
          )}
        </StickToBottom.Content>
        <ScrollToLatest />
      </StickToBottom>
    </section>
  );
}
