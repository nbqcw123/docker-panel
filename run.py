#!/usr/bin/env python3
"""Docker Management Panel - Entry point for direct Python execution."""
import os
import uvicorn
from main import app

if __name__ == "__main__":
    port = int(os.environ.get("DOCKER_PANEL_PORT", 50088))
    uvicorn.run(app, host="0.0.0.0", port=port, workers=1)
