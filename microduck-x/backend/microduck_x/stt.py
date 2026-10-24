"""Offline speech recognition. Audio never leaves this machine.

Engines, in order of preference:

* **faster-whisper** (CTranslate2 Whisper) — accurate, CPU int8 or CUDA float16. Its model is
  *not* downloaded implicitly: `local_files_only=True` until `download_whisper()` is called
  explicitly (the dashboard's Settings page, or `x-setup.sh --whisper`), which states the size
  first. `base.en` is ~145 MB, `tiny.en` ~75 MB, `small.en` ~485 MB.
* **pocketsphinx** — CMU Sphinx, ships its US-English model inside the wheel, so it works with
  no download at all. Much less accurate on free speech; constrained to a command grammar here
  (`grammar.py`), which is where it is usable.

The browser records the microphone (push-to-talk or voice-activity triggered) and posts the clip;
`ffmpeg` decodes whatever container the browser chose into 16 kHz mono.
"""
from __future__ import annotations

import math
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

WHISPER_SIZES_MB = {"tiny.en": 75, "base.en": 145, "small.en": 485, "medium.en": 1530, "tiny": 75, "base": 145,
                    "small": 485}
# Biases decoding toward the command vocabulary without restricting it.
WHISPER_PROMPT = ("Microduck, walk forward, walk backward, turn left, turn right, ninety degrees, stop, "
                  "emergency stop, sit down, stand up, pause, resume, reset the simulation, laboratory, garden, "
                  "arena, apartment, camera follow mode, joint angles, walking test, waypoint, goal two.")


@dataclass
class Transcript:
    text: str
    confidence: float | None  # None = the engine gives no calibrated number
    engine: str
    seconds_audio: float
    seconds_compute: float
    no_speech_prob: float | None = None
    language: str | None = None

    def as_dict(self) -> dict:
        return self.__dict__.copy()


class SttUnavailable(RuntimeError):
    pass


def decode_audio(data: bytes) -> np.ndarray:
    if shutil.which("ffmpeg") is None:
        raise SttUnavailable("ffmpeg is not installed (sudo apt install ffmpeg)")
    proc = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-f", "f32le", "-ac", "1",
         "-ar", "16000", "pipe:1"], input=data, capture_output=True, timeout=30)
    if proc.returncode != 0:
        raise ValueError(f"could not decode the audio: {proc.stderr.decode(errors='replace')[:200]}")
    return np.frombuffer(proc.stdout, dtype=np.float32)


class WhisperEngine:
    name = "faster-whisper"

    def __init__(self, model: str, models_dir: Path, device: str = "auto"):
        self.model_name = model
        self.root = models_dir / "whisper"
        self.device = device
        self._model = None
        self._lock = threading.Lock()

    @staticmethod
    def importable() -> bool:
        try:
            import faster_whisper  # noqa: F401
            return True
        except Exception:  # noqa: BLE001
            return False

    def cached(self) -> bool:
        return any(self.root.glob(f"models--Systran--faster-whisper-{self.model_name}/snapshots/*/model.bin"))

    def _device(self) -> tuple[str, str]:
        if self.device in ("cpu", "cuda"):
            dev = self.device
        else:
            try:
                import ctranslate2

                dev = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
            except Exception:  # noqa: BLE001
                dev = "cpu"
        return dev, ("float16" if dev == "cuda" else "int8")

    def load(self, allow_download: bool = False):
        with self._lock:
            if self._model is None:
                from faster_whisper import WhisperModel

                dev, ctype = self._device()
                self._model = WhisperModel(self.model_name, device=dev, compute_type=ctype,
                                           download_root=str(self.root), local_files_only=not allow_download)
                self.loaded_on = f"{dev}/{ctype}"
        return self._model

    def transcribe(self, audio: np.ndarray) -> Transcript:
        if not self.cached():
            raise SttUnavailable(f"whisper model '{self.model_name}' is not downloaded "
                                 f"(~{WHISPER_SIZES_MB.get(self.model_name, '?')} MB); download it from Settings")
        model = self.load()
        t0 = time.perf_counter()
        segments, info = model.transcribe(audio, language="en" if self.model_name.endswith(".en") else None,
                                          beam_size=1, vad_filter=True, initial_prompt=WHISPER_PROMPT,
                                          condition_on_previous_text=False)
        segments = list(segments)
        text = " ".join(s.text.strip() for s in segments).strip()
        if segments:
            weights = [max(1, len(s.text)) for s in segments]
            conf = sum(math.exp(s.avg_logprob) * w for s, w in zip(segments, weights)) / sum(weights)
            nsp = max(s.no_speech_prob for s in segments)
        else:
            conf, nsp = 0.0, 1.0
        return Transcript(text=text, confidence=round(conf, 3), engine=f"{self.name}:{self.model_name}",
                          seconds_audio=round(len(audio) / 16000, 2), seconds_compute=round(time.perf_counter() - t0, 2),
                          no_speech_prob=round(nsp, 3), language=info.language)


