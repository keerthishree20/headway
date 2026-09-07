"use client";

import { useEffect, useState } from "react";

/**
 * Resolves CSS custom properties to concrete colours.
 *
 * Leaflet's canvas renderer writes straight to a 2D context, which cannot
 * parse `var(--token)` -- the marker would silently not paint. So the map
 * reads the computed values here and re-reads them when the OS theme flips.
 */
export function useThemeTokens<T extends string>(
  names: readonly T[],
): Record<T, string> {
  const read = () => {
    const out = {} as Record<T, string>;
    if (typeof window === "undefined") {
      names.forEach((n) => (out[n] = "#888888"));
      return out;
    }
    const style = getComputedStyle(document.documentElement);
    names.forEach((n) => {
      out[n] = style.getPropertyValue(n).trim() || "#888888";
    });
    return out;
  };

  const [tokens, setTokens] = useState<Record<T, string>>(read);

  useEffect(() => {
    setTokens(read());
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => setTokens(read());
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [names.join(",")]);

  return tokens;
}
