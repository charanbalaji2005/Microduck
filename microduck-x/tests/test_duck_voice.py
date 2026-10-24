"""The hangar speaks with the robot's own voice: frontend/src/voice/duckVoice.ts must render what
`sounds render` renders, sample for sample. Skipped unless the `sounds` binary is built
(`cargo build -p sounds`) and the dashboard's node_modules are installed."""
import json
import shutil
import subprocess
import wave
from pathlib import Path

import pytest

X = Path(__file__).resolve().parents[1]
REPO = X.parent
SOUNDS = REPO / "target" / "debug" / "sounds"
FRONTEND = X / "frontend"
ESBUILD = FRONTEND / "node_modules" / ".bin" / "esbuild"

def _runnable() -> bool:
    if not (SOUNDS.exists() and ESBUILD.exists() and shutil.which("node")):
        return False
    # node_modules installed from the other side of WSL carries the other OS's esbuild binary
    return subprocess.run([str(ESBUILD), "--version"], capture_output=True).returncode == 0


pytestmark = pytest.mark.skipif(not _runnable(), reason="needs target/debug/sounds, node and a working frontend esbuild")

CASES = [(seed, tag, v) for seed in (100, 0xC71F7C0B) for tag in ("alarm", "greet", "inquire", "peck", "chirp", "coo", "wheee")
         for v in (0, 3)]


def test_voice_port_matches_the_robot(tmp_path):
    bundle = tmp_path / "duckVoice.cjs"
    subprocess.run([str(ESBUILD), str(FRONTEND / "src/voice/duckVoice.ts"), "--bundle", "--format=cjs",
                    "--platform=node", f"--outfile={bundle}", "--log-level=error"], check=True)
    script = tmp_path / "render.cjs"
    script.write_text(
        "const V = require(process.argv[2]); const out = {};\n"
        "for (const [seed, tag, v] of JSON.parse(process.argv[3])) out[`${seed}/${tag}/${v}`] ="
        " Array.from(V.render(tag, V.personality(seed), v), (x) => Math.max(-32768, Math.min(32767, Math.trunc(x * 32767))));\n"
        "out.seed = V.seedFromId('duck-a'); console.log(JSON.stringify(out));\n")
    ours = json.loads(subprocess.run(["node", str(script), str(bundle), json.dumps(CASES)], check=True,
                                     capture_output=True, text=True).stdout)
    assert ours["seed"] == 0xC71F7C0B, "sha256('duck-a')[:4], the sim duck's voice seed"
    for seed, tag, v in CASES:
        wav = tmp_path / f"{seed}_{tag}_{v}.wav"
        subprocess.run([str(SOUNDS), "render", tag, str(wav), "--seed", str(seed), "--variant", str(v)],
                       check=True, capture_output=True)
        with wave.open(str(wav)) as w:
            ref = memoryview(w.readframes(w.getnframes())).cast("h").tolist()
        got = ours[f"{seed}/{tag}/{v}"]
        assert len(got) == len(ref), (seed, tag, v)
        assert max(abs(a - b) for a, b in zip(got, ref)) <= 2, (seed, tag, v)
