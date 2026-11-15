"use client";

import { useEffect, useState } from "react";
import { deltaComposite, type BBox } from "./model/changeComposite";
import type { ChangeSpec } from "./model/nisarChange";

export interface ChangeImage {
  url: string; // PNG data URL of the classified composite
  bounds: BBox; // the after frame's bounds — the image source's coordinates
  key: string; // the pair + cyan flag this image was built for
}

// The Δ-dB composite of the resolved pair as a PNG data URL for the map's
// image source. Same canvas plumbing as ChangeMap: the two frame PNGs are
// decoded to ImageData, classified per pixel (red ≥3 dB darker, cyan ≥3 dB
// brighter, transparent under 3 dB) and put back as one transparent PNG.
// Undefined while the pair loads, on a reason spec, or while off.
export function useNisarChangeImage(
  spec: ChangeSpec | undefined,
  on: boolean,
  cyan: boolean
): ChangeImage | undefined {
  const pair = on && spec && "pair" in spec ? spec.pair : undefined;
  const key = pair
    ? `${pair.before.url}|${pair.after.url}|${pair.after.pol}|${cyan ? "c" : "r"}`
    : "";
  const [img, setImg] = useState<ChangeImage>();
  useEffect(() => {
    if (!key || !pair) return;
    let alive = true;
    const load = (src: string) =>
      new Promise<HTMLImageElement>((ok, fail) => {
        const im = new Image();
        im.crossOrigin = "anonymous";
        im.onload = () => ok(im);
        im.onerror = () => fail(new Error(src));
        im.src = src;
      });
    const pixels = (im: HTMLImageElement) => {
      const c = document.createElement("canvas");
      c.width = im.naturalWidth;
      c.height = im.naturalHeight;
      const ctx = c.getContext("2d", { willReadFrequently: true })!;
      ctx.drawImage(im, 0, 0);
      return ctx.getImageData(0, 0, c.width, c.height);
    };
    Promise.all([load(pair.before.url), load(pair.after.url)])
      .then(([b, a]) => {
        const before = pixels(b), after = pixels(a);
        const out = deltaComposite(
          { data: before.data, width: before.width, height: before.height, bounds: pair.before.bounds, stretch: pair.before.stretch },
          { data: after.data, width: after.width, height: after.height, bounds: pair.after.bounds, stretch: pair.after.stretch },
          cyan
        );
        const c = document.createElement("canvas");
        c.width = after.width;
        c.height = after.height;
        c.getContext("2d")!.putImageData(new ImageData(out, after.width, after.height), 0, 0);
        if (alive) setImg({ url: c.toDataURL("image/png"), bounds: pair.after.bounds, key });
      })
      .catch(() => {
        if (alive) setImg(undefined);
      });
    return () => {
      alive = false;
    };
    // `key` names the pair + the cyan flag; `pair` is a fresh object each
    // render (resolveChangePair), so it cannot be the dep itself.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  // A new pair hides the stale composite until its own PNG is ready.
  return img && img.key === key ? img : undefined;
}
