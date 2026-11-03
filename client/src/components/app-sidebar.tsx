import { ConnectionStatus } from "@/components/connection-status";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import type { Status } from "@/hooks/use-live-recall";
import { WifiIcon, WifiOffIcon } from "lucide-react";

type AppSidebarProps = {
  status: Status;
  offline: boolean;
  clientId: number;
  onToggleOffline: () => void;
};

export function AppSidebar({ status, offline, clientId, onToggleOffline }: AppSidebarProps) {
  return (
    <aside className="flex shrink-0 flex-col gap-4 bg-sidebar p-4 md:h-full md:w-64 md:p-5">
      <div className="flex items-center justify-between gap-2">
        <h1 className="font-heading text-3xl tracking-wide [text-shadow:2px_2px_0_#47c2f0]">LiveRecall</h1>
        <ThemeToggle />
      </div>

      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 rounded-xl bg-card p-4 text-sm max-md:hidden">
        <dt className="text-muted-foreground">Session</dt>
        <dd className="text-right font-semibold">L1</dd>
        <dt className="text-muted-foreground">Client</dt>
        <dd className="text-right font-semibold tabular-nums">{clientId}</dd>
      </dl>

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
