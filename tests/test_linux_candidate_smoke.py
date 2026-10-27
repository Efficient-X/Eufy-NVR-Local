"""Real Linux process-group checks, never simulated as a Windows pass."""
import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Real Linux process groups required")
@pytest.mark.asyncio
async def test_actual_linux_worker_and_health_lifecycle():
    spec = importlib.util.spec_from_file_location("linux_candidate_smoke",
        ROOT / "tools/linux_candidate_smoke.py")
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)
    await smoke.run(ROOT / "bridge")
