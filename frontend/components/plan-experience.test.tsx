import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import type { Snapshot } from "../lib/api";
import { PlanExperience } from "./plan-experience";

const { state } = vi.hoisted(() => ({ state: { snapshot: null as Snapshot | null } }));

vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams("session=example") }));
vi.mock("../lib/api", async importOriginal => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  request: vi.fn(async () => state.snapshot),
}));

const snapshot: Snapshot = {
  development_mode: false,
  public_session_id: "example",
  origin_city: "London",
  state_revision: 1,
  phase: "draft_ready",
  progress_message: "Your draft is ready.",
  itinerary: { revision: 1, generated_at: "2026-10-05T12:00:00Z", trips: [], caveats: [], ranking_policy_version: "1" },
  transcript_tail: [{ id: "reply", turn_id: "turn", role: "assistant", content: "Your plan is ready.", created_at: "2026-10-05T12:00:00Z" }],
  interaction_deadline: "2026-10-05T13:00:00Z",
  follow_ups_remaining: 10,
};

const leg = { id: "flight", kind: "transport", mode: "flight", origin: "LTN", destination: "SZG", departs_at: "2027-01-28T08:00:00Z", arrives_at: "2027-01-28T10:55:00+01:00", operator: "easyJet", service_number: "U2 2639", quote: null, offer: { booking_url: "https://www.google.com/travel/flights", evidence: { underlying_source: "Google Flights", provider: "SearchApi" } }, caveats: [] };
const partialTrip = {
  fixture: { id: "salzburg", city: "Salzburg", opponent: "FC Red Bull Salzburg", kickoff_at: "2027-01-28T20:00:00Z", timezone: "Europe/Vienna", venue: "Red Bull Arena", venue_status: "provisional" },
  journey: { outbound: [leg], inbound: [{ ...leg, id: "return", origin: "SZG", destination: "LTN" }] },
  stay: null,
} as unknown as NonNullable<Snapshot["preview_trip"]>;
const hotel = { property_name: "Salzburg Hotel", check_in: "2027-01-28", check_out: "2027-01-29", room_description: "Single room", bathroom: "private", offer: { booking_url: "https://www.booking.com/hotel/at/example.html", evidence: { underlying_source: "Booking.com", provider: "SearchApi" } }, quote: null, caveats: [] } as unknown as NonNullable<typeof partialTrip.stay>;

