// Interface sounds, in the duck's real voice. Each event plays one of the robot's own tags
// (duckVoice.ts), picked the way robotd uses them: greet when it wakes, chirp to acknowledge,
// inquire for a question, alarm for a warning, coo when it settles, wheee for the joy ride.

import { useSyncExternalStore } from "react";
import { speakable } from "../lib/chatContent";
import { stopReading } from "../lib/clipboard";
import {
  isSpeaking, onVoiceChange, pauseVoice, play, resumeVoice, setVoiceMuted, setVoiceVolume, speak, stopVoice,
  voiceBlocked, voiceLevel, voicePaused, type Tag,
} from "./duckVoice";

export type SoundEvent =
  | "greeting" | "happiness" | "curiosity" | "confusion" | "excitement"
  | "warning" | "play" | "acknowledgment" | "resting" | "success";

const EVENT_TAGS: Record<SoundEvent, Tag[]> = {
  greeting: ["greet"], happiness: ["chirp", "chirp"], curiosity: ["inquire"], confusion: ["peck", "inquire"],
  excitement: ["greet", "chirp"], warning: ["alarm"], play: ["wheee"], acknowledgment: ["chirp"],
  resting: ["coo"], success: ["greet", "chirp"],
};

/** The identity whose voice the main duck speaks with — robotd's DUCK_IDENTITY in the sim. */
let mainDuck = "duck-a";
export function setMainDuck(id: string) { mainDuck = id || "duck-a"; }
export function mainDuckId() { return mainDuck; }

export function playSoundEvent(event: SoundEvent, id = mainDuck) {
  for (const tag of EVENT_TAGS[event]) play(id, tag);
}

// A long answer is said as several utterances, one per stretch of sentences, with a breath between
// them — never one per word, and never more than MAX_CHUNKS, so a page of text is a few seconds
// of duck rather than a minute. The robot has no words; the chunks only give the noise the
// answer's shape. Code and equations are counted as one mention each (chatContent.speakable).
const CHUNK_WORDS = 24;
const MAX_CHUNKS = 6;
const SENTENCE_GAP_S = 0.32;

export function speechChunks(text: string): string[] {
  const sentences = speakable(text).split(/(?<=[.!?])\s+/).filter((s) => /\w/.test(s));
  const chunks: string[] = [];
  let cur: string[] = [];
  for (const s of sentences) {
    cur.push(s);
    if (cur.join(" ").split(/\s+/).length >= CHUNK_WORDS) { chunks.push(cur.join(" ")); cur = []; }
  }
  if (cur.length) chunks.push(cur.join(" "));
  return chunks.slice(0, MAX_CHUNKS);
}

interface Said { mood?: string; babble?: string; text?: string }
let last: Said | null = null;

/** A chat reply aloud: the mood picks the tags, the babble's length sets how many; a long answer
 *  adds a short utterance per chunk of its text. Whatever the duck was saying stops first, so two
 *  replies never talk over each other. Returns when it will have finished (0: it could not play). */
export function sayReply(mood?: string, babble?: string, text?: string, id = mainDuck) {
  last = { mood, babble, text };
  stopReading(); // the browser's read-aloud and the duck would talk over each other
  stopVoice(id);
  resumeVoice();
  let end = speak(id, mood ?? "ramble", babble ?? "");
  if (end > 0 && text) {
    for (const chunk of speechChunks(text).slice(1)) {
      end = speak(id, chunk.trimEnd().endsWith("?") ? "curious" : "ramble", chunk, { gap: SENTENCE_GAP_S }) || end;
    }
  }
  return end;
}

/** Say the last reply again, exactly as before (the same text always picks the same sounds). */
export function replayReply() {
  return last ? sayReply(last.mood, last.babble, last.text) : 0;
}
export const canReplay = () => last !== null;

/** How loud the main duck is being heard right now, 0..1 — what its beak follows. */
export function mouthLevel() { return voiceLevel(mainDuck); }

/** "Stop talking": the main duck stops mid-sentence. Nothing else — the legs keep going. */
export function stopTalking() { stopVoice(mainDuck); resumeVoice(); }

export function pauseTalking() { pauseVoice(); }
export function resumeTalking() { resumeVoice(); }

export function setMuted(muted: boolean) { setVoiceMuted(muted); }
export function setVolume(volume: number) { setVoiceVolume(volume); }

// ── what the page shows: speaking / paused / held back by the browser ─────────────────────────

export interface VoiceState { speaking: boolean; paused: boolean; blocked: boolean; canReplay: boolean }
let snapshot: VoiceState = { speaking: false, paused: false, blocked: false, canReplay: false };
function read(): VoiceState {
  const next = { speaking: isSpeaking(mainDuck), paused: voicePaused(), blocked: voiceBlocked(), canReplay: last !== null };
  if (next.speaking !== snapshot.speaking || next.paused !== snapshot.paused || next.blocked !== snapshot.blocked
      || next.canReplay !== snapshot.canReplay) snapshot = next;
  return snapshot;
}

export function useVoiceState(): VoiceState {
  return useSyncExternalStore(onVoiceChange, read);
}
