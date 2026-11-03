import { AppSidebar } from "@/components/app-sidebar";
import { LiveCaptions } from "@/components/live-captions";
import { RecallPanel } from "@/components/recall-panel";
import { useLiveRecall } from "@/hooks/use-live-recall";
import type { LectureId } from "@/lib/lectures";
import { useState } from "react";

type LectureViewProps = { lecture: LectureId; onLectureChange: (lecture: LectureId) => void };

function LectureView({ lecture, onLectureChange }: LectureViewProps) {
  const live = useLiveRecall(lecture);

  return (
    <div className="flex h-svh flex-col overflow-hidden bg-background text-foreground md:flex-row">
      <AppSidebar
        clientId={live.clientId}
        lecture={lecture}
        offline={live.offline}
        onLectureChange={onLectureChange}
        onToggleOffline={live.toggleOffline}
        status={live.status}
      />
      <LiveCaptions captions={live.captions} lecture={lecture} live={live.status === "connected"} />
      <RecallPanel captions={live.captions} />
    </div>
  );
}

export function App() {
  const [lecture, setLecture] = useState<LectureId>("L1");
  // A new key gives each lecture a fresh connection and caption state.
  return <LectureView key={lecture} lecture={lecture} onLectureChange={setLecture} />;
}
