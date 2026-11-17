import type { Snapshot } from "./api";

// Phase-anchored progress for the waiting state. The only sources of truth are the
// real phase/progress_message transitions the workflow commits; nothing here claims
// a step finished that the backend has not actually passed.
export type Stage = "preferences" | "searching" | "done" | "failed";

export function stageFor(snapshot: Snapshot | null): Stage {
  if (!snapshot) return "preferences";
  if (snapshot.itinerary) return "done";
  if (snapshot.phase === "failed" || snapshot.phase === "closed") return "failed";
  if (snapshot.preview_trip || /compar/i.test(snapshot.progress_message)) return "searching";
  return "preferences";
}

export function tickerLines(stage: Stage, city?: string, origin = "London"): string[] {
  if (stage === "preferences") return [
    "Checking your travel preferences…",
    "Looking at your matchday plans…",
    "Putting your trip brief together…",
  ];
  if (stage === "done") return ["Your route is ready."];
  if (stage === "failed") return ["We couldn’t complete this trip request."];
  return [
    `Comparing routes from ${origin}…`,
    "Checking journey times…",
    city ? `Comparing places to stay in ${city}…` : "Comparing places to stay…",
    "Weighing price vs. journey time…",
    "Shortlisting your options…",
  ];
}
