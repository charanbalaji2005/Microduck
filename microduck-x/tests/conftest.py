import asyncio
import os
import shutil
from pathlib import Path

import pytest

from microduck_x.app import Service
from microduck_x.config import Settings

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def settings(tmp_path):
    os.environ.pop("MDX_MANAGE_SIM", None)
    s = Settings(state_dir=tmp_path / "state", mock=True)
    s.experiments_dir = tmp_path / "experiments"
    s.experiments_dir.mkdir()
    s.stt_engine = "none"
    s.groq_api_key = ""  # a developer's .env must not send test chat to Groq (test_brain.py fakes it)
    s.__post_init__()
    return s


@pytest.fixture
async def svc(settings):
    """A Service wired to the MOCK robotd and MOCK simulator (not the Microduck)."""
    service = Service(settings)
    await service.start()
    for _ in range(100):
        if service.robot.connected and service.xbody.connected and service.hub.sim and service.hub.robot:
            break
        await asyncio.sleep(0.05)
    await service.executor.load_model_index()
    await service.xbody.model()
    yield service
    await service.stop()


async def wait_terminal(ex, timeout=20):
    from microduck_x.executor import TERMINAL
    for _ in range(int(timeout / 0.05)):
        if ex.status in TERMINAL:
            return ex
        await asyncio.sleep(0.05)
    raise AssertionError(f"{ex.action} still {ex.status}")
