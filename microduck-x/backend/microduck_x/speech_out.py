"""Local text-to-speech. espeak-ng if installed; otherwise the dashboard uses the browser's own
offline voices (Windows ships several). Nothing is sent anywhere."""
from __future__ import annotations

import shutil
import subprocess


def available() -> bool:
    return shutil.which("espeak-ng") is not None


def synthesize(text: str, voice: str = "en-us", speed: int = 165) -> bytes:
    if not available():
        raise RuntimeError("espeak-ng is not installed (sudo apt install espeak-ng)")
    text = text.strip()[:600]
    proc = subprocess.run(["espeak-ng", "-v", voice, "-s", str(speed), "--stdout", text], capture_output=True, timeout=20)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode(errors="replace")[:200])
    return proc.stdout
