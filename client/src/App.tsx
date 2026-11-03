import { AppSidebar } from "@/components/app-sidebar";
import { LiveCaptions } from "@/components/live-captions";
import { RecallPanel } from "@/components/recall-panel";
import { useLiveRecall } from "@/hooks/use-live-recall";

export function App() {
  const live = useLiveRecall();

  return (
    <div className="flex h-svh flex-col overflow-hidden bg-background text-foreground md:flex-row">
      <AppSidebar
        clientId={live.clientId}
        offline={live.offline}
        onToggleOffline={live.toggleOffline}
        status={live.status}
      />
      <LiveCaptions captions={live.captions} />
      <RecallPanel onRecall={live.showRecall} recall={live.recall} />
    </div>
  );
}
