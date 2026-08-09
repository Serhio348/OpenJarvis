/**
 * Chat TTS: Edge neural ru-RU-DmitryNeural via /v1/speech/synthesize.
 * Browser autoplay blocks play() after await — unlockAudio() must run on
 * a user gesture (send / mic / speaker click) before synthesis.
 */

import { synthesizeSpeech } from '../lib/api';

const DEFAULT_VOICE = 'ru-RU-DmitryNeural';
/** Tiny silent WAV — used once to unlock autoplay. */
const SILENT_WAV =
  'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=';

let currentAudio: HTMLAudioElement | null = null;
let currentObjectUrl: string | null = null;
let audioUnlocked = false;

function stripForSpeech(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`[^`]+`/g, ' ')
    .replace(/!\[[^\]]*]\([^)]*\)/g, ' ')
    .replace(/\[([^\]]+)]\([^)]*\)/g, '$1')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/^\s*[-*•]\s+/gm, '')
    .replace(/\*{1,2}([^*]+)\*{1,2}/g, '$1')
    .replace(/_{1,2}([^_]+)_{1,2}/g, '$1')
    .replace(/\s+/g, ' ')
    .trim();
}

function stopBrowserSpeech(): void {
  if (typeof window !== 'undefined' && window.speechSynthesis) {
    window.speechSynthesis.cancel();
  }
}

function stopAudioElement(): void {
  if (currentAudio) {
    try {
      currentAudio.onended = null;
      currentAudio.onerror = null;
      currentAudio.pause();
      currentAudio.removeAttribute('src');
      currentAudio.load();
    } catch {
      // ignore
    }
    currentAudio = null;
  }
  if (currentObjectUrl) {
    URL.revokeObjectURL(currentObjectUrl);
    currentObjectUrl = null;
  }
}

export function stopSpeaking(): void {
  stopBrowserSpeech();
  stopAudioElement();
}

export function isSpeaking(): boolean {
  if (currentAudio && !currentAudio.paused && !currentAudio.ended) return true;
  if (typeof window !== 'undefined' && window.speechSynthesis?.speaking) return true;
  return false;
}

/**
 * Call synchronously from a click/keydown handler so Chrome allows later play().
 */
export function unlockAudio(): void {
  if (typeof window === 'undefined' || audioUnlocked) return;
  try {
    const a = new Audio(SILENT_WAV);
    a.volume = 0.01;
    const p = a.play();
    if (p && typeof p.then === 'function') {
      void p
        .then(() => {
          audioUnlocked = true;
          a.pause();
        })
        .catch(() => {
          // Still mark unlocked attempt; next user gesture may succeed.
        });
    }
    audioUnlocked = true;
  } catch {
    // ignore
  }
}

async function speakViaEdge(
  text: string,
  speed: number,
): Promise<{ ok: boolean; voiceName?: string; error?: string }> {
  const blob = await synthesizeSpeech(text, {
    voice: DEFAULT_VOICE,
    speed,
  });
  if (!blob || blob.size < 100) {
    return { ok: false, error: 'empty audio from server' };
  }

  // Keep a single element; set src after fetch, then play.
  stopAudioElement();
  const url = URL.createObjectURL(blob);
  currentObjectUrl = url;
  const audio = new Audio();
  currentAudio = audio;
  audio.preload = 'auto';
  audio.src = url;

  try {
    await audio.play();
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    stopAudioElement();
    return { ok: false, error: msg || 'audio play blocked', voiceName: DEFAULT_VOICE };
  }

  return new Promise((resolve) => {
    audio.onended = () => {
      stopAudioElement();
      resolve({ ok: true, voiceName: DEFAULT_VOICE });
    };
    audio.onerror = () => {
      stopAudioElement();
      resolve({ ok: false, error: 'audio playback failed', voiceName: DEFAULT_VOICE });
    };
  });
}

async function speakViaBrowser(
  text: string,
  rate: number,
): Promise<{ ok: boolean; voiceName?: string; error?: string }> {
  if (typeof window === 'undefined' || !window.speechSynthesis) {
    return { ok: false, error: 'speechSynthesis not available' };
  }
  stopBrowserSpeech();
  const utt = new SpeechSynthesisUtterance(text);
  utt.lang = 'ru-RU';
  utt.rate = rate;
  utt.pitch = 0.9;
  const voices = window.speechSynthesis.getVoices();
  const voice =
    voices.find((v) => /dmitry|дмитрий/i.test(v.name)) ||
    voices.find((v) => v.lang.startsWith('ru') && /male|pavel/i.test(v.name)) ||
    voices.find((v) => v.lang.startsWith('ru'));
  if (voice) {
    utt.voice = voice;
    utt.lang = voice.lang;
  }
  return new Promise((resolve) => {
    utt.onend = () => resolve({ ok: true, voiceName: voice?.name || 'ru-RU' });
    utt.onerror = (ev) => {
      if (ev.error === 'interrupted' || ev.error === 'canceled') {
        resolve({ ok: true, voiceName: voice?.name });
        return;
      }
      resolve({ ok: false, error: ev.error, voiceName: voice?.name });
    };
    window.speechSynthesis.speak(utt);
  });
}

export async function speakText(
  raw: string,
  opts?: { rate?: number },
): Promise<{ ok: boolean; voiceName?: string; error?: string }> {
  const text = stripForSpeech(raw);
  if (!text) return { ok: false, error: 'empty text' };

  // Don't call stopSpeaking() before unlock — cancel is fine though.
  stopSpeaking();

  const speed = opts?.rate ?? 1.0;
  try {
    const edge = await speakViaEdge(text, speed);
    if (edge.ok) return edge;
    // If play was blocked, browser TTS usually also blocked after await.
    if (edge.error && /not allowed|interact|gesture|play/i.test(edge.error)) {
      return edge;
    }
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    try {
      const browser = await speakViaBrowser(
        text,
        Math.min(1.1, Math.max(0.7, speed * 0.95)),
      );
      if (browser.ok) return browser;
      return { ok: false, error: msg || browser.error };
    } catch {
      return { ok: false, error: msg };
    }
  }
  return speakViaBrowser(text, Math.min(1.1, Math.max(0.7, speed * 0.95)));
}

export function preloadTtsVoices(): void {
  if (typeof window === 'undefined' || !window.speechSynthesis) return;
  window.speechSynthesis.getVoices();
  window.speechSynthesis.addEventListener('voiceschanged', () => {
    window.speechSynthesis.getVoices();
  });
}
