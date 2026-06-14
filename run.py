#!/usr/bin/env python3
"""Docker Management Panel - Entry point for direct Python execution."""
import uvicorn
from main import app

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=50087, workers=1)
