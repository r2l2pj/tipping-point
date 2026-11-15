"use client";
// "Water in red" (Lucas): the look of every NISAR image — one switch shared
// by the Radar view and the Compare sides ('Water in red' default, 'Plain
// radar'), and a canvas recolour of the PNG cached per frame (blob URL).
import { useEffect, useState, useSyncExternalStore } from "react";
import type { Stretch } from "./model/changeComposite";
import { DEFAULT_STRETCH, waterRamp } from "./model/waterRamp";

export type RadarLook = "water" | "plain";
let look: RadarLook = "water";
const subs = new Set<() => void>();
export const setRadarLook = (l: RadarLook) => { look = l; subs.forEach((f) => f()); };
export const useRadarLook = (): RadarLook =>
  useSyncExternalStore((f) => { subs.add(f); return () => { subs.delete(f); }; }, () => look, () => "water");

const cache = new Map<string, Promise<string>>();
const keyOf = (url: string, s?: Stretch) => `${url}|${s ? `${s.min},${s.max}` : "default"}`;

export function recolorWater(url: string, stretch?: Stretch): Promise<string> {
  const k = keyOf(url, stretch);
  let p = cache.get(k);
  if (!p) {
    p = new Promise<string>((resolve, reject) => {
      const img = new Image();
      img.crossOrigin = "anonymous";
      img.onload = () => {
        try {
          const c = document.createElement("canvas");
          c.width = img.naturalWidth; c.height = img.naturalHeight;
          const ctx = c.getContext("2d", { willReadFrequently: true });
          if (!ctx) throw new Error("no 2d context");
          ctx.drawImage(img, 0, 0);
          const d = ctx.getImageData(0, 0, c.width, c.height);
          ctx.putImageData(new ImageData(waterRamp(d.data, stretch ?? DEFAULT_STRETCH), c.width, c.height), 0, 0);
          c.toBlob((b) => (b ? resolve(URL.createObjectURL(b)) : reject(new Error("toBlob"))), "image/png");
        } catch (e) { reject(e); }
      };
      img.onerror = () => reject(new Error(`load ${url}`));
      img.src = url;
    });
    p.catch(() => cache.delete(k));
    cache.set(k, p);
  }
  return p;
}

// The URL to paint: the recoloured frame once ready (the plain PNG meanwhile,
// on failure, or with the 'Plain radar' look).
export function useWaterUrl(url: string | undefined, stretch: Stretch | undefined, on: boolean): string | undefined {
  const [done, setDone] = useState<{ src: string; out: string } | null>(null);
  const smin = stretch?.min, smax = stretch?.max;
  const k = url ? `${url}|${smin ?? ""},${smax ?? ""}` : "";
  useEffect(() => {
    if (!on || !url) return;
    let live = true;
    const s = smin !== undefined && smax !== undefined ? { min: smin, max: smax } : undefined;
    recolorWater(url, s).then((out) => { if (live) setDone({ src: k, out }); }).catch(() => {});
    return () => { live = false; };
  }, [on, url, smin, smax, k]);
  if (!on || !url) return url;
  return done?.src === k ? done.out : url;
}

// L61 (Lucas: "NISAR flooded area in RED"): a one-colour mask PNG (NISAR
// water, published blue) repainted in one colour, alpha kept. Cached.
const tints = new Map<string, Promise<string>>();
export function tintMask(url: string, rgb: readonly [number, number, number]): Promise<string> {
  const k = `${url}|${rgb.join(",")}`;
  let p = tints.get(k);
  if (!p) {
    p = new Promise<string>((resolve, reject) => {
      const img = new Image();
      img.crossOrigin = "anonymous";
      img.onload = () => {
        try {
          const c = document.createElement("canvas");
          c.width = img.naturalWidth; c.height = img.naturalHeight;
          const ctx = c.getContext("2d", { willReadFrequently: true });
          if (!ctx) throw new Error("no 2d context");
          ctx.drawImage(img, 0, 0);
          const d = ctx.getImageData(0, 0, c.width, c.height);
          for (let i = 0; i < d.data.length; i += 4) {
            if (d.data[i + 3] === 0) continue;
            d.data[i] = rgb[0]; d.data[i + 1] = rgb[1]; d.data[i + 2] = rgb[2];
          }
          ctx.putImageData(d, 0, 0);
          c.toBlob((b) => (b ? resolve(URL.createObjectURL(b)) : reject(new Error("toBlob"))), "image/png");
        } catch (e) { reject(e); }
      };
      img.onerror = () => reject(new Error(`load ${url}`));
      img.src = url;
    });
    p.catch(() => tints.delete(k));
    tints.set(k, p);
  }
  return p;
}

// The tinted URL once ready; undefined meanwhile (never flash the blue mask).
export function useTintedUrl(url: string | undefined, rgb: readonly [number, number, number]): string | undefined {
  const [done, setDone] = useState<{ src: string; out: string } | null>(null);
  const [r, g, b] = rgb;
  useEffect(() => {
    if (!url) return;
    let live = true;
    tintMask(url, [r, g, b]).then((out) => { if (live) setDone({ src: url, out }); }).catch(() => {});
    return () => { live = false; };
  }, [url, r, g, b]);
  return url && done?.src === url ? done.out : undefined;
}

// The NISAR compare mode ('side' | 'change'), kept across the overlay's
// re-mounts (a new pass pair re-mounts it).
export type CompareMode = "side" | "change";
let cmode: CompareMode = "side";
const csubs = new Set<() => void>();
const setCompareMode = (m: CompareMode) => { cmode = m; csubs.forEach((f) => f()); };
export function useCompareMode(): [CompareMode, (m: CompareMode) => void] {
  const m = useSyncExternalStore((f) => { csubs.add(f); return () => { csubs.delete(f); }; }, () => cmode, () => "side" as CompareMode);
  return [m, setCompareMode];
}
