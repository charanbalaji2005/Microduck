import { useEffect, useRef, useState } from "react";
import { chat, chatCancel, chatClear, chatSession, clearEstop, errorText, estop, getJson, postJson } from "../lib/api";
import { clearChatView, setState, setUi, useStore, type ChatMsg } from "../lib/store";
import AIMessage from "../components/chat/AIMessage";
import ShowcaseViewer from "../sim/ShowcaseViewer";
import DrivePad from "../components/DrivePad";
import Icon from "../components/Icon";
import { DEFAULT_LOOK, PART_LABELS, loadLook, saveLook, shareLink, type DuckLook, type PartName } from "../sim/duckLook";
import { behaviorManager } from "../lib/autonomousBehavior";
import { speechInput } from "../voice/speechRecognition";
import { playSoundEvent, replayReply, setMuted, setVolume, stopTalking } from "../voice/events";
import { unlockVoice } from "../voice/duckVoice";
import VoiceControls, { setVoiceMuted } from "../voice/VoiceControls";
import { stopReading } from "../lib/clipboard";

// The complete Microduck X / Microquack interactive experience:
// 3D Orbital Hangar, authentic procedural droid-voice synthesis, autonomous behaviors,
// speech bubble, chat interface, and customization paint shop.

const CHIPS = [
  "Hello!", "Status report", "Where are we?", "Good duck!", "Tell me a joke", "Watch out!", "Good night",
  "Come here", "Look around", "Explore", "Play", "Rest", "Stand up", "Dance", "Spin", "Forward",
  "Left", "Right", "Nod", "Sit", "Home", "Stop"
];

function chatShift(open: boolean) {
  const w = typeof window === "undefined" ? 1400 : window.innerWidth;
  return open && w > 900 ? Math.min(260, w * 0.17) : 0;
}

function chatShiftUp(open: boolean) {
  const w = typeof window === "undefined" ? 1400 : window.innerWidth;
  const h = typeof window === "undefined" ? 900 : window.innerHeight;
  return open && w <= 900 ? Math.min(140, h * 0.18) : 0;
}

const MAX_CHARS = 2000; // the service's MDX_CHAT_MAX_CHARS default; the service is the one that enforces it