class SphinxEngine:
    name = "pocketsphinx"

    def __init__(self, grammar_path: Path | None = None):
        self.grammar_path = grammar_path
        self._lock = threading.Lock()
        self._decoder = None

    @staticmethod
    def importable() -> bool:
        try:
            import pocketsphinx  # noqa: F401
            return True
        except Exception:  # noqa: BLE001
            return False

    def _get(self):
        if self._decoder is None:
            from pocketsphinx import Config, Decoder

            config = Config(loglevel="FATAL")
            if self.grammar_path and self.grammar_path.exists():
                config["lm"] = None  # one search at a time: the grammar replaces the language model
                config["jsgf"] = str(self.grammar_path)
            self._decoder = Decoder(config)
        return self._decoder

    def transcribe(self, audio: np.ndarray) -> Transcript:
        t0 = time.perf_counter()
        # Sphinx's endpointing needs silence on both sides; a push-to-talk clip often has none.
        pad = np.zeros(8000, dtype=np.float32)
        pcm = (np.clip(np.concatenate([pad, audio, pad]), -1, 1) * 32767).astype(np.int16).tobytes()
        with self._lock:
            d = self._get()
            d.start_utt()
            d.process_raw(pcm, full_utt=True)
            d.end_utt()
            hyp = d.hyp()
        text = hyp.hypstr if hyp else ""
        return Transcript(text=text, confidence=None, engine=self.name + (":grammar" if self.grammar_path else ""),
                          seconds_audio=round(len(audio) / 16000, 2), seconds_compute=round(time.perf_counter() - t0, 2))


class Speech:
    """Picks the engine per settings and reports, honestly, which one is in use and why."""

    def __init__(self, preference: str, whisper_model: str, models_dir: Path, device: str, grammar_path: Path | None):
        self.preference = preference
        self.whisper = WhisperEngine(whisper_model, models_dir, device) if WhisperEngine.importable() else None
        self.sphinx = SphinxEngine(grammar_path) if SphinxEngine.importable() else None
        self.download_state: dict = {"state": "idle"}

    def engine(self):
        pref = self.preference
        if pref == "none":
            return None
        if pref in ("auto", "faster-whisper") and self.whisper and self.whisper.cached():
            return self.whisper
        if pref == "faster-whisper":
            return None
        if pref in ("auto", "pocketsphinx") and self.sphinx:
            return self.sphinx
        return None

    def status(self) -> dict:
        e = self.engine()
        why = None
        if e is None:
            if self.preference == "none":
                why = "speech recognition disabled (MDX_STT=none)"
            elif self.preference == "faster-whisper" and self.whisper and not self.whisper.cached():
                why = f"whisper model '{self.whisper.model_name}' not downloaded"
            else:
                why = "no speech engine installed (pip install faster-whisper or pocketsphinx)"
        return dict(
            engine=e.name if e else None, reason=why, preference=self.preference, ffmpeg=bool(shutil.which("ffmpeg")),
            whisper=dict(installed=self.whisper is not None, model=self.whisper.model_name if self.whisper else None,
                         cached=bool(self.whisper and self.whisper.cached()),
                         size_mb=WHISPER_SIZES_MB.get(self.whisper.model_name) if self.whisper else None,
                         download=self.download_state),
            pocketsphinx=dict(installed=self.sphinx is not None,
                              grammar=bool(self.sphinx and self.sphinx.grammar_path)),
        )

    def transcribe(self, data: bytes) -> Transcript:
        e = self.engine()
        if e is None:
            raise SttUnavailable(self.status()["reason"])
        audio = decode_audio(data)
        if len(audio) < 1600:
            raise ValueError("the clip is shorter than 0.1 s")
        return e.transcribe(audio)

    def download_whisper(self) -> None:
        """Explicit, user-initiated download through huggingface_hub into the models directory."""
        if not self.whisper:
            raise SttUnavailable("faster-whisper is not installed")
        self.download_state = {"state": "downloading", "started": time.time(),
                               "size_mb": WHISPER_SIZES_MB.get(self.whisper.model_name)}
        try:
            self.whisper.load(allow_download=True)
            self.download_state = {"state": "done", "finished": time.time()}
        except Exception as error:  # noqa: BLE001
            self.download_state = {"state": "failed", "error": str(error)}
            raise
