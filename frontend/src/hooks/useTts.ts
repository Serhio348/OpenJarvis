/**
 * Browser TTS (speechSynthesis) with a Jarvis-like British male voice.
 * Russian replies use a male ru-RU voice so Cyrillic stays intelligible.
 */

export type TtsVoiceMode = 'jarvis' | 'auto';

const JARVIS_NAME_RE =
  /george|daniel|ryan|arthur|uk english male|english \(united kingdom\).*male|microsoft george|google uk english male/i;

const MALE_HINT_RE = /male|david|mark|james|george|daniel|dmitry|pavel|димитрий|павел/i;
const FEMALE_HINT_RE = /female|zira|susan|irina|ekaterina|helen|hazel/i;

let voicesCache: SpeechSynthesisVoice[] = [];
let voicesReady: Promise<SpeechSynthesisVoice[]> | null = null;

function loadVoices(): Promise<SpeechSynthesisVoice[]> {
  if (typeof window === 'undefined' || !window.speechSynthesis) {
    return Promise.resolve([]);
  }
  if (voicesCache.length) return Promise.resolve(voicesCache);

  if (!voicesReady) {
    voicesReady = new Promise((resolve) => {
      const read = () => {
        const list = window.speechSynthesis.getVoices();
        if (list.length) {
          voicesCache = list;
          resolve(list);
          return true;
        }
        return false;
      };
      if (read()) return;
      const onVoices = () => {
        if (read()) {
          window.speechSynthesis.removeEventListener('voiceschanged', onVoices);
        }
      };
      window.speechSynthesis.addEventListener('voiceschanged', onVoices);
      // Chrome sometimes needs a tick + getVoices kick.
      window.setTimeout(() => {
        if (read()) {
          window.speechSynthesis.removeEventListener('voiceschanged', onVoices);
        } else {
          resolve([]);
        }
      }, 500);
    });
  }
  return voicesReady;
}

function isMostlyCyrillic(text: string): boolean {
  const letters = text.replace(/[^\p{L}]/gu, '');
  if (!letters) return false;
  const cyr = [...letters].filter((ch) => /[\u0400-\u04FF]/.test(ch)).length;
  return cyr / letters.length >= 0.35;
}

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

/** Pick Jarvis-like British male, or male Russian for Cyrillic text. */
export function pickJarvisVoice(
  voices: SpeechSynthesisVoice[],
  text: string,
): SpeechSynthesisVoice | null {
  if (!voices.length) return null;
  const cyr = isMostlyCyrillic(text);

  if (!cyr) {
    const jarvis = voices.find(
      (v) =>
        (v.lang.startsWith('en-GB') || v.lang.startsWith('en_GB')) &&
        JARVIS_NAME_RE.test(v.name),
    );
    if (jarvis) return jarvis;

    const enGbMale = voices.find(
      (v) =>
        (v.lang.startsWith('en-GB') || v.lang.startsWith('en_GB')) &&
        MALE_HINT_RE.test(v.name) &&
        !FEMALE_HINT_RE.test(v.name),
    );
    if (enGbMale) return enGbMale;

    const enGb = voices.find(
      (v) => v.lang.startsWith('en-GB') || v.lang.startsWith('en_GB'),
    );
    if (enGb) return enGb;

    const enMale = voices.find(
      (v) =>
        v.lang.startsWith('en') &&
        MALE_HINT_RE.test(v.name) &&
        !FEMALE_HINT_RE.test(v.name),
    );
    if (enMale) return enMale;
  }

  // Russian content: male ru voice (Jarvis persona, intelligible Cyrillic).
  const ruMale = voices.find(
    (v) =>
      (v.lang.startsWith('ru') || /russian/i.test(v.name)) &&
      MALE_HINT_RE.test(v.name) &&
      !FEMALE_HINT_RE.test(v.name),
  );
  if (ruMale) return ruMale;

  const ru = voices.find((v) => v.lang.startsWith('ru') || /russian/i.test(v.name));
  if (ru) return ru;

  // Last resort: any Jarvis English voice even for mixed text.
  return (
    voices.find((v) => JARVIS_NAME_RE.test(v.name)) ||
    voices.find((v) => v.lang.startsWith('en')) ||
    voices[0] ||
    null
  );
}

export function stopSpeaking(): void {
  if (typeof window === 'undefined' || !window.speechSynthesis) return;
  window.speechSynthesis.cancel();
}

export function isSpeaking(): boolean {
  if (typeof window === 'undefined' || !window.speechSynthesis) return false;
  return window.speechSynthesis.speaking;
}

export async function speakText(
  raw: string,
  opts?: { rate?: number },
): Promise<{ ok: boolean; voiceName?: string; error?: string }> {
  if (typeof window === 'undefined' || !window.speechSynthesis) {
    return { ok: false, error: 'speechSynthesis not available' };
  }
  const text = stripForSpeech(raw);
  if (!text) return { ok: false, error: 'empty text' };

  const voices = await loadVoices();
  const voice = pickJarvisVoice(voices, text);

  stopSpeaking();

  const utt = new SpeechSynthesisUtterance(text);
  if (voice) {
    utt.voice = voice;
    utt.lang = voice.lang;
  } else {
    utt.lang = isMostlyCyrillic(text) ? 'ru-RU' : 'en-GB';
  }
  // Slightly slower + lower pitch ≈ formal butler (Jarvis-ish).
  utt.rate = opts?.rate ?? 0.92;
  utt.pitch = 0.85;
  utt.volume = 1;

  return new Promise((resolve) => {
    utt.onend = () => resolve({ ok: true, voiceName: voice?.name });
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

/** Warm up voice list (call once on app mount / settings open). */
export function preloadTtsVoices(): void {
  void loadVoices();
}
