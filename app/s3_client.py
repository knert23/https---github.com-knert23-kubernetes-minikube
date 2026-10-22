from __future__ import annotations

import logging
from typing import Any, Optional
from xml.etree import ElementTree

import requests

from .config import settings

logger = logging.getLogger(__name__)


class S3Error(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(message)

def _parse_xml(content: bytes) -> ElementTree.Element:
    root = ElementTree.fromstring(content)
    # Убираем namespace из тегов, чтобы искать их по обычным именам
    for elem in root.iter():
        if isinstance(elem.tag, str) and elem.tag.startswith("{"):
            elem.tag = elem.tag.split("}", 1)[1]
    return root

def _handle_response(response: requests.Response, context: str) -> None:
    if response.ok:
        return
    
    # Пытаемся извлечь сообщение об ошибке из XML
    message = f"{context}: HTTP {response.status_code}"
    try:
        root = _parse_xml(response.content)
        error_msg = root.findtext(".//Message")
        if error_msg:
            message = f"{context}: {error_msg}"
    except ElementTree.ParseError:
        pass
    
    raise S3Error(response.status_code, message)


class S3Client:
    def __init__(self) -> None:
        settings.validate()
        self._headers = {
            "Authorization": f"Bearer {settings.iam_token}",
        }
        self._base_url = settings.endpoint_url.rstrip("/")

    # ------------------------- Buckets -------------------------

    def list_buckets(self) -> list[dict[str, Any]]:
        url = f"{self._base_url}/"
        response = requests.get(url, headers=self._headers, timeout=30)
        _handle_response(response, "list_buckets")
        
        # Парсинг XML-ответа
        root = _parse_xml(response.content)
        buckets = []
        for bucket_elem in root.findall(".//Bucket"):
            name = bucket_elem.findtext("Name")
            creation_date = bucket_elem.findtext("CreationDate")
            if name:
                buckets.append({
                    "name": name,
                    "creation_date": creation_date,
                })
        return buckets

    def create_bucket(self, bucket_name: str) -> dict[str, Any]:
        url = f"{self._base_url}/{bucket_name}"
        response = requests.put(url, headers=self._headers, timeout=30)
        _handle_response(response, f"create_bucket '{bucket_name}'")
        logger.info("Bucket created: %s", bucket_name)
        return {"name": bucket_name, "status": "created"}

    def bucket_exists(self, bucket_name: str) -> bool:
        url = f"{self._base_url}/{bucket_name}"
        response = requests.head(url, headers=self._headers, timeout=10)
        return response.status_code == 200

    def delete_bucket(self, bucket_name: str, force: bool = True) -> dict[str, Any]:
        if force:
            self._clear_bucket(bucket_name)
        
        url = f"{self._base_url}/{bucket_name}"
        response = requests.delete(url, headers=self._headers, timeout=30)
        _handle_response(response, f"delete_bucket '{bucket_name}'")
        logger.info("Bucket deleted: %s", bucket_name)
        return {"name": bucket_name, "status": "deleted"}

    def _clear_bucket(self, bucket_name: str) -> None:
        objects = self.list_objects(bucket_name)
        for obj in objects:
            try:
                self.delete_object(bucket_name, obj["key"])
            except S3Error:
                pass  # Продолжаем удалять остальные

    # ------------------------- Objects -------------------------

    def list_objects(self, bucket_name: str, prefix: str = "") -> list[dict[str, Any]]:
        url = f"{self._base_url}/{bucket_name}"
        params = {}
        if prefix:
            params["prefix"] = prefix
        
        response = requests.get(url, headers=self._headers, params=params, timeout=30)
        _handle_response(response, f"list_objects '{bucket_name}'")
        
        # Парсинг XML-ответа
        root = _parse_xml(response.content)
        objects = []
        for content_elem in root.findall(".//Contents"):
            key = content_elem.findtext("Key")
            size = content_elem.findtext("Size")
            last_modified = content_elem.findtext("LastModified")
            etag = content_elem.findtext("ETag", "").strip('"')
            
            if key:
                objects.append({
                    "key": key,
                    "size": int(size) if size else 0,
                    "last_modified": last_modified,
                    "etag": etag,
                })
        
        return objects

    def upload_object(self, bucket_name: str, key: str, data: bytes, content_type: Optional[str] = None,) -> dict[str, Any]:
        url = f"{self._base_url}/{bucket_name}/{key}"
        headers = {**self._headers}
        if content_type:
            headers["Content-Type"] = content_type
        
        response = requests.put(url, headers=headers, data=data, timeout=300)
        _handle_response(response, f"upload_object '{bucket_name}/{key}'")
        
        return {
            "bucket": bucket_name,
            "key": key,
            "size": len(data),
            "status": "uploaded",
        }

    def get_object(self, bucket_name: str, key: str) -> dict[str, Any]:
        url = f"{self._base_url}/{bucket_name}/{key}"
        response = requests.get(url, headers=self._headers, timeout=300)
        _handle_response(response, f"get_object '{bucket_name}/{key}'")
        
        content_type = response.headers.get("Content-Type", "application/octet-stream")
        last_modified = response.headers.get("Last-Modified")
        
        return {
            "key": key,
            "content_type": content_type,
            "size": len(response.content),
            "last_modified": last_modified,
            "body": response.content,
        }

    def delete_object(self, bucket_name: str, key: str) -> dict[str, Any]:
        url = f"{self._base_url}/{bucket_name}/{key}"
        response = requests.delete(url, headers=self._headers, timeout=30)
        _handle_response(response, f"delete_object '{bucket_name}/{key}'")
        
        return {
            "bucket": bucket_name,
            "key": key,
            "status": "deleted",
        }

    def get_presigned_url(self, bucket_name: str, key: str, expires_in: Optional[int] = None) -> str:
        # Для IAM-токена возвращаем обычный URL
        # Объект будет доступен пока валиден токен
        return f"{self._base_url}/{bucket_name}/{key}"


_client: Optional[S3Client] = None


def get_s3_client() -> S3Client:
    global _client
    if _client is None:
        _client = S3Client()
    return _client


if __name__ == "__main__":
    client = get_s3_client()
