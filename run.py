"""Entry point: `python run.py` boots the app on http://127.0.0.1:8765."""
import os
import uvicorn

if __name__ == "__main__":
    host = os.environ.get("MLC_HOST", "127.0.0.1")
    port = int(os.environ.get("MLC_PORT", "8870"))
    reload = os.environ.get("MLC_RELOAD", "0") == "1"
    uvicorn.run("app.main:app", host=host, port=port, reload=reload, log_level="info")
