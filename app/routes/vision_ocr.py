from __future__ import annotations

import base64
import io
import json
import logging
from pathlib import PurePosixPath
from typing import Any

import requests
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from PIL import Image, ImageDraw, UnidentifiedImageError
from pydantic import ValidationError

from ..config import settings
from ..models import VisionOCRAsyncOperation, VisionOCRObjectRequest
from ..s3_client import S3Client, get_s3_client

router = APIRouter(tags=["vision-ocr"])
logger = logging.getLogger(__name__)

LANGUAGE_CODES = ["ru"]
BOX_COLORS = {
    "block": (0, 102, 255),
    "header": (156, 39, 176),
    "line": (255, 153, 0),
    "word": (230, 0, 0),
}
ANNOTATION_WRAPPER_KEYS = ("result", "response", "data", "recognition", "results")


def _request_ocr(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    try:
        response = requests.request(
            method,
            f"{settings.vision_ocr_endpoint_url.rstrip('/')}{path}",
            headers={
                "Authorization": f"Api-Key {settings.vision_api_key}",
            },
            timeout=30,
            **kwargs,
        )
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Yandex Vision OCR request failed: {exc}",
        ) from exc

    if not response.ok:
        message = f"Yandex Vision OCR request failed: HTTP {response.status_code}"
        try:
            body = response.json()
        except ValueError:
            body = None

        if isinstance(body, dict):
            error = body.get("error", body)
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                message = f"Yandex Vision OCR: {error['message']}"

        status_code = (
            response.status_code
            if 400 <= response.status_code < 500
            else 502
        )
        raise HTTPException(status_code=status_code, detail=message)

    try:
        body = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail="Yandex Vision OCR returned invalid JSON",
        ) from exc

    if not isinstance(body, dict):
        raise HTTPException(
            status_code=502,
            detail="Yandex Vision OCR returned an unexpected response format",
        )
    return body


def _find_text_annotation(payload: Any, depth: int = 0) -> dict[str, Any] | None:
    if depth > 6:
        return None
    if isinstance(payload, list):
        for item in payload:
            annotation = _find_text_annotation(item, depth + 1)
            if annotation is not None:
                return annotation
        return None
    if not isinstance(payload, dict):
        return None

    annotation = payload.get("textAnnotation") or payload.get("text_annotation")
    if isinstance(annotation, dict):
        return annotation

    for key in ANNOTATION_WRAPPER_KEYS:
        annotation = _find_text_annotation(payload.get(key), depth + 1)
        if annotation is not None:
            return annotation
    return None


