import { useState, useCallback, useRef, useEffect } from 'react';
import { transcribeAudio, fetchSpeechHealth } from '../lib/api';

export type SpeechState = 'idle' | 'recording' | 'transcribing';

/** RMS below this (0–1) counts as silence (MediaRecorder fallback only) */
const SILENCE_THRESHOLD = 0.015;
const SILENCE_MS = 1100;
const LEAD_IN_MS = 500;
const MAX_RECORD_MS = 45_000;
const NO_SPEECH_MS = 8_000;

interface SpeechRecognitionEventLike {
  results: SpeechRecognitionResultList;
}

interface SpeechRecognitionErrorLike {
  error: string;
}

interface SpeechRecognitionLike extends EventTarget {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start: () => void;
  stop: () => void;
  abort: () => void;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: SpeechRecognitionErrorLike) => void) | null;
  onend: (() => void) | null;
}

declare global {
  interface Window {
    SpeechRecognition?: new () => SpeechRecognitionLike;
    webkitSpeechRecognition?: new () => SpeechRecognitionLike;
  }
}

interface UseSpeechOptions {
  /** Called after utterance end / silence + transcription */
  onAutoTranscript?: (text: string) => void;
}

function getSpeechRecognitionCtor(): (new () => SpeechRecognitionLike) | null {
  if (typeof window === 'undefined') return null;
  return window.SpeechRecognition || window.webkitSpeechRecognition || null;
}

/**
 * Mic STT — prefer browser Web Speech API (ru-RU), same as QR consultant.
 * Falls back to MediaRecorder → local faster-whisper when Web Speech is missing.
 */
