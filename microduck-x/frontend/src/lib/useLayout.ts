import { useEffect, useState } from "react";

// Which layout the hangar uses, from the viewport — not from the device's name:
//
//   sheet  portrait phones and tablets: the scene fills the screen, chat is a bottom sheet
//   dock   landscape below 1280 px (phones on their side, tablets): scene | chat, side by side
//   float  1280 px and wider: the desktop layout, chat floating over the scene
//
// It also publishes the on-screen keyboard's height as --kb on <html>, from visualViewport, so
// the composer can sit above the keyboard where the browser does not resize the page for it.

export type LayoutMode = "sheet" | "dock" | "float";

export interface Layout { mode: LayoutMode; width: number; height: number; touch: boolean; reducedMotion: boolean }

function read(): Layout {
  const width = window.innerWidth, height = window.innerHeight;
  const portrait = height >= width;
  const mode: LayoutMode = width >= 1280 ? "float" : portrait ? "sheet" : "dock";
  return {
    mode, width, height,
    touch: window.matchMedia("(pointer: coarse)").matches,
    reducedMotion: window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  };
}

export function useLayout(): Layout {
  const [layout, setLayout] = useState<Layout>(read);
  useEffect(() => {
    let raf = 0;
    const update = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const next = read();
        setLayout((prev) => prev.mode === next.mode && prev.width === next.width && prev.height === next.height
          && prev.touch === next.touch && prev.reducedMotion === next.reducedMotion ? prev : next);
        const vv = window.visualViewport;
        // what the keyboard (or other browser UI) covers at the bottom of the layout viewport
        const kb = vv ? Math.max(0, Math.round(window.innerHeight - vv.height - vv.offsetTop)) : 0;
        document.documentElement.style.setProperty("--kb", `${kb > 80 ? kb : 0}px`);
      });
    };
    window.addEventListener("resize", update);
    window.addEventListener("orientationchange", update);
    window.visualViewport?.addEventListener("resize", update);
    window.visualViewport?.addEventListener("scroll", update);
    const mq = [window.matchMedia("(pointer: coarse)"), window.matchMedia("(prefers-reduced-motion: reduce)")];
    mq.forEach((m) => m.addEventListener("change", update));
    update();
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", update);
      window.removeEventListener("orientationchange", update);
      window.visualViewport?.removeEventListener("resize", update);
      window.visualViewport?.removeEventListener("scroll", update);
      mq.forEach((m) => m.removeEventListener("change", update));
    };
  }, []);
  return layout;
}
