import { useState, useCallback, useRef, useEffect } from 'react';
import { transcribeAudio, fetchSpeechHealth } from '../lib/api';

export type SpeechState = 'idle' | 'recording' | 'transcribing';

/** RMS below this (0–1) counts as silence */
const SILENCE_THRESHOLD = 0.015;
/** Continuous silence after speech before auto-stop */
const SILENCE_MS = 1100;
/** Ignore leading silence / wait for first speech */
const LEAD_IN_MS = 500;
/** Hard cap so recording never hangs */
const MAX_RECORD_MS = 45_000;
/** Stop if user never speaks */
const NO_SPEECH_MS = 8_000;

interface UseSpeechOptions {
  /** Called after silence (or max duration) auto-stop + transcription */
  onAutoTranscript?: (text: string) => void;
}

export function useSpeech(options: UseSpeechOptions = {}) {
  const { onAutoTranscript } = options;
  const onAutoTranscriptRef = useRef(onAutoTranscript);
  onAutoTranscriptRef.current = onAutoTranscript;

  const [state, setState] = useState<SpeechState>('idle');
  const [error, setError] = useState<string | null>(null);
  const [available, setAvailable] = useState(false);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const rafRef = useRef<number | null>(null);
  const timersRef = useRef<number[]>([]);
  const stoppingRef = useRef(false);

  const checkHealth = useCallback(async (): Promise<boolean> => {
    try {
      const health = await fetchSpeechHealth();
      setAvailable(health.available);
      return health.available;
    } catch {
      setAvailable(false);
      return false;
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    let attempts = 0;

    const poll = async () => {
      if (cancelled) return;
      const ok = await checkHealth();
      attempts += 1;
      if (!ok && attempts < 12) {
        window.setTimeout(poll, 5000);
      }
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

  const startRecording = useCallback(async (): Promise<void> => {
    setError(null);
    stoppingRef.current = false;

    if (!navigator.mediaDevices?.getUserMedia) {
      setError('Microphone not supported in this browser');
      return;
    }

    const ok = available || (await checkHealth());
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
  }, [available, checkHealth, startVad, finalizeRecording]);

  const stopRecording = useCallback(async (): Promise<string> => {
    return finalizeRecording();
  }, [finalizeRecording]);

  useEffect(() => {
    return () => {
      cleanupAudioGraph();
      streamRef.current?.getTracks().forEach((t) => t.stop());
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
