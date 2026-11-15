"use client";
// L61: "Flood risk + NISAR" is the default Flood look; "Rain index only"
// switches back to the plain flood index. One switch for the map, the
// legend and the caption.
import { useSyncExternalStore } from "react";

let on = true;
const subs = new Set<() => void>();
export const setFloodNisar = (v: boolean) => { on = v; subs.forEach((f) => f()); };
export const useFloodNisar = (): boolean =>
  useSyncExternalStore((f) => { subs.add(f); return () => { subs.delete(f); }; }, () => on, () => true);
