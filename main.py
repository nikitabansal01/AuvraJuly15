"""AUVRA v2 API entry point.

The legacy ASGI app is deliberately not a deployment entry point.  It remains
in the repository only as migration evidence while Render serves ``app.v2``.
"""

import uvicorn
from app.v2.runtime.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "app.v2.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.ENVIRONMENT == "development",
        log_level=settings.LOG_LEVEL.lower(),
        access_log=True,
    )
