import { ConnectionStatus } from "@/components/connection-status";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { Status } from "@/hooks/use-live-recall";
import { LECTURES, type LectureId } from "@/lib/lectures";
import { WifiIcon, WifiOffIcon } from "lucide-react";

type AppSidebarProps = {
  status: Status;
  offline: boolean;
  clientId: number;
  lecture: LectureId;
  onLectureChange: (lecture: LectureId) => void;
  onToggleOffline: () => void;
};

export function AppSidebar({ status, offline, clientId, lecture, onLectureChange, onToggleOffline }: AppSidebarProps) {
  return (
    <aside className="flex shrink-0 flex-col gap-4 bg-sidebar p-4 md:h-full md:w-72 md:p-5">
      <div className="flex items-center justify-between gap-2">
        <h1 className="font-heading text-3xl tracking-wide [text-shadow:2px_2px_0_#47c2f0]">LiveRecall</h1>
        <ThemeToggle />
      </div>

      <div className="flex flex-col gap-3 rounded-xl bg-card p-3 text-sm max-md:flex-row max-md:items-center max-md:justify-between max-md:py-2">
        <Select onValueChange={(value) => onLectureChange(value as LectureId)} value={lecture}>
          <SelectTrigger aria-label="Lecture" className="w-full rounded-full bg-background font-semibold max-md:w-auto">
            <SelectValue />
          </SelectTrigger>
          <SelectContent position="popper">
            {Object.entries(LECTURES).map(([id, { title }]) => (
              <SelectItem key={id} value={id}>{id} · {title}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <div className="flex justify-between gap-4 px-1">
          <span className="text-muted-foreground">Client</span>
          <span className="font-semibold tabular-nums">{clientId}</span>
        </div>
      </div>

      <div className="flex-1 max-md:hidden" />

      <div className="flex items-center justify-between gap-3 md:flex-col md:items-stretch">
        <ConnectionStatus status={status} />
        <Button
          aria-pressed={offline}
          onClick={onToggleOffline}
          size="lg"
          type="button"
          variant={offline ? "default" : "secondary"}
        >
          {offline ? <WifiIcon /> : <WifiOffIcon />}
          {offline ? "Go online" : "Go offline"}
        </Button>
      </div>
    </aside>
  );
}
