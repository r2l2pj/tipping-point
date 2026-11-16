"use client";
// The risk look on NISAR views (conductor, Lucas): 'hatch' (default — yellow
// lines over the NISAR image) | 'fill' (the usual colour classes). A canvas
// re-render of the day's TPI class PNG, cached per URL (blob URL).
import { useEffect, useState, useSyncExternalStore } from "react";
import { hatchRgba } from "./model/riskHatch";

export type RiskLook = "hatch" | "fill";
let look: RiskLook = "hatch";
const subs = new Set<() => void>();
export const setRiskLook = (l: RiskLook) => { look = l; subs.forEach((f) => f()); };
export const useRiskLook = (): RiskLook =>
  useSyncExternalStore((f) => { subs.add(f); return () => { subs.delete(f); }; }, () => look, () => "hatch");

const cache = new Map<string, Promise<string>>();
export function hatchRisk(url: string): Promise<string> {
  let p = cache.get(url);
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
          const out = hatchRgba(d.data, c.width, c.height);
          const o = document.createElement("canvas");
          o.width = out.width; o.height = out.height;
          o.getContext("2d")!.putImageData(new ImageData(out.data as Uint8ClampedArray<ArrayBuffer>, out.width, out.height), 0, 0);
          o.toBlob((b) => (b ? resolve(URL.createObjectURL(b)) : reject(new Error("toBlob"))), "image/png");
        } catch (e) { reject(e); }
      };
      img.onerror = () => reject(new Error(`load ${url}`));
      img.src = url;
    });
    p.catch(() => cache.delete(url));
    cache.set(url, p);
  }
  return p;
}

// The hatched URL once ready; undefined while it renders or when off (the
// caller paints the colour fill meanwhile).
export function useRiskHatchUrl(url: string | undefined, on: boolean): string | undefined {
  const [done, setDone] = useState<{ src: string; out: string } | null>(null);
  useEffect(() => {
    if (!on || !url) return;
    let live = true;
    hatchRisk(url).then((out) => { if (live) setDone({ src: url, out }); }).catch(() => {});
    return () => { live = false; };
  }, [on, url]);
  return on && url && done?.src === url ? done.out : undefined;
}
