"use client";
// A request to show one NISAR pass on the map (the river's dance bars): the
// Radar — NISAR view picks this date + track once.
import { useSyncExternalStore } from "react";

// `forDay`: the timeline day the map moves to (the nearest day when the
// pass falls outside the timeline) — the pick holds on that day.
export interface RadarPassRequest { date: string; track?: string; forDay?: string; n: number }
let req: RadarPassRequest | null = null;
const subs = new Set<() => void>();
export const requestRadarPass = (date: string, track?: string, forDay?: string) => {
  req = { date, ...(track ? { track } : {}), ...(forDay ? { forDay } : {}), n: (req?.n ?? 0) + 1 };
  subs.forEach((f) => f());
};
export const useRadarPassRequest = (): RadarPassRequest | null =>
  useSyncExternalStore((f) => { subs.add(f); return () => { subs.delete(f); }; }, () => req, () => null);
