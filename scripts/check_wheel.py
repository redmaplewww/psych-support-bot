"""Smoke-test installed-package startup without checkout files or provider keys."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from zipfile import ZipFile


def main() -> None:
    wheel = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="psy-installed-wheel-") as directory:
        root = Path(directory)
        with ZipFile(wheel) as archive:
            archive.extractall(root)
        env = dict(os.environ)
        env.update(
            {
                "DATABASE_URL": f"sqlite:///{root / 'smoke.db'}",
                "OPENAI_API_KEY": "",
                "DASHSCOPE_API_KEY": "",
                "LANGFUSE_PUBLIC_KEY": "",
                "LANGFUSE_SECRET_KEY": "",
                "VOICE_STT_API_KEY": "",
                "VOICE_TTS_API_KEY": "",
                "SPECULATIVE_REPLY_ENABLED": "false",
                "PROFILE_LLM_EXTRACTION_ENABLED": "false",
            }
        )
        code = """
import sys
sys.path.insert(0, sys.argv[1])
from fastapi.testclient import TestClient
from psych_support_bot.app import app
with TestClient(app) as client:
    assert client.get('/health').status_code == 200
    assert client.get('/').status_code == 200
print('installed wheel: migration startup, health and static page passed')
"""
        subprocess.run([sys.executable, "-I", "-c", code, str(root)], cwd=root, env=env, check=True, timeout=45)


if __name__ == "__main__":
    main()
