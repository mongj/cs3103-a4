import type { LectureId } from "@/lib/lectures";
import { cn } from "@/lib/utils";

// A fake lecture feed: a cartoon scene drawn in SVG, in Padlet's illustration
// style (thick ink outlines, flat pastel fills). CSS in index.css animates it.
const INK = "#111111";
const SKY = "#c3e4fa";
const GRASS = "#c9f0c4";
const PURPLE = "#ddd0fa";
const PINK = "#fbcfdc";
const YELLOW = "#fec006";
const HOT_PINK = "#ff4081";
const GREEN = "#7bd389";

function Packet({ fill, begin }: { fill: string; begin: string }) {
  return (
    <rect fill={fill} height="12" rx="3" width="16" x="-8" y="-6">
      <animateMotion begin={begin} dur="3s" repeatCount="indefinite" rotate="auto">
        <mpath href="#lecture-link" />
      </animateMotion>
    </rect>
  );
}

// A process tag sliding along the ready queue into the CPU.
function Process({ fill, label, begin }: { fill: string; label: string; begin: string }) {
  return (
    <g>
      <rect fill={fill} height="24" rx="6" width="34" x="-17" y="-12" />
      <text fill={INK} fontFamily="Lilita One" fontSize="13" stroke="none" textAnchor="middle" y="5">
        {label}
      </text>
      <animateMotion begin={begin} dur="4.5s" repeatCount="indefinite">
        <mpath href="#lecture-queue" />
      </animateMotion>
    </g>
  );
}

function Computer({ x, screen }: { x: number; screen: string }) {
  return (
    <g>
      <rect fill={screen} height="52" rx="8" width="72" x={x} y="128" />
      <path d={`M${x + 16} 146 h40 M${x + 16} 158 h26`} strokeWidth="2.5" />
      <rect fill="#ffffff" height="10" width="14" x={x + 29} y="180" />
      <rect fill="#ffffff" height="7" rx="3" width="36" x={x + 18} y="190" />
    </g>
  );
}

function BoardTitle({ children }: { children: string }) {
  return (
    <>
      <text fill={INK} fontFamily="Lilita One" fontSize="26" stroke="none" x="94" y="82">
        {children}
      </text>
      <path d="M96 96 l10 -7 l10 7 l10 -7 l10 7 l10 -7 l10 7 l10 -7 l10 7" fill="none" stroke={HOT_PINK} />
    </>
  );
}

function NetworksBoard() {
  return (
    <>
      <BoardTitle>Networks 101</BoardTitle>
      <Computer screen={PURPLE} x={96} />
      <Computer screen={PINK} x={306} />
      <path
        d="M168 154 C 210 112, 262 196, 306 154"
        fill="none"
        id="lecture-link"
        strokeDasharray="6 8"
        strokeWidth="2.5"
      />
      <Packet begin="0s" fill={YELLOW} />
      <Packet begin="-1s" fill={HOT_PINK} />
      <Packet begin="-2s" fill={GREEN} />
    </>
  );
}

// Processes queue into a CPU above a strip of memory pages.
function OsBoard() {
  return (
    <>
      <BoardTitle>Operating Systems</BoardTitle>
      <path d="M100 160 H290" fill="none" id="lecture-queue" strokeDasharray="6 8" strokeWidth="2.5" />
      <Process begin="0s" fill={PURPLE} label="P1" />
      <Process begin="-1.5s" fill={YELLOW} label="P2" />
      <Process begin="-3s" fill={GREEN} label="P3" />
      <path d="M306 128 v-10 M326 128 v-10 M346 128 v-10 M306 192 v10 M326 192 v10 M346 192 v10" />
      <path d="M294 140 h-10 M294 160 h-10 M294 180 h-10 M358 140 h10 M358 160 h10 M358 180 h10" />
      <rect fill={PINK} height="64" rx="10" width="64" x="294" y="128" />
      <text fill={INK} fontFamily="Lilita One" fontSize="18" stroke="none" textAnchor="middle" x="326" y="166">
        CPU
      </text>
      {[PURPLE, YELLOW, "#ffffff", GREEN, PURPLE, "#ffffff", YELLOW, PINK].map((fill, i) => (
        <rect fill={fill} height="18" key={i} rx="3" width="30" x={100 + i * 34} y="216" />
      ))}
    </>
  );
}

type LectureVideoProps = { lecture: LectureId; live: boolean; talking: boolean };

