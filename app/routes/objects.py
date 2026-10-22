from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response

from ..models import ObjectList, OperationResult, UploadedObject
from ..s3_client import S3Client, get_s3_client

router = APIRouter(tags=["objects"])

MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 МБ


@router.get(
    "/buckets/{bucket_name}/objects",
    response_model=ObjectList,
    summary="Список объектов в bucket",
)
def list_objects(
    bucket_name: str,
    prefix: str = Query("", description="Фильтр по префиксу ключа"),
    client: S3Client = Depends(get_s3_client),
) -> ObjectList:
    objects = client.list_objects(bucket_name, prefix=prefix)
    return ObjectList(bucket=bucket_name, count=len(objects), objects=objects)


@router.post(
    "/buckets/{bucket_name}/objects",
    response_model=UploadedObject,
    status_code=status.HTTP_201_CREATED,
    summary="Загрузить файл в bucket",
)
async def upload_object(
    bucket_name: str,
    file: UploadFile = File(..., description="Загружаемый файл"),
    key: str | None = Form(None, description="Ключ объекта (по умолчанию — имя файла)"),
    client: S3Client = Depends(get_s3_client),
) -> UploadedObject:
    if not file.filename:
        raise HTTPException(status_code=422, detail="У файла нет имени")

    data = await file.read()
    if len(data) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail="Файл больше 50 МБ")

    object_key = key or file.filename
    client.upload_object(bucket_name, object_key, data, content_type=file.content_type)
    return UploadedObject(
        bucket=bucket_name,
        key=object_key,
        size=len(data),
        content_type=file.content_type,
    )


@router.get(
    "/buckets/{bucket_name}/objects/{object_key:path}",
    summary="Просмотреть или скачать объект",
    responses={200: {"content": {"application/octet-stream": {}}}},
)
def get_object(
    bucket_name: str,
    object_key: str,
    download: bool = Query(False, description="True — отдать как вложение (скачивание)"),
    client: S3Client = Depends(get_s3_client),
) -> Response:
    if not object_key:
        raise HTTPException(status_code=422, detail="Пустой ключ объекта")

    obj = client.get_object(bucket_name, object_key)
    filename = object_key.rsplit("/", 1)[-1]
    disposition = "attachment" if download else "inline"

    return Response(
        content=obj["body"],
        media_type=obj["content_type"] or "application/octet-stream",
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(filename)}",
        },
    )


@router.delete(
    "/buckets/{bucket_name}/objects/{object_key:path}",
    response_model=OperationResult,
    summary="Удалить объект из bucket",
)
def delete_object(
    bucket_name: str,
    object_key: str,
    client: S3Client = Depends(get_s3_client),
) -> OperationResult:
    if not object_key:
        raise HTTPException(status_code=422, detail="Пустой ключ объекта")

    client.delete_object(bucket_name, object_key)
    return OperationResult(status="deleted", resource=f"{bucket_name}/{object_key}")