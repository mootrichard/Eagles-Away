"use client";
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { ApiError, Snapshot, newer, request, terminal } from "../lib/api";
import { ItineraryView, JourneyPreview } from "./itinerary-view";
import { PlanningProgress } from "./planning-progress";
import { trackAnalytics } from "../lib/analytics";
import { Icon } from "./icons";

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
  const waitingView = useRef<"hero" | "preview" | null>(null);
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
        setError(e instanceof ApiError && e.status === 404 ? "This session is unavailable in this browser." : "Reconnecting…");
      } finally {
        inFlight = false;
        if (!stopped && !terminal(current.current)) {
          timer = setTimeout(poll, failures ? Math.min(30000, 2000 * 2 ** failures) : 2000 + Math.random() * 200);
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
  // Remember which waiting view was last on screen, so the reveal never steps back to the hero card.
  useEffect(() => {
    if (!snapshot || snapshot.itinerary || terminal(snapshot)) return;
    if (snapshot.preview_trip) waitingView.current = "preview";
    else if (showHero) waitingView.current = "hero";
  }, [showHero, snapshot]);
  // Brief all-green "done" beat before revealing the first itinerary, only straight after the hero card.
  useEffect(() => {
    if (hasItinerary && !hadItinerary.current && waitingView.current === "hero") {
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
  if (!session) return <div className="plan card plain"><h1>No session selected</h1><Link className="text-link" href="/">Start a trip brief<Icon name="arrowRight" size={16} /></Link></div>;
  const origin = snapshot?.origin_city ?? "your city";
  return <div className="plan">
    {snapshot?.development_mode && <div className="notice">Development preview · Synthetic trips and prices</div>}
    <div className="plan-heading">
      <h1>Your away day</h1>
      <span className="tag"><Icon name="pin" size={14} />From {origin}</span>
    </div>
    <div className={terminal(snapshot) ? "status" : "status sr-only"} role="status" aria-live="polite">{snapshot?.progress_message ?? "Connecting to your planning session…"}</div>
    {error && <p className="error" role="alert"><Icon name="alert" />{error}</p>}
    {!snapshot?.itinerary && !snapshot?.preview_trip && !terminal(snapshot) && (showHero
      ? <PlanningProgress snapshot={snapshot} city={city} origin={origin} variant="hero" />
      : <div className="plan-connecting" aria-hidden="true"><span className="plan-connecting-mark" />Preparing your route…</div>)}
    {snapshot?.preview_trip && !snapshot.itinerary && <JourneyPreview trip={snapshot.preview_trip} ended={terminal(snapshot)}>{!terminal(snapshot) && <PlanningProgress snapshot={snapshot} city={snapshot.preview_trip.fixture.city} origin={origin} variant="preview" />}</JourneyPreview>}
    {completing && <PlanningProgress snapshot={snapshot} city={city} origin={origin} variant="hero" forceDone />}
    {terminal(snapshot) && !snapshot?.itinerary && <p className="end-note">{snapshot?.preview_trip ? "Partial results saved." : "No itinerary was saved for this request."}<Link className="text-link" href="/">Try another trip brief<Icon name="arrowRight" size={16} /></Link></p>}
    {snapshot?.itinerary && !completing && <div className="plan-reveal"><ItineraryView itinerary={snapshot.itinerary} /></div>}
    {snapshot?.itinerary && terminal(snapshot) && <p className="end-note"><Link className="text-link" href="/">Start a new trip brief<Icon name="arrowRight" size={16} /></Link></p>}
  </div>;
}
