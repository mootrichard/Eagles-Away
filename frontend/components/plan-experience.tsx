"use client";
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { ApiError, Snapshot, newer, request, terminal } from "../lib/api";
import { ItineraryView, JourneyPreview } from "./itinerary-view";
import { PlanningProgress } from "./planning-progress";
import { trackAnalytics } from "../lib/analytics";

export function PlanExperience() {
  const params = useSearchParams();
  const session = params.get("session");
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const current = useRef<Snapshot | null>(null);
  const [error, setError] = useState("");
  const [city] = useState<string | undefined>(() => {
    if (typeof window === "undefined" || !session) return undefined;
    try { return sessionStorage.getItem(`cpfc-city-${session}`) ?? undefined; } catch { return undefined; }
  });
  const [showHero, setShowHero] = useState(false);
  const [completing, setCompleting] = useState(false);
  const heroShown = useRef(false);
  const hadItinerary = useRef(false);
  const hasItinerary = !!snapshot?.itinerary;
  const outcomeTracked = useRef(false);
  const refresh = useRef<() => void>(() => {});
  useEffect(() => {
    if (!session) return;
    let stopped = false, inFlight = false, failures = 0;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      if (stopped || inFlight || document.hidden || terminal(current.current)) return;
      clearTimeout(timer); inFlight = true;
      try {
        const revision = current.current?.state_revision;
        const data = await request<Snapshot>(`/api/sessions/${session}/snapshot${revision === undefined ? "" : `?after_revision=${revision}`}`);
        if (stopped) return;
        current.current = newer(current.current, data); setSnapshot(current.current); setError(""); failures = 0;
      } catch (e) {
        if (stopped) return;
        failures++;
        setError(e instanceof ApiError && e.status === 404 ? "This session is unavailable in this browser." : "Reconnecting… Your displayed draft is still saved.");
      } finally {
        inFlight = false;
        if (!stopped && !terminal(current.current)) {
          const idle = current.current?.phase === "draft_ready" && !current.current.active_turn_id;
          timer = setTimeout(poll, failures ? Math.min(30000, 2000 * 2 ** failures) : (idle ? 20000 : 2000) + Math.random() * 200);
        }
      }
    }
    refresh.current = () => { clearTimeout(timer); void poll(); };
    const resume = () => { if (!document.hidden) refresh.current(); };
    document.addEventListener("visibilitychange", resume); window.addEventListener("online", resume);
    void poll();
    return () => { stopped = true; clearTimeout(timer); document.removeEventListener("visibilitychange", resume); window.removeEventListener("online", resume); };
  }, [session]);
  // Anti-flash gate: never show the elaborate stepper for instant/recorded results.
  useEffect(() => { const t = setTimeout(() => setShowHero(true), 400); return () => clearTimeout(t); }, []);
  useEffect(() => {
    if (showHero && snapshot && !snapshot.itinerary && !terminal(snapshot)) heroShown.current = true;
  }, [showHero, snapshot]);
  // Brief all-green "done" beat before revealing the first itinerary, only if the hero was shown.
  useEffect(() => {
    if (hasItinerary && !hadItinerary.current && heroShown.current) {
      setCompleting(true);
      const t = setTimeout(() => setCompleting(false), 450);
      hadItinerary.current = hasItinerary;
      return () => clearTimeout(t);
    }
    hadItinerary.current = hasItinerary;
  }, [hasItinerary]);
  useEffect(() => {
    if (outcomeTracked.current || !snapshot) return;
    if (snapshot.itinerary) { trackAnalytics("plan_ready"); outcomeTracked.current = true; }
    else if (snapshot.phase === "failed") { trackAnalytics("plan_failed"); outcomeTracked.current = true; }
  }, [snapshot]);
  if (!session) return <div className="plan panel"><h1>No session selected</h1><Link href="/">Start a trip brief <span className="arrow">→</span></Link></div>;
  const origin = snapshot?.origin_city ?? "your city";
  return <div className="plan">{snapshot?.development_mode && <div className="notice">Development preview · Synthetic trips and prices</div>}<div className="plan-heading"><div><p className="eyebrow">YOUR AWAY DAYS</p><h1>A plan worth<br />travelling for.</h1></div><span className="tag">From {origin}</span></div>
    <div className={terminal(snapshot) ? "status" : "status sr-only"} role="status" aria-live="polite">{snapshot?.phase === "failed" ? "We couldn’t complete this trip request." : snapshot?.progress_message ?? "Connecting to your planning session…"}</div>
    {error && <p className="error" role="alert">{error}</p>}
    {!snapshot?.itinerary && !snapshot?.preview_trip && !terminal(snapshot) && (showHero
      ? <PlanningProgress snapshot={snapshot} city={city} origin={origin} variant="hero" />
      : <div className="plan-connecting" aria-hidden="true"><span className="plan-connecting-mark" />Preparing your route…</div>)}
    {snapshot?.preview_trip && !snapshot.itinerary && <JourneyPreview trip={snapshot.preview_trip} ended={terminal(snapshot)}>{!terminal(snapshot) && <PlanningProgress snapshot={snapshot} city={snapshot.preview_trip.fixture.city} origin={origin} variant="preview" />}</JourneyPreview>}
    {completing && <PlanningProgress snapshot={snapshot} city={city} origin={origin} variant="hero" forceDone />}
    {terminal(snapshot) && !snapshot?.itinerary && <p className="notice">{snapshot?.preview_trip ? "Your partial results are saved here. A complete itinerary could not be verified. " : "No itinerary was saved for this request. "}<Link href="/">Try another trip brief <span className="arrow">→</span></Link></p>}
    {snapshot?.itinerary && !completing && <div className="plan-reveal"><ItineraryView itinerary={snapshot.itinerary} /></div>}
    {snapshot?.itinerary && terminal(snapshot) && <p className="fine">This planning session has ended. <Link href="/">Start a new trip brief <span className="arrow">→</span></Link></p>}
  </div>;
}