def _draw_ocr_boxes(
    image_data: bytes,
    recognition: dict[str, Any],
) -> tuple[bytes, str]:
    annotation = _find_text_annotation(recognition)
    if annotation is None:
        logger.error(
            "Yandex Vision OCR response has no textAnnotation; top-level keys: %s",
            list(recognition),
        )
        raise HTTPException(
            status_code=502,
            detail=(
                "Yandex Vision OCR response does not contain textAnnotation "
                f"(top-level keys: {', '.join(recognition) or 'none'})"
            ),
        )

    try:
        with Image.open(io.BytesIO(image_data)) as source:
            image_format = source.format
            if image_format not in {"PNG", "JPEG"}:
                raise HTTPException(
                    status_code=415,
                    detail="Рамки OCR можно сохранять только для изображений PNG и JPEG",
                )
            image = source.convert("RGB")
    except UnidentifiedImageError as exc:
        raise HTTPException(
            status_code=415,
            detail="Исходный объект не является поддерживаемым изображением PNG или JPEG",
        ) from exc

    try:
        annotation_width = float(annotation.get("width") or image.width)
        annotation_height = float(annotation.get("height") or image.height)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail="Yandex Vision OCR returned invalid annotation dimensions",
        ) from exc
    if annotation_width <= 0 or annotation_height <= 0:
        raise HTTPException(
            status_code=502,
            detail="Yandex Vision OCR returned invalid annotation dimensions",
        )

    scale_x = image.width / annotation_width
    scale_y = image.height / annotation_height
    min_dimension = min(image.width, image.height)
    line_width = max(1, round(min_dimension / 900))
    level_gap = max(2, round(min_dimension / 300))
    draw = ImageDraw.Draw(image)

    def draw_box(
        item: dict[str, Any],
        color: tuple[int, int, int],
        padding: int,
    ) -> None:
        bounding_box = item.get("boundingBox") or item.get("bounding_box")
        vertices = bounding_box.get("vertices") if isinstance(bounding_box, dict) else None
        if not isinstance(vertices, list) or len(vertices) < 3:
            return

        points = []
        for vertex in vertices:
            if not isinstance(vertex, dict):
                raise HTTPException(
                    status_code=502,
                    detail="Yandex Vision OCR returned invalid bounding-box vertices",
                )
            try:
                points.append(
                    (
                        round(float(vertex["x"]) * scale_x),
                        round(float(vertex["y"]) * scale_y),
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=502,
                    detail="Yandex Vision OCR returned invalid bounding-box coordinates",
                ) from exc
        if padding:
            center_x = sum(x for x, _ in points) / len(points)
            center_y = sum(y for _, y in points) / len(points)
            points = [
                (
                    x + (padding if x >= center_x else -padding),
                    y + (padding if y >= center_y else -padding),
                )
                for x, y in points
            ]
        draw.polygon(points, outline=color, width=line_width)

    blocks = annotation.get("blocks")
    if isinstance(blocks, list):
        header_blocks = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            layout_type = block.get("layoutType") or block.get("layout_type")
            if layout_type == "LAYOUT_TYPE_HEADER":
                header_blocks.append(block)
            else:
                draw_box(block, BOX_COLORS["block"], level_gap * 2)
            lines = block.get("lines")
            if not isinstance(lines, list):
                continue
            for line in lines:
                if not isinstance(line, dict):
                    continue
                draw_box(line, BOX_COLORS["line"], level_gap)
                words = line.get("words")
                if isinstance(words, list):
                    for word in words:
                        if isinstance(word, dict):
                            draw_box(word, BOX_COLORS["word"], 0)
        for block in header_blocks:
            draw_box(block, BOX_COLORS["header"], level_gap * 2)

    output = io.BytesIO()
    output_format = "PNG" if image_format == "PNG" else "JPEG"
    save_options = {"quality": 95} if output_format == "JPEG" else {}
    image.save(output, format=output_format, **save_options)
    content_type = "image/png" if output_format == "PNG" else "image/jpeg"
    return output.getvalue(), content_type


def _recognized_object_key(object_key: str) -> str:
    parent, separator, filename = object_key.rpartition("/")
    path = PurePosixPath(filename)
    recognized_name = f"{path.stem}_recognized{path.suffix}"
    return f"{parent}{separator}{recognized_name}"


@router.post(
    "/buckets/{bucket_name}/recognitions",
    response_model=VisionOCRAsyncOperation,
    summary="Запустить распознавание изображения из bucket",
)
def recognize_object(
    bucket_name: str,
    request: VisionOCRObjectRequest,
    client: S3Client = Depends(get_s3_client),
) -> VisionOCRAsyncOperation:
    obj = client.get_object(bucket_name, request.object_key)
    payload: dict[str, Any] = {
        "content": base64.b64encode(obj["body"]).decode("ascii"),
    }
    if obj["content_type"] != "application/octet-stream":
        payload["mimeType"] = obj["content_type"]
    payload["languageCodes"] = request.language_codes or LANGUAGE_CODES
    if request.model:
        payload["model"] = request.model

    body = _request_ocr(
        "POST",
        "/ocr/v1/recognizeTextAsync",
        json=payload,
    )
    try:
        return VisionOCRAsyncOperation.model_validate(body)
    except (ValidationError, TypeError) as exc:
        raise HTTPException(
            status_code=502,
            detail="Yandex Vision OCR returned an invalid operation response",
        ) from exc


@router.get(
    "/recognitions/{operation_id}",
    response_model=dict[str, Any],
    summary="Получить состояние или результат распознавания",
)
def get_recognition(
    operation_id: str = Path(..., min_length=1, max_length=50),
    bucket_name: str = Query(..., alias="bucketName", min_length=3, max_length=63),
    object_key: str = Query(..., alias="objectKey", min_length=1),
    client: S3Client = Depends(get_s3_client),
) -> dict[str, Any]:
    body = _request_ocr(
        "GET",
        "/ocr/v1/getRecognition",
        params={"operationId": operation_id},
    )
    logger.info(
        "Yandex Vision OCR result for operation %s:\n%s",
        operation_id,
        json.dumps(body, ensure_ascii=False, indent=2),
    )
    original = client.get_object(bucket_name, object_key)
    annotated_image, content_type = _draw_ocr_boxes(original["body"], body)
    recognized_key = _recognized_object_key(object_key)
    client.upload_object(
        bucket_name,
        recognized_key,
        annotated_image,
        content_type=content_type,
    )
    body["recognizedImageKey"] = recognized_key
    return body