export function useSpeech(options: UseSpeechOptions = {}) {
  const { onAutoTranscript } = options;
  const onAutoTranscriptRef = useRef(onAutoTranscript);
  onAutoTranscriptRef.current = onAutoTranscript;

  const [state, setState] = useState<SpeechState>('idle');
  const [error, setError] = useState<string | null>(null);
  const [whisperAvailable, setWhisperAvailable] = useState(false);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const rafRef = useRef<number | null>(null);
  const timersRef = useRef<number[]>([]);
  const stoppingRef = useRef(false);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const webSpeechTextRef = useRef('');
  const webSpeechActiveRef = useRef(false);
  /** Avoid double-send when user clicks mic to stop (onend + stopRecording). */
  const suppressWebSpeechAutoRef = useRef(false);

  const SpeechCtor = getSpeechRecognitionCtor();
  const preferWebSpeech = !!SpeechCtor;
  const available = preferWebSpeech || whisperAvailable;

  const checkHealth = useCallback(async (): Promise<boolean> => {
    try {
      const health = await fetchSpeechHealth();
      setWhisperAvailable(health.available);
      return health.available;
    } catch {
      setWhisperAvailable(false);
      return false;
    }
  }, []);

  useEffect(() => {
    // Still probe whisper for fallback; Web Speech does not need it.
    let cancelled = false;
    const poll = async () => {
      if (cancelled) return;
      await checkHealth();
    };
    void poll();
    const onVisible = () => {
      if (document.visibilityState === 'visible') void checkHealth();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      cancelled = true;
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [checkHealth]);

  const cleanupAudioGraph = useCallback(() => {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    for (const id of timersRef.current) window.clearTimeout(id);
    timersRef.current = [];
    if (audioCtxRef.current) {
      void audioCtxRef.current.close().catch(() => undefined);
      audioCtxRef.current = null;
    }
  }, []);

  const finalizeRecording = useCallback(async (): Promise<string> => {
    const recorder = mediaRecorderRef.current;
    if (!recorder || recorder.state !== 'recording' || stoppingRef.current) {
      throw new Error('Not recording');
    }
    stoppingRef.current = true;
    cleanupAudioGraph();

    return new Promise((resolve, reject) => {
      recorder.onstop = async () => {
        setState('transcribing');
        streamRef.current?.getTracks().forEach((track) => track.stop());
        streamRef.current = null;
        mediaRecorderRef.current = null;

        const blob = new Blob(chunksRef.current, {
          type: recorder.mimeType || 'audio/webm',
        });
        chunksRef.current = [];
        stoppingRef.current = false;

        try {
          if (blob.size < 256) {
            setState('idle');
            resolve('');
            return;
          }
          const result = await transcribeAudio(blob);
          setState('idle');
          resolve((result.text || '').trim());
        } catch (err) {
          setState('idle');
          const msg = err instanceof Error ? err.message : 'Transcription failed';
          setError(msg);
          reject(err instanceof Error ? err : new Error(msg));
        }
      };

      try {
        recorder.requestData();
      } catch {
        // ignore
      }
      recorder.stop();
    });
  }, [cleanupAudioGraph]);

  const startVad = useCallback(
    (stream: MediaStream, onSilence: () => void) => {
      const AudioCtx =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext: typeof AudioContext })
          .webkitAudioContext;
      const ctx = new AudioCtx();
      audioCtxRef.current = ctx;
      const source = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 2048;
      source.connect(analyser);

      const data = new Uint8Array(analyser.fftSize);
      let speechHeard = false;
      let silenceStartedAt: number | null = null;
      const startedAt = performance.now();
      let fired = false;

      const fire = () => {
        if (fired) return;
        fired = true;
        onSilence();
      };

      const tick = () => {
        if (fired) return;
        analyser.getByteTimeDomainData(data);
        let sum = 0;
        for (let i = 0; i < data.length; i += 1) {
          const v = (data[i] - 128) / 128;
          sum += v * v;
        }
        const rms = Math.sqrt(sum / data.length);
        const now = performance.now();
        const elapsed = now - startedAt;

        if (rms >= SILENCE_THRESHOLD) {
          speechHeard = true;
          silenceStartedAt = null;
        } else if (speechHeard && elapsed > LEAD_IN_MS) {
          if (silenceStartedAt === null) silenceStartedAt = now;
          if (now - silenceStartedAt >= SILENCE_MS) {
            fire();
            return;
          }
        } else if (!speechHeard && elapsed > NO_SPEECH_MS) {
          fire();
          return;
        }

        rafRef.current = requestAnimationFrame(tick);
      };

      rafRef.current = requestAnimationFrame(tick);
      timersRef.current.push(window.setTimeout(fire, MAX_RECORD_MS));
    },
    [],
  );

  const startWebSpeech = useCallback((): void => {
    const Ctor = getSpeechRecognitionCtor();
    if (!Ctor) {
      setError('Web Speech API недоступен в этом браузере');
      return;
    }

    // Fresh instance each time (Chrome can get stuck after abort).
    try {
      recognitionRef.current?.abort();
    } catch {
      // ignore
    }

    const recognition = new Ctor();
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.lang = 'ru-RU';
    webSpeechTextRef.current = '';
    webSpeechActiveRef.current = true;

    recognition.onresult = (event: SpeechRecognitionEventLike) => {
      const result = event.results[0];
      if (result?.isFinal) {
        const text = result[0]?.transcript?.trim() || '';
        if (text) {
          webSpeechTextRef.current = webSpeechTextRef.current
            ? `${webSpeechTextRef.current} ${text}`
            : text;
        }
      }
    };

    recognition.onerror = (event: SpeechRecognitionErrorLike) => {
      const code = event.error;
      if (code === 'aborted' || code === 'no-speech') {
        // no-speech: user was silent — quiet idle
        if (code === 'no-speech') {
          setError('Речь не обнаружена. Попробуйте ещё раз.');
        }
      } else if (code === 'not-allowed') {
        setError('Доступ к микрофону запрещён');
      } else if (code === 'network') {
        setError('Нет сети для распознавания речи (нужен интернет)');
      } else if (code === 'language-not-supported') {
        setError('Русский язык не поддерживается движком речи браузера');
      } else {
        setError(`Ошибка распознавания: ${code}`);
      }
      webSpeechActiveRef.current = false;
      setState('idle');
    };

    recognition.onend = () => {
      const text = webSpeechTextRef.current.trim();
      webSpeechActiveRef.current = false;
      setState('idle');
      recognitionRef.current = null;
      if (text && !suppressWebSpeechAutoRef.current) {
        onAutoTranscriptRef.current?.(text);
      }
      suppressWebSpeechAutoRef.current = false;
    };

    recognitionRef.current = recognition;
    setError(null);
    try {
      recognition.start();
      setState('recording');
    } catch {
      setError('Не удалось запустить распознавание речи');
      webSpeechActiveRef.current = false;
      setState('idle');
    }
  }, []);

  const startMediaFallback = useCallback(async (): Promise<void> => {
    if (!navigator.mediaDevices?.getUserMedia) {
      setError('Microphone not supported in this browser');
      return;
    }

    const ok = whisperAvailable || (await checkHealth());
    if (!ok) {
      setError('Speech backend not available — wait a few seconds and try again');
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      streamRef.current = stream;

      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : MediaRecorder.isTypeSupported('audio/webm')
          ? 'audio/webm'
          : undefined;
      const recorder = mimeType
        ? new MediaRecorder(stream, { mimeType })
        : new MediaRecorder(stream);
      chunksRef.current = [];

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };

      recorder.start(250);
      mediaRecorderRef.current = recorder;
      setState('recording');

      startVad(stream, () => {
        if (stoppingRef.current) return;
        void finalizeRecording()
          .then((text) => {
            onAutoTranscriptRef.current?.(text);
          })
          .catch(() => {
            // error already stored in state
          });
      });
    } catch {
      setError('Microphone access denied — allow mic in the browser address bar');
      setState('idle');
    }
  }, [whisperAvailable, checkHealth, startVad, finalizeRecording]);

  const startRecording = useCallback(async (): Promise<void> => {
    setError(null);
    stoppingRef.current = false;
    if (preferWebSpeech) {
      startWebSpeech();
      return;
    }
    await startMediaFallback();
  }, [preferWebSpeech, startWebSpeech, startMediaFallback]);

  const stopRecording = useCallback(async (): Promise<string> => {
    if (preferWebSpeech && recognitionRef.current && webSpeechActiveRef.current) {
      return new Promise((resolve) => {
        const rec = recognitionRef.current;
        if (!rec) {
          resolve('');
          return;
        }
        suppressWebSpeechAutoRef.current = true;
        const finished = () => resolve(webSpeechTextRef.current.trim());
        const prevEnd = rec.onend;
        rec.onend = () => {
          try {
            prevEnd?.();
          } catch {
            // ignore
          }
          finished();
        };
        try {
          rec.stop();
        } catch {
          suppressWebSpeechAutoRef.current = false;
          finished();
        }
      });
    }
    return finalizeRecording();
  }, [preferWebSpeech, finalizeRecording]);

  useEffect(() => {
    return () => {
      cleanupAudioGraph();
      streamRef.current?.getTracks().forEach((t) => t.stop());
      try {
        recognitionRef.current?.abort();
      } catch {
        // ignore
      }
    };
  }, [cleanupAudioGraph]);

  return {
    state,
    error,
    available,
    startRecording,
    stopRecording,
    isRecording: state === 'recording',
    isTranscribing: state === 'transcribing',
  };
}
