"""`uvicorn api.main:app --reload --port 8000`"""
from __future__ import annotations

import logging

from api.app import create_app
from api.config import ROOT

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# serves web/dist at / once it's built (scripts/serve.sh, the Docker image); dev uses Vite
app = create_app(web_dist=ROOT / "web" / "dist")
