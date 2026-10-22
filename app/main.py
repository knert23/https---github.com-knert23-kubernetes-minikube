from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from .routes import buckets, objects, vision_ocr
from .s3_client import S3Error

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(
    title="S3 Lab — Yandex Cloud Storage",
    description=(
        "Веб-приложение для работы с bucket'ами и объектами "
        "по протоколу S3 (Yandex Cloud)"
    ),
    version="1.0.0",
)


@app.exception_handler(S3Error)
async def s3_error_handler(request: Request, exc: S3Error) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health", tags=["service"], summary="Проверка работоспособности")
def health() -> dict:
    return {"status": "ok"}


app.include_router(buckets.router)
app.include_router(objects.router)
app.include_router(vision_ocr.router)