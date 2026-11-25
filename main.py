"""Application entrypoint for local execution."""

import uvicorn

from slbm_tracker.config import get_settings

if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(
        "slbm_tracker.api.app:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
        log_level=settings.log_level.lower(),
    )
