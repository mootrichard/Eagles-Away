import type { Fixture } from "./api";

const countryCodes: Record<string, string> = {
  Austria: "AT", Belgium: "BE", Croatia: "HR", Cyprus: "CY", Czechia: "CZ", Denmark: "DK", France: "FR", Germany: "DE",
  Greece: "GR", Hungary: "HU", Israel: "IL", Italy: "IT", Netherlands: "NL", Norway: "NO", Poland: "PL", Portugal: "PT",
  Romania: "RO", Serbia: "RS", Spain: "ES", Sweden: "SE", Switzerland: "CH", Türkiye: "TR", Turkey: "TR", Ukraine: "UA",
};

export function flag(fixture: Pick<Fixture, "venue_location">): string {
  const code = countryCodes[fixture.venue_location?.split(",").at(-1)?.trim() ?? ""];
  return code ? String.fromCodePoint(...[...code].map(c => 0x1f1a5 + c.charCodeAt(0))) : "";
}

export function country(fixture: Pick<Fixture, "venue_location">): string {
  return fixture.venue_location?.split(",").at(-1)?.trim() ?? "";
}

// Provider timestamps carry the local offset, so the written wall-clock time is the local time.
export const wallTime = (iso: string) => iso.slice(11, 16);

export function day(date: string, opts: Intl.DateTimeFormatOptions = { weekday: "short", day: "numeric", month: "short" }): string {
  return new Date(`${date.slice(0, 10)}T12:00:00Z`).toLocaleDateString("en-GB", { ...opts, timeZone: "UTC" });
}

export function londonDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-CA", { timeZone: "Europe/London" });
}

export function addDays(date: string, days: number): string {
  const at = new Date(`${date}T12:00:00Z`);
  at.setUTCDate(at.getUTCDate() + days);
  return at.toISOString().slice(0, 10);
}

export function dayRange(from: string, to: string): string {
  const sameMonth = from.slice(0, 7) === to.slice(0, 7);
  return `${day(from, sameMonth ? { day: "numeric" } : { day: "numeric", month: "short" })}–${day(to, { day: "numeric", month: "short" })}`;
}

export function duration(from: string, to: string): string {
  const minutes = Math.round((Date.parse(to) - Date.parse(from)) / 60000);
  if (!Number.isFinite(minutes) || minutes <= 0) return "";
  const h = Math.floor(minutes / 60), m = minutes % 60;
  return h ? `${h}h${m ? ` ${String(m).padStart(2, "0")}m` : ""}` : `${m}m`;
}

export function nights(checkIn: string, checkOut: string): number {
  return Math.max(0, Math.round((Date.parse(checkOut) - Date.parse(checkIn)) / 86400000));
}

export function kickoff(fixture: Pick<Fixture, "kickoff_at" | "timezone">, timeZone = fixture.timezone): string {
  return new Date(fixture.kickoff_at).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone });
}

export function matchDay(fixture: Pick<Fixture, "kickoff_at" | "timezone">, opts: Intl.DateTimeFormatOptions = { weekday: "short", day: "numeric", month: "short" }): string {
  return new Date(fixture.kickoff_at).toLocaleDateString("en-GB", { ...opts, timeZone: fixture.timezone });
}

export function dateParts(fixture: Pick<Fixture, "kickoff_at">): { weekday: string; day: string; month: string } {
  const at = new Date(fixture.kickoff_at);
  const part = (o: Intl.DateTimeFormatOptions) => at.toLocaleDateString("en-GB", { ...o, timeZone: "Europe/London" });
  return { weekday: part({ weekday: "short" }), day: part({ day: "numeric" }), month: part({ month: "short" }) };
}

export function daysUntil(iso: string, now: Date): number {
  return Math.ceil((Date.parse(iso) - now.getTime()) / 86400000);
}