function moodLabel(m?: string) { return (m ?? "").toUpperCase(); }
function clip(s: string, n: number) {
  if (s.length <= n) return s;
  const cut = s.lastIndexOf(" ", n);
  return s.slice(0, cut > 0 ? cut : n) + "…";
}
const plain = (s: string) => s.replace(/```[\s\S]*?(```|$)/g, " ").replace(/[`*#>]/g, "").replace(/\s+/g, " ").trim();

function Bubble({ msg }: { msg: ChatMsg | null }) {
  const [shown, setShown] = useState<ChatMsg | null>(null);
  useEffect(() => {
    if (!msg || Date.now() / 1000 - msg.t > 30) return;
    setShown(msg);
    const h = setTimeout(() => setShown(null), Math.min(16000, 7000 + msg.text.length * 80));
    return () => clearTimeout(h);
  }, [msg?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!shown) return null;

  return (
    <div className="thought" key={shown.id}>
      <div className="thought-content">
        {shown.babble && (
          <div className="thought-babble">{clip(shown.babble, 90)}</div>
        )}
        <div className="thought-text">{clip(plain(shown.text), 220)}</div>
        {shown.mood && (
          <div className="thought-mood-badge">{moodLabel(shown.mood)}</div>
        )}
      </div>
      <div className="thought-tail" aria-hidden="true">
        <svg viewBox="0 0 32 22" width="32" height="22">
          <path
            d="M3 0 C9 7, 12 14, 4 22 C16 17, 24 9, 29 0 Z"
            fill="#fbfcfe"
            stroke="rgba(15, 23, 42, 0.16)"
            strokeWidth="2"
            strokeLinejoin="round"
          />
          <line x1="3" y1="0" x2="29" y2="0" stroke="#fbfcfe" strokeWidth="4" />
        </svg>
      </div>
    </div>
  );
}

export default function Hangar() {
  const bubbleAnchor = useRef<HTMLDivElement>(null);
  const ringAnchor = useRef<HTMLDivElement>(null);
  const [look, setLook] = useState<DuckLook>(loadLook);
  const [name, setName] = useState("");
  const [customizing, setCustomizing] = useState(false);
  const [text, setText] = useState("");
  const [toast, setToast] = useState<string | null>(null);
  const [isSpeakingInput, setIsSpeakingInput] = useState(false);
  const [sending, setSending] = useState(false);

  const msgs = useStore((s) => s.chat);
  const bubble = useStore((s) => s.bubble);
  const ui = useStore((s) => s.ui);
  const st = useStore((s) => s.status);
  const voiceError = useStore((s) => s.voiceError);
  const listEnd = useRef<HTMLDivElement>(null);
  const thinking = msgs.find((m) => m.pending && m.session === chatSession);
  const brain = st?.brain;
  const muted = ui.muted;

  // the synth starts at its own defaults; give it what this browser chose last time
  useEffect(() => { setMuted(ui.muted); setVolume(ui.volume); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    getJson("/api/duck").then((m) => { setName(m.duck_name === "Microduck" ? "" : m.duck_name ?? ""); }).catch(() => undefined);
  }, []);

  useEffect(() => {
    listEnd.current?.scrollIntoView({ block: "end" });
  }, [msgs.length, ui.chatOpen, thinking?.text.length]);

  // Space: a tap stops the duck (lib/keyboard.ts, for every page); held, it is push-to-talk too.
  const talkTimer = useRef<number | undefined>(undefined);
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.code === "Space" && !e.repeat && e.target === document.body && !isSpeakingInput && !customizing) {
        window.clearTimeout(talkTimer.current);
        talkTimer.current = window.setTimeout(startVoiceInput, 280);
      }
    };
    const handleKeyUp = (e: KeyboardEvent) => {
      if (e.code !== "Space") return;
      window.clearTimeout(talkTimer.current);
      if (isSpeakingInput) {
        e.preventDefault();
        stopVoiceInput();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    window.addEventListener("keyup", handleKeyUp);
    return () => {
      window.clearTimeout(talkTimer.current);
      window.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("keyup", handleKeyUp);
    };
  }, [isSpeakingInput, customizing]); // eslint-disable-line react-hooks/exhaustive-deps

  const startVoiceInput = () => {
    if (!speechInput.isSupported()) {
      setToast("Speech recognition not supported in this browser. Please type your message.");
      setTimeout(() => setToast(null), 3000);
      return;
    }
    setIsSpeakingInput(true);
    speechInput.start(
      (transcript, isFinal) => {
        setText(transcript);
        if (isFinal) {
          setIsSpeakingInput(false);
          say(transcript);
        }
      },
      (err) => {
        setIsSpeakingInput(false);
        setToast(`Voice input: ${err}`);
        setTimeout(() => setToast(null), 3000);
      }
    );
  };

  const stopVoiceInput = () => {
    speechInput.stop();
    setIsSpeakingInput(false);
  };

  const flash = (message: string, ms = 3000) => {
    setToast(message);
    setTimeout(() => setToast(null), ms);
  };

  // One message in flight at a time. A stop or a chip still goes while the brain is answering;
  // a second open question is refused by the service (409) and offered again with "Try again".
  const say = async (t: string, regenerate = false) => {
    const v = t.trim();
    if (!v) { flash("Type a message first."); return; }
    if (v.length > MAX_CHARS) { flash(`That message is ${v.length} characters; the limit is ${MAX_CHARS}.`); return; }
    if (sending) return;
    setText("");
    setSending(true);
    // a new request interrupts the old answer's voice and any reading aloud; the legs are untouched
    stopTalking();
    stopReading();
    unlockVoice(); // a click or Enter is the gesture browsers want before audio
    behaviorManager.recordInteraction();
    try {
      await chat(v, "chat", regenerate);
    } catch (e) {
      setState((s) => ({
        chat: [...s.chat, { id: `e${Date.now()}`, who: "duck", mood: "sorry", t: Date.now() / 1000, text: errorText(e),
                            error: "request", retry: v }]
      }));
    } finally {
      setSending(false);
    }
  };

  const stopAnswer = () => { chatCancel().catch(() => undefined); };

  const clearConversation = () => {
    stopTalking();
    chatClear().catch(() => undefined); // the brain forgets this session's context
    clearChatView();
  };

  const retryVoice = () => {
    unlockVoice();
    setState({ voiceError: null });
    if (!ui.muted) replayReply();
  };

  const confirm = async (m: ChatMsg, yes: boolean) => {
    behaviorManager.recordInteraction();
    setState((s) => ({
      chat: s.chat.map((x) => (x.id === m.id ? { ...x, confirm: undefined, text: yes ? x.text : "Okay, ignored." } : x))
    }));
    if (yes && m.confirm) await chat(m.confirm, "voice");
  };

  const toggleMute = () => setVoiceMuted(!muted);

  const setPart = (p: PartName, c: string) => {
    const next = { ...look, [p]: c };
    setLook(next);
    saveLook(next);
  };

  const finishCustomize = async () => {
    setCustomizing(false);
    behaviorManager.recordInteraction();
    playSoundEvent("success");
    await postJson("/api/duck", { duck_name: name || "Microduck", look }).catch(() => undefined);
  };

  const share = async () => {
    const link = shareLink(look, name || "Microduck");
    try {
      await navigator.clipboard.writeText(link);
      setToast("Link copied — it carries your duck's colors.");
    } catch {
      setToast(link);
    }
    setTimeout(() => setToast(null), 3500);
  };

  const resetFactoryColors = () => {
    setLook({ ...DEFAULT_LOOK });
    saveLook({ ...DEFAULT_LOOK });
  };

  const [menuOpen, setMenuOpen] = useState(false);

  const handleDuckClick = () => {
    setMenuOpen((prev) => {
      const next = !prev;
      if (next) {
        playSoundEvent("acknowledgment");
      }
      return next;
    });
  };

  const handleCanvasClick = () => {
    if (menuOpen) setMenuOpen(false);
  };

  const estopped = !!st?.safety.estop;
  const parts = Object.keys(PART_LABELS) as PartName[];

  return (
    <div className={`hangar ${customizing ? "customizing" : ""}`}>
      <ShowcaseViewer
        look={look}
        customizing={customizing}
        bloom={ui.bloom}
        shiftLeft={chatShift(ui.chatOpen)}
        shiftUp={chatShiftUp(ui.chatOpen)}
        anchors={{ bubble: bubbleAnchor, ring: ringAnchor }}
        onDuckClick={handleDuckClick}
        onCanvasClick={handleCanvasClick}
      />

      <div className="anchor" ref={bubbleAnchor}>
        {!customizing && <Bubble msg={bubble} />}
      </div>

      <div className="anchor" ref={ringAnchor}>
        {!customizing && menuOpen && (
          <div className="ring open">
            <button
              className="orb orb-top"
              onClick={() => { setCustomizing(true); setMenuOpen(false); }}
              title="Customize duck colors"
            >
              <span className="orb-icon"><Icon name="palette" size={18} /></span>
              <span>Customize</span>
            </button>

            <button
              className={`orb orb-left ${muted ? "on" : ""}`}
              onClick={toggleMute}
              title="Mute/unmute procedural audio"
            >
              <span className="orb-icon"><Icon name={muted ? "mute" : "volume"} size={18} /></span>
              <span>{muted ? "Muted" : "Mute"}</span>
            </button>

            <button
              className={`orb orb-right ${isSpeakingInput ? "rec" : ""}`}
              title="Hold to speak (or hold Spacebar)"
              onMouseDown={startVoiceInput}
              onMouseUp={stopVoiceInput}
              onMouseLeave={() => isSpeakingInput && stopVoiceInput()}
            >
              <span className="orb-icon"><Icon name={isSpeakingInput ? "mic" : "talk"} size={18} /></span>
              <span>{isSpeakingInput ? "Listening" : "Speak"}</span>
            </button>
          </div>
        )}
      </div>

      <header className="h-top">
        <div className="h-brand">
          <div className="h-title">{customizing ? "MICRODUCK X" : (name || "MICRODUCK X").toUpperCase()}</div>
          <div className="h-sub">{st?.mock ? "MOCK robot · not MuJoCo, not the Microduck · the duck's own voice"
            : "real Microduck model · MuJoCo physics · procedural droid voice"}</div>
        </div>

        {customizing && (
          <div className="h-build">
            <div className="h-build-title">BUILD YOUR DUCK</div>
            <input className="h-name" placeholder="Name your duck…" value={name} maxLength={24} onChange={(e) => setName(e.target.value)} />
          </div>
        )}

        <div className="h-actions">
          <a
            className="h-btn"
            href="https://github.com/charanbalaji2005/Microduck-AI"
            target="_blank"
            rel="noopener noreferrer"
            title="Microduck-AI on GitHub"
          >
            <Icon name="github" size={16} />
            <span>GitHub</span>
          </a>
          {!customizing && (
            <button className="h-btn" onClick={share} title="Share your duck">
              <Icon name="share" size={16} />
              <span>Share</span>
            </button>
          )}
          {estopped ? (
            <button className="h-estop latched" onClick={() => clearEstop()}>
              <Icon name="stop" size={15} />
              <span>E-STOP LATCHED · clear</span>
            </button>
          ) : (
            <button className="h-estop" onClick={() => estop()} title="Emergency stop (Esc)">
              <Icon name="stop" size={15} />
              <span>STOP</span>
            </button>
          )}
        </div>
      </header>

      {customizing && (
        <>
          <div className="swatches left">
            {parts.slice(0, 3).map((p) => (
              <label key={p} className="swatch">
                <span>{PART_LABELS[p]}</span>
                <input type="color" value={look[p]} onChange={(e) => setPart(p, e.target.value)} />
              </label>
            ))}
          </div>
          <div className="swatches right">
            {parts.slice(3).map((p) => (
              <label key={p} className="swatch">
                <span>{PART_LABELS[p]}</span>
                <input type="color" value={look[p]} onChange={(e) => setPart(p, e.target.value)} />
              </label>
            ))}
          </div>
          <div className="h-bottom">
            <button className="h-yellow" onClick={share}>Share your duck</button>
            <button className="h-dark" onClick={resetFactoryColors}>Factory colours</button>
            <button className="h-dark" onClick={finishCustomize}>Done — back to the hangar</button>
          </div>
        </>
      )}

      {!customizing && (
        <aside className={`h-chat ${ui.chatOpen ? "" : "closed"}`}>
          <button
            className="h-chat-toggle"
            onClick={() => setUi({ chatOpen: !ui.chatOpen })}
            title={ui.chatOpen ? "Close chat" : "Open chat"}
            aria-label={ui.chatOpen ? "Close chat" : "Open chat"}
          >
            <span className="toggle-desktop">{ui.chatOpen ? "›" : "‹"}</span>
            <span className="toggle-mobile">{ui.chatOpen ? "✕" : "💬 Chat"}</span>
          </button>
          <div className="h-chat-bar">
            <span className={`h-brain ${brain?.enabled ? "on" : ""}`}
                  title={brain?.enabled ? `Open questions go to Groq (${brain.model}, reasoning ${brain.reasoning_effort}); `
                    + (brain.retrieval ? "web lookups on." : "no web lookups.") : brain?.problem ?? "status unknown"}>
              {brain?.enabled ? `Groq · ${brain.model?.split("/").pop()}` : "Groq brain off"}
            </span>
            {thinking && <button onClick={stopAnswer} title="Stop the answer being written (Esc in the message box)">Stop answer</button>}
            <button onClick={() => setUi({ duckSpeak: !ui.duckSpeak })} aria-pressed={ui.duckSpeak}
                    title="Show the duck's own noises above its answers">Duck-speak {ui.duckSpeak ? "on" : "off"}</button>
            <button onClick={clearConversation} title="Clear the chat and the brain's memory of this conversation">Clear</button>
          </div>
          <VoiceControls />
          {voiceError && (
            <div className="h-voice-err">
              {voiceError} <button onClick={retryVoice}>Retry voice</button>
            </div>
          )}
          <div className="h-msgs" aria-live="polite">
            {msgs.slice(-40).map((m) =>
              m.who === "user" ? (
                <div key={m.id} className="h-user">{m.text}</div>
              ) : (
                <AIMessage
                  key={m.id}
                  m={m}
                  duckSpeak={ui.duckSpeak}
                  busy={sending || !!thinking}
                  onAsk={(q) => say(q)}
                  onRegenerate={(q) => say(q, true)}
                  onConfirm={(ok) => confirm(m, ok)}
                />
              )
            )}
            {!msgs.length && (
              <div className="h-duck">
                <div className="h-text">
                  Say hi! Try “dance”, “spin”, “where are we”{brain?.enabled ? ", or ask me anything — “why is the sky blue?”" : ""}. Click the duck to interact!
                </div>
              </div>
            )}
            <div ref={listEnd} />
          </div>

          <div className="h-chips">
            {CHIPS.map((c) => (
              <button key={c} onClick={() => say(c)}>{c}</button>
            ))}
          </div>

          <form className="h-input" onSubmit={(e) => { e.preventDefault(); say(text); }}>
            <input
              value={text}
              maxLength={MAX_CHARS}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Escape" && thinking) { e.preventDefault(); stopAnswer(); } }}
              placeholder={isSpeakingInput ? "Listening to your voice…" : `Say something to ${name || "your duck"}…`}
            />
            <button className="h-yellow" type="submit" disabled={sending}>{sending ? "…" : "Send"}</button>
          </form>
        </aside>
      )}

      {!customizing && <DrivePad />}

      <footer className={`h-foot ${ui.chatOpen ? "chat-open" : ""}`}>
        <span className="h-credit">built &amp; designed by <span className="h-credit-author">Neelampalli Charan Balaji</span></span>
      </footer>

      {toast && <div className="h-toast">{toast}</div>}
    </div>
  );
}
