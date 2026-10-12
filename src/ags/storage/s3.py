import boto3
from botocore.exceptions import ClientError


class S3Storage:
    """Same keys as LocalStorage, in one bucket."""

    def __init__(self, bucket: str, *, region_name: str | None = None, client=None) -> None:
        self._bucket = bucket
        self._s3 = client or boto3.client("s3", region_name=region_name)

    def get(self, key: str) -> bytes | None:
        try:
            return self._s3.get_object(Bucket=self._bucket, Key=key)["Body"].read()
        except ClientError as e:
            if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
                return None
            raise

    def put(self, key: str, data: bytes) -> None:
        self._s3.put_object(Bucket=self._bucket, Key=key, Body=data)

    def put_if_absent(self, key: str, data: bytes) -> bool:
        try:
            self._s3.put_object(Bucket=self._bucket, Key=key, Body=data, IfNoneMatch="*")
        except ClientError as e:
            # 412: key exists. 409: a concurrent conditional write is in flight.
            if e.response["Error"]["Code"] in ("PreconditionFailed", "ConditionalRequestConflict"):
                return False
            raise
        return True

    def list(self, prefix: str) -> list[str]:
        keys: list[str] = []
        for page in self._s3.get_paginator("list_objects_v2").paginate(Bucket=self._bucket, Prefix=prefix):
            keys.extend(obj["Key"] for obj in page.get("Contents", []))
        return sorted(keys)
