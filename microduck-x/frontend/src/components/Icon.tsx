import "./icons.css";

// Line icons, drawn here (no icon font, nothing downloaded). Parts carry class names that
// styles.css animates: some move on hover (.ic-hover), some always (.ic-live), so a button
// shows what it does — waves leave the speaker, the gear turns, the planet's ring orbits.

export type IconName =
  | "palette" | "volume" | "mute" | "mic" | "chat" | "share" | "settings" | "camera" | "scenery" | "shuffle"
  | "duck" | "sparkle" | "close" | "chevron-left" | "chevron-right" | "send" | "stop" | "check" | "talk"
  | "sunrise" | "waves" | "snow" | "sun" | "mountain" | "city" | "sunset" | "star" | "planet" | "rocket" | "timer"
  | "copy" | "download" | "expand" | "collapse" | "maximize" | "refresh" | "thumb-up" | "thumb-down" | "more" | "lines" | "github"
  | "play" | "pause" | "replay" | "drive" | "menu";

const P: Record<IconName, JSX.Element> = {
  palette: <><path d="M12 3a9 9 0 1 0 0 18c1.2 0 1.8-.8 1.8-1.7 0-1.3-1-1.6-1-2.7 0-1 .8-1.6 1.8-1.6h2.1A4.3 4.3 0 0 0 21 10.7C21 6.4 17 3 12 3Z" />
    <g className="ic-spin-hover"><circle cx="7.5" cy="11" r="1.2" fill="currentColor" /><circle cx="10" cy="7" r="1.2" fill="currentColor" />
      <circle cx="14.5" cy="6.8" r="1.2" fill="currentColor" /><circle cx="17.4" cy="10" r="1.2" fill="currentColor" /></g></>,
  volume: <><path d="M4 9.5h3.2L12 5.5v13l-4.8-4H4z" fill="currentColor" stroke="none" />
    <path className="ic-wave w1" d="M15.5 9a4 4 0 0 1 0 6" /><path className="ic-wave w2" d="M18.3 6.5a7.6 7.6 0 0 1 0 11" /></>,
  mute: <><path d="M4 9.5h3.2L12 5.5v13l-4.8-4H4z" fill="currentColor" stroke="none" /><path className="ic-shake-hover" d="m16 9.5 5 5m0-5-5 5" /></>,
  mic: <><rect x="9" y="3.5" width="6" height="11" rx="3" /><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3" />
    <circle className="ic-ping" cx="12" cy="9" r="9" strokeWidth="1.2" /></>,
  chat: <><path d="M4 5.5h16v10.5H9.5L5 19.5V16H4z" strokeLinejoin="round" />
    <g className="ic-dots"><circle cx="8.6" cy="10.8" r="1" fill="currentColor" /><circle cx="12" cy="10.8" r="1" fill="currentColor" /><circle cx="15.4" cy="10.8" r="1" fill="currentColor" /></g></>,
  talk: <><path d="M5 18.5 6.2 15A7.5 7.5 0 1 1 9 17.6z" strokeLinejoin="round" /><path className="ic-wave w1" d="M9.5 10.5h5M9.5 13h3" /></>,
  share: <><circle cx="18" cy="5.5" r="2.6" /><circle cx="6" cy="12" r="2.6" /><circle cx="18" cy="18.5" r="2.6" /><path className="ic-draw" d="m8.4 13.3 7.2 4M15.6 6.8l-7.2 4" /></>,
  settings: <g className="ic-spin-hover"><circle cx="12" cy="12" r="3.2" />
    <path d="M12 2.8v2.4M12 18.8v2.4M2.8 12h2.4M18.8 12h2.4M5.5 5.5l1.7 1.7M16.8 16.8l1.7 1.7M5.5 18.5l1.7-1.7M16.8 7.2l1.7-1.7" /><circle cx="12" cy="12" r="6.6" /></g>,
  camera: <><path d="M4 8h3.5l1.6-2.5h5.8L16.5 8H20v11H4z" strokeLinejoin="round" /><circle className="ic-pulse-hover" cx="12" cy="13.2" r="3.4" /></>,
  scenery: <><rect x="3" y="4.5" width="18" height="15" rx="2.5" /><circle className="ic-rise" cx="15.8" cy="9.4" r="1.8" fill="currentColor" stroke="none" />
    <path d="m3.5 17.5 5.2-5.6 4 4.3 2.6-2.6 5.2 4.8" strokeLinejoin="round" /></>,
  shuffle: <><path className="ic-slide a" d="M3.5 7h3.2c4.6 0 6 10 10.6 10h3.2m0 0-2.6-2.6M20.5 17l-2.6 2.6" /><path className="ic-slide b" d="M3.5 17h3.2c1.7 0 2.8-1.3 3.7-3m3.2-4c.9-1.7 2-3 3.7-3h3.2m0 0-2.6-2.6M20.5 7l-2.6 2.6" /></>,
  duck: <g className="ic-waddle"><path d="M8.3 8.6a3.7 3.7 0 1 1 7.4 0c0 .9-.3 1.7-.8 2.4 3 .3 5.4 2.4 5.4 5 0 2.8-3.7 4.6-8.3 4.6S3.7 18.8 3.7 16c0-1.6.8-3 2.2-3.9" strokeLinejoin="round" />
    <path d="M15.7 8.2h2.8l-1.4 1.6" strokeLinejoin="round" /><circle cx="12.9" cy="7.6" r=".8" fill="currentColor" /></g>,
  sparkle: <><path className="ic-twinkle" d="M12 3.5 13.9 10l6.6 2-6.6 2L12 20.5 10.1 14l-6.6-2 6.6-2z" strokeLinejoin="round" /><path className="ic-twinkle d2" d="M19 3.5v3M17.5 5h3" /></>,
  close: <path className="ic-spin-hover" d="m6.5 6.5 11 11m0-11-11 11" />,
  "chevron-left": <path className="ic-nudge-l" d="m14.5 6-6 6 6 6" />,
  "chevron-right": <path className="ic-nudge-r" d="m9.5 6 6 6-6 6" />,
  send: <g className="ic-fly"><path d="M4 11.5 20 4l-6.5 16-2.8-6.7z" strokeLinejoin="round" /><path d="m10.7 13.3 4.3-4.3" /></g>,
  stop: <><path d="M8.3 3.5h7.4l4.8 4.8v7.4l-4.8 4.8H8.3l-4.8-4.8V8.3z" strokeLinejoin="round" /><path d="M9 12h6" strokeWidth="2.4" /></>,
  check: <path className="ic-draw" d="m5 12.5 4.3 4.3L19 7.2" />,
  sunrise: <><path d="M3 17.5h18M7 17.5a5 5 0 0 1 10 0" /><g className="ic-rays"><path d="M12 6.5v2.6M5.6 9.6l1.8 1.8M18.4 9.6l-1.8 1.8" /></g><path d="M8 20.5h8" /></>,
  waves: <g className="ic-waves"><path d="M2.5 9c2.4 0 2.4-2 4.8-2s2.4 2 4.7 2 2.4-2 4.7-2 2.4 2 4.8 2" /><path d="M2.5 13.5c2.4 0 2.4-2 4.8-2s2.4 2 4.7 2 2.4-2 4.7-2 2.4 2 4.8 2" /><path d="M2.5 18c2.4 0 2.4-2 4.8-2s2.4 2 4.7 2 2.4-2 4.7-2 2.4 2 4.8 2" /></g>,
  snow: <g className="ic-spin-slow"><path d="M12 2.5v19M3.8 7.2l16.4 9.6M3.8 16.8l16.4-9.6" /><path d="m9.5 4 2.5 2 2.5-2M9.5 20l2.5-2 2.5 2" /></g>,
  sun: <><circle cx="12" cy="12" r="4" /><g className="ic-spin-slow"><path d="M12 2.5v2.3M12 19.2v2.3M2.5 12h2.3M19.2 12h2.3M5.3 5.3l1.6 1.6M17.1 17.1l1.6 1.6M5.3 18.7l1.6-1.6M17.1 6.9l1.6-1.6" /></g></>,
  mountain: <><path d="m2.5 19.5 6.5-10 4 6 2.5-3.5 6 7.5z" strokeLinejoin="round" /><path className="ic-drift" d="M14 6.5a2.2 2.2 0 0 1 4.2-.6 1.8 1.8 0 1 1 .3 3.6h-4.6a1.5 1.5 0 0 1 .1-3" /></>,
  city: <><path d="M3 20.5h18M5 20.5V11h4v9.5M9 20.5V6.5h5v14M14 20.5V9.5h5v11" strokeLinejoin="round" />
    <g className="ic-blink"><path d="M11 9.5h1M11 12.5h1M16 12.5h1M7 14h0.01M16 15.5h1" strokeWidth="1.6" /></g></>,
  sunset: <><path d="M3 15.5h18M6.5 15.5a5.5 5.5 0 0 1 11 0" /><g className="ic-waves"><path d="M5 19c1.2 0 1.2-1 2.4-1s1.2 1 2.4 1 1.2-1 2.4-1 1.2 1 2.4 1 1.2-1 2.4-1 1.2 1 2.4 1" /></g></>,
  star: <path className="ic-twinkle" d="m12 3.5 2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z" strokeLinejoin="round" />,
  planet: <><circle cx="12" cy="12" r="5" /><ellipse className="ic-orbit" cx="12" cy="12" rx="10" ry="3.6" transform="rotate(-20 12 12)" /></>,
  rocket: <g className="ic-launch"><path d="M12 3c3 2 4.5 5.3 4.5 9.2L14.8 16H9.2l-1.7-3.8C7.5 8.3 9 5 12 3Z" strokeLinejoin="round" /><circle cx="12" cy="9.5" r="1.6" />
    <path d="M9.2 16 6.5 18l.6-4.3M14.8 16l2.7 2-.6-4.3M11 19.5h2" /></g>,
  timer: <><circle cx="12" cy="13" r="7.5" /><path d="M10 2.5h4" /><path className="ic-sweep" d="M12 13V8.5" /></>,
  copy: <><rect x="8.5" y="8.5" width="11" height="11" rx="2" /><path d="M15.5 8.5V6a1.5 1.5 0 0 0-1.5-1.5H6A1.5 1.5 0 0 0 4.5 6v8A1.5 1.5 0 0 0 6 15.5h2.5" /></>,
  download: <><path d="M12 4v11m0 0-4.2-4.2M12 15l4.2-4.2" /><path d="M4.5 16.5v2a1.5 1.5 0 0 0 1.5 1.5h12a1.5 1.5 0 0 0 1.5-1.5v-2" /></>,
  expand: <path d="m7 10 5 5 5-5" />,
  collapse: <path d="m7 14 5-5 5 5" />,
  maximize: <path d="M14.5 4.5h5v5M9.5 19.5h-5v-5M19.5 4.5l-6 6M4.5 19.5l6-6" />,
  refresh: <><path className="ic-spin-hover" d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3M19.5 4.5v4h-4" /></>,
  "thumb-up": <path d="M7.5 10.5v9h-3v-9zM7.5 10.5l3.6-6.3a1.8 1.8 0 0 1 3.3 1.3l-.9 3.9h5a1.8 1.8 0 0 1 1.8 2.1l-1.2 6.5a1.8 1.8 0 0 1-1.8 1.5H7.5" strokeLinejoin="round" />,
  "thumb-down": <path d="M7.5 13.5v-9h-3v9zM7.5 13.5l3.6 6.3a1.8 1.8 0 0 0 3.3-1.3l-.9-3.9h5a1.8 1.8 0 0 0 1.8-2.1l-1.2-6.5a1.8 1.8 0 0 0-1.8-1.5H7.5" strokeLinejoin="round" />,
  more: <><circle cx="5.5" cy="12" r="1.3" fill="currentColor" /><circle cx="12" cy="12" r="1.3" fill="currentColor" /><circle cx="18.5" cy="12" r="1.3" fill="currentColor" /></>,
  lines: <path d="M9.5 6.5h10M9.5 12h10M9.5 17.5h10M4.5 5.5v2M4 12h1.5M4.5 16.5v2" />,
  play: <path d="M8 5.5v13l10.5-6.5z" fill="currentColor" strokeLinejoin="round" />,
  pause: <path d="M8.5 5.5v13M15.5 5.5v13" strokeWidth="2.6" />,
  replay: <><path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3M4.5 4.5v4h4" /><path d="M10.5 9.5v5l4-2.5z" fill="currentColor" stroke="none" /></>,
  drive: <><circle cx="12" cy="12" r="8.5" /><path d="M12 6.5 14 9h-4zM12 17.5 10 15h4zM6.5 12 9 10v4zM17.5 12 15 14v-4z" fill="currentColor" stroke="none" /></>,
  menu: <path d="M4.5 7h15M4.5 12h15M4.5 17h15" />,
  github: <path fill="currentColor" stroke="none" fillRule="evenodd" clipRule="evenodd" d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.53 1.032 1.53 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0 1 12 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0 0 22 12.017C22 6.484 17.522 2 12 2Z" />,
};

export default function Icon({ name, size = 20, className = "" }: { name: IconName; size?: number; className?: string }) {
  return (
    <svg className={`ic ic-${name} ${className}`} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={1.8} strokeLinecap="round" aria-hidden="true">{P[name]}</svg>
  );
}

/** The icon for each world in sim/scenery.ts. */
export const SCENERY_ICON: Record<string, IconName> = {
  meadow: "sunrise", beach: "waves", snow: "snow", desert: "sun", hills: "mountain", city: "city", venice: "sunset",
  stars: "star", orbit: "planet", hangar: "rocket",
};
