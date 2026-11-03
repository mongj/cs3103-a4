import { CaptionCard } from "@/components/caption-card";
import { LectureVideo } from "@/components/lecture-video";
import type { Caption } from "@/hooks/use-live-recall";
import type { LectureId } from "@/lib/lectures";

type LiveCaptionsProps = { captions: Caption[]; lecture: LectureId; live: boolean };

// The lecture feed with only the newest segment under it; older captions live
// in the "What did I miss?" panel.
export function LiveCaptions({ captions, lecture, live }: LiveCaptionsProps) {
  const latest = captions.at(-1);

  return (
    <section className="flex min-h-0 min-w-0 flex-col max-md:flex-none md:flex-1">
      <h2 className="px-5 py-4 font-heading text-2xl tracking-wide">Live lecture</h2>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex min-h-full max-w-5xl flex-col justify-center gap-5 px-5 pb-5">
          <LectureVideo lecture={lecture} live={live} talking={live && latest !== undefined && !latest.final} />
          <div aria-live="polite" className="min-h-28">
            {latest ? (
              <CaptionCard caption={latest} className="text-lg" />
            ) : (
              <p className="py-3 text-center text-sm text-muted-foreground">Captions pop up here as the lecturer speaks.</p>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
