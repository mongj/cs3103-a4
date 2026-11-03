// Hardcoded demo lectures: one LiveRecall server per lecture, on its own port.
export const LECTURES = {
  L1: { title: "Networks", port: 8765 },
  L2: { title: "Operating Systems", port: 8766 },
} as const;

export type LectureId = keyof typeof LECTURES;
