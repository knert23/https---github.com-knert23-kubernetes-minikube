from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..models import BucketCreate, BucketList, BucketStats, OperationResult
from ..s3_client import S3Client, get_s3_client

router = APIRouter(tags=["buckets"])


@router.get("/buckets", response_model=BucketList, summary="Список всех bucket'ов")
def list_buckets(client: S3Client = Depends(get_s3_client)) -> BucketList:
    buckets = client.list_buckets()
    return BucketList(count=len(buckets), buckets=buckets)


@router.post("/buckets", response_model=OperationResult, status_code=status.HTTP_201_CREATED, summary="Создать bucket")
def create_bucket(payload: BucketCreate, client: S3Client = Depends(get_s3_client)) -> OperationResult:
    client.create_bucket(payload.name)
    return OperationResult(status="created", resource=payload.name)


@router.get("/buckets/{bucket_name}", response_model=BucketStats, summary="Информация о bucket (существует ли, сколько объектов)")
def bucket_stats(bucket_name: str, client: S3Client = Depends(get_s3_client)) -> BucketStats:
    if not client.bucket_exists(bucket_name):
        raise HTTPException(status_code=404, detail=f"Bucket '{bucket_name}' не найден")
    objects = client.list_objects(bucket_name)
    return BucketStats(
        name=bucket_name,
        objects_count=len(objects),
        total_size=sum(obj["size"] for obj in objects),
    )


@router.delete("/buckets/{bucket_name}", response_model=OperationResult, summary="Удалить bucket")
def delete_bucket(
    bucket_name: str,
    force: bool = Query(True, description="Удалять непустой bucket вместе с объектами"),
    client: S3Client = Depends(get_s3_client),
) -> OperationResult:
    client.delete_bucket(bucket_name, force=force)
    return OperationResult(status="deleted", resource=bucket_name)