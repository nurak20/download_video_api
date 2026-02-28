from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.routes.downloads import router as downloads_router
from app.api.v1.routes.instagram import router as instagram_router
from app.api.v1.routes.tiktok import router as tiktok_router
from app.core.logging import configure_logging


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(downloads_router)
    app.include_router(instagram_router)
    app.include_router(tiktok_router)
    return app


app = create_app()