export function LectureVideo({ lecture, live, talking }: LectureVideoProps) {
  const sweater = lecture === "L1" ? YELLOW : GREEN;

  return (
    <div className="relative aspect-video w-full overflow-hidden rounded-3xl shadow-[6px_6px_0_#47c2f0]">
      <svg
        aria-label={`Animated illustration of a lecturer teaching ${lecture === "L1" ? "networking" : "operating systems"} at a whiteboard`}
        className={cn("lecture block size-full", talking && "talking")}
        preserveAspectRatio="xMidYMid slice"
        role="img"
        viewBox="0 0 640 360"
      >
        <g stroke={INK} strokeLinecap="round" strokeLinejoin="round" strokeWidth="3">
          {/* Room */}
          <rect fill={SKY} height="360" stroke="none" width="640" />
          <path d="M-10 306 Q160 286 320 300 T650 294 V370 H-10 Z" fill={GRASS} />

          {/* Window with a drifting cloud */}
          <clipPath id="lecture-window">
            <rect height="80" rx="8" width="120" x="490" y="28" />
          </clipPath>
          <rect fill="#eef8ff" height="80" rx="8" width="120" x="490" y="28" />
          <g clipPath="url(#lecture-window)">
            <circle cx="585" cy="52" fill={YELLOW} r="12" />
            <g className="lecture-cloud">
              <path d="M500 86 a10 10 0 0 1 14 -12 a14 14 0 0 1 26 2 a9 9 0 0 1 6 10 Z" fill="#ffffff" />
            </g>
          </g>
          <rect fill="none" height="80" rx="8" width="120" x="490" y="28" />
          <path d="M550 28 V108 M490 68 H610" />

          {/* Clock */}
          <circle cx="440" cy="56" fill="#ffffff" r="20" />
          <path className="lecture-clock-hand" d="M440 56 V42" />
          <path d="M440 56 H450" />

          {/* Plant */}
          <g className="lecture-plant">
            <ellipse cx="26" cy="252" fill={GREEN} rx="9" ry="22" transform="rotate(-25 26 252)" />
            <ellipse cx="44" cy="246" fill={GREEN} rx="9" ry="24" transform="rotate(20 44 246)" />
            <ellipse cx="35" cy="240" fill="#a6e3a1" rx="8" ry="26" />
          </g>
          <path d="M16 270 H54 L49 302 H21 Z" fill="#f4a261" />

          {/* Whiteboard */}
          <rect fill="#ffffff" height="210" rx="14" width="330" x="72" y="40" />
          <rect fill="#fdeea0" height="9" rx="4" width="290" x="92" y="250" />
          {lecture === "L1" ? <NetworksBoard /> : <OsBoard />}
          <path className="lecture-sparkle" d="M392 34 l4 10 l10 4 l-10 4 l-4 10 l-4 -10 l-10 -4 l10 -4 Z" fill={YELLOW} />

          {/* Lecturer */}
          <g className="lecture-bob">
            <g className="lecture-arm">
              <path d="M462 236 L414 206" stroke={INK} strokeWidth="18" />
              <path d="M462 236 L414 206" stroke={sweater} strokeWidth="12" />
              <path d="M412 204 L364 172" strokeWidth="4" />
              <circle cx="362" cy="171" fill={HOT_PINK} r="5" />
              <circle cx="412" cy="205" fill={PURPLE} r="9" />
            </g>
            <path d="M448 306 Q452 214 510 210 Q568 214 572 306 Z" fill={sweater} />
            <path d="M494 214 L510 236 L526 214" fill="#ffffff" />
            <circle cx="510" cy="172" fill={PURPLE} r="44" />
            <circle cx="486" cy="190" fill={PINK} r="7" stroke="none" />
            <circle cx="534" cy="190" fill={PINK} r="7" stroke="none" />
            <g className="lecture-eyes">
              <ellipse cx="494" cy="166" fill="#ffffff" rx="9" ry="11" />
              <ellipse cx="526" cy="166" fill="#ffffff" rx="9" ry="11" />
              <circle cx="492" cy="168" fill={INK} r="4.5" stroke="none" />
              <circle cx="524" cy="168" fill={INK} r="4.5" stroke="none" />
            </g>
            <path className="lecture-smile" d="M500 190 Q510 200 520 190" fill="none" />
            <ellipse className="lecture-mouth" cx="510" cy="194" fill="#8a1c40" rx="9" ry="6" />
            {/* Graduation cap */}
            <path d="M466 130 L510 112 L554 130 L510 148 Z" fill={INK} />
            <path d="M510 130 L540 142 V156" fill="none" stroke={YELLOW} />
            <circle cx="540" cy="158" fill={YELLOW} r="3.5" stroke="none" />
          </g>
        </g>
      </svg>

      <span
        className={cn(
          "absolute top-2 left-2 flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-bold tracking-wide text-white sm:top-3 sm:left-3 sm:gap-1.5 sm:px-3 sm:py-1 sm:text-xs",
          live ? "bg-primary" : "bg-neutral-500",
        )}
      >
        <span className={cn("size-2 rounded-full bg-white", live && "animate-pulse")} />
        {live ? "LIVE" : "OFF AIR"}
      </span>
    </div>
  );
}
