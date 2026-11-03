import type { Status } from "@/hooks/use-live-recall";
import { cn } from "@/lib/utils";

const view: Record<Status, { label: string; dot: string }> = {
  disconnected: { label: "Disconnected", dot: "bg-muted-foreground" },
  connecting: { label: "Connecting", dot: "bg-highlight animate-pulse" },
  connected: { label: "Connected", dot: "bg-success" },
  reconnecting: { label: "Reconnecting", dot: "bg-highlight animate-pulse" },
  offline: { label: "Offline", dot: "bg-destructive" },
};

export function ConnectionStatus({ status }: { status: Status }) {
  return (
    <div className="flex items-center gap-2 text-sm font-semibold" role="status">
      <span className={cn("size-2.5 shrink-0 rounded-full", view[status].dot)} />
      <span>{view[status].label}</span>
    </div>
  );
}
