"use client";

import { useEffect, useState } from "react";

/** Tracks the effective colour scheme so the map can pick a matching basemap. */
export function useIsDark(): boolean {
  const [dark, setDark] = useState(false);

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const resolve = () => {
      const stamped = document.documentElement.getAttribute("data-theme");
      setDark(stamped ? stamped === "dark" : media.matches);
    };
    resolve();
    media.addEventListener("change", resolve);
    return () => media.removeEventListener("change", resolve);
  }, []);

  return dark;
}
