"""ASGI entrypoint for hosts that import an `app` object (Vercel, `uvicorn app:app`).

Serves the web process only: API + built interface. The job runner
(`nyra worker`, Chromium + CLIP) has to run somewhere long-lived; see
docs/DEPLOYMENT.md.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

from nyra.cloud.api import create_app, settings_from_env  # noqa: E402

app = create_app(settings_from_env())