describe("trip plan", () => {
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); state.snapshot = null; });

  it.each(["journey", "stay"] as const)("keeps the %s visible when the other search fails", async kind => {
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: () => {}, removeEventListener: () => {} }));
    const trip = kind === "journey" ? partialTrip : { ...partialTrip, journey: null, stay: hotel };
    state.snapshot = { ...snapshot, phase: "researching", itinerary: null, preview_trip: trip };
    render(<PlanExperience />);
    const providerName = kind === "journey" ? /View on Google Flights/ : /View on Booking.com/;
    expect((await screen.findAllByRole("link", { name: providerName }))[0]).toBeVisible();
    state.snapshot = { ...state.snapshot, state_revision: 2, phase: "failed", finalization_reason: "planning_failed" };
    window.dispatchEvent(new Event("online"));
    expect(await screen.findByRole("article", { name: "Partial trip results" })).toBeVisible();
    expect(screen.getAllByRole("link", { name: providerName })[0]).toBeVisible();
    expect(screen.getByText(kind === "journey" ? /We couldn’t verify accommodation/ : /We couldn’t verify a return journey/)).toBeVisible();
    expect(screen.queryByRole("heading", { name: "Finding your stay" })).not.toBeInTheDocument();
    expect(screen.queryByText("Take the plan with you.")).not.toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(/email/i);
  });

  it.each(["journey", "stay"] as const)("restores saved %s results when a failed session is reopened", async kind => {
    state.snapshot = { ...snapshot, phase: "failed", itinerary: null, preview_trip: kind === "journey" ? partialTrip : { ...partialTrip, journey: null, stay: hotel } };
    render(<PlanExperience />);
    expect(await screen.findByRole("article", { name: "Partial trip results" })).toBeVisible();
    expect(screen.getByText("Incomplete trip")).toBeVisible();
    expect(screen.getAllByRole("link", { name: kind === "journey" ? /View on Google Flights/ : /View on Booking.com/ })[0]).toBeVisible();
    expect(screen.getByRole("link", { name: /Try another trip brief/ })).toHaveAttribute("href", "/");
  });

  it("shows the saved plan without the follow-up panel", async () => {
    state.snapshot = snapshot;
    render(<PlanExperience />);
    expect(await screen.findByText("Your draft is ready.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Send me/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Your conversation" })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Ask a question or change your trip" })).not.toBeInTheDocument();
  });

  it("offers a new brief after the session ends without a conversation panel", async () => {
    state.snapshot = { ...snapshot, phase: "closed", progress_message: "This planning session has ended." };
    render(<PlanExperience />);
    expect(await screen.findByRole("link", { name: /Start a new trip brief/ })).toHaveAttribute("href", "/");
    expect(screen.queryByRole("region", { name: "Your conversation" })).not.toBeInTheDocument();
  });

  it("keeps an expired plan visible without delivery controls", async () => {
    state.snapshot = { ...snapshot, phase: "closed", finalization_reason: "limit" };
    render(<PlanExperience />);
    expect(await screen.findByRole("link", { name: /Start a new trip brief|Try another trip brief/ })).toBeVisible();
    expect(document.body).not.toHaveTextContent(/email/i);
    expect(screen.queryByText("Take the plan with you.")).not.toBeInTheDocument();
  });

  it("explains an unsuccessful search without delivery copy", async () => {
    state.snapshot = { ...snapshot, phase: "failed", itinerary: null };
    render(<PlanExperience />);
    expect(await screen.findByRole("link", { name: /Start a new trip brief|Try another trip brief/ })).toBeVisible();
    expect(document.body).not.toHaveTextContent(/email/i);
    expect(screen.queryByText("Take the plan with you.")).not.toBeInTheDocument();
  });

  it("shows a checked journey as an incomplete preview while hotels are pending", async () => {
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: () => {}, removeEventListener: () => {} }));
    const outbound = { id: "outbound", kind: "transport", mode: "flight", origin: "Manchester", destination: "Lyon", departs_at: "2026-10-14T08:00:00Z", arrives_at: "2026-10-14T10:00:00Z", operator: "Example Air", service_number: "EA1", quote: null, offer: { evidence: { underlying_source: "Google Flights", provider: "SearchApi" } }, caveats: [] };
    const inbound = { ...outbound, id: "inbound", origin: "Lyon", destination: "Manchester", departs_at: "2026-10-16T08:00:00Z", arrives_at: "2026-10-16T10:00:00Z" };
    state.snapshot = { ...snapshot, phase: "researching", itinerary: null, preview_trip: { fixture: { city: "Lyon", opponent: "Olympique Lyonnais", kickoff_at: "2026-10-15T19:00:00Z", timezone: "Europe/Paris", venue: "Parc Olympique Lyonnais", venue_status: "confirmed" }, journey: { outbound: [outbound], inbound: [inbound] }, stay: null } as unknown as NonNullable<Snapshot["preview_trip"]> };
    render(<PlanExperience />);
    expect(await screen.findByRole("article", { name: "Journey found while planning continues" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Olympique Lyonnais v Palace" })).toBeVisible();
    const match = screen.getByRole("heading", { name: "Olympique Lyonnais v Crystal Palace" });
    const checking = screen.getByRole("heading", { name: "Finding your stay" });
    const returning = screen.getByRole("heading", { name: /Lyon.*Manchester/ });
    expect(match).toBeVisible();
    expect(screen.getByRole("link", { name: /Buy away tickets/ })).toHaveAttribute("href", "https://www.eticketing.co.uk/cpfc/Events?preFilter=9&preFilterName=3.+Away+Matches+%2526+Coach+Travel&utm_source=website&utm_medium=referral&utm_campaign=WebStoryTickets021026");
    expect(checking).toBeVisible();
    expect(returning).toBeVisible();
    expect(checking.compareDocumentPosition(match) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(match.compareDocumentPosition(returning) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.queryByText(/Comparing routes from/)).not.toBeInTheDocument();
    expect(screen.queryByText("Take the plan with you.")).not.toBeInTheDocument();
  });

  it("goes from the in-progress preview straight to the itinerary without replaying the waiting card", async () => {
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: () => {}, removeEventListener: () => {} }));
    state.snapshot = { ...snapshot, phase: "researching", itinerary: null, preview_trip: partialTrip };
    render(<PlanExperience />);
    expect(await screen.findByRole("article", { name: "Journey found while planning continues" })).toBeVisible();
    await act(() => new Promise(resolve => setTimeout(resolve, 450)));
    state.snapshot = { ...snapshot, state_revision: 2 };
    window.dispatchEvent(new Event("online"));
    await waitFor(() => expect(screen.queryByRole("article", { name: "Journey found while planning continues" })).not.toBeInTheDocument());
    expect(screen.queryByText("Your route is ready.")).not.toBeInTheDocument();
  });
});
