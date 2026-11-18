"""The two things the service needs from AWS when it runs on EKS.

- Database credentials, read from the RDS-managed secret at startup through the pod's own identity
  (EKS Pod Identity). No password is stored in Kubernetes, in an image or in Terraform state.
- Model files, copied from S3 to local disk before vLLM or the embeddings server starts:

      python -m clinical_rag.aws fetch s3://bucket/models/qwen3-8b/ /models/qwen3-8b

  The cluster has no internet access, so models are imported into S3 once and served from there.

boto3 is imported inside the functions, so running locally with Ollama does not need AWS libraries.
"""

import json
import logging
import os
import sys
from pathlib import Path
from urllib.parse import quote

log = logging.getLogger("aws")


def database_url_from_secret(secret_arn: str, host: str, database: str, port: int = 5432) -> str:
    """Build a PostgreSQL URL from an RDS-managed secret, which holds only the username and password."""
    import boto3

    secret = json.loads(boto3.client("secretsmanager").get_secret_value(SecretId=secret_arn)["SecretString"])
    user, password = quote(secret["username"], safe=""), quote(secret["password"], safe="")
    return f"postgresql://{user}:{password}@{host}:{port}/{database}?sslmode=require"


def split_s3_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("s3://"):
        raise ValueError(f"not an s3:// URI: {uri}")
    bucket, _, prefix = uri[len("s3://") :].partition("/")
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return bucket, prefix


def fetch(uri: str, target: str, s3=None) -> int:
    """Copy every object under an S3 prefix into a local directory. Files already present with the
    same size are skipped, so a restarted pod does not download 16 GB again."""
    if s3 is None:
        import boto3

        s3 = boto3.client("s3")
    bucket, prefix = split_s3_uri(uri)
    root = Path(target)
    copied = 0
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            relative = obj["Key"][len(prefix) :]
            if not relative or relative.endswith("/"):
                continue
            path = root / relative
            if path.exists() and path.stat().st_size == obj["Size"]:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(bucket, obj["Key"], str(path))
            copied += 1
            log.info("fetched %s (%.1f MB)", relative, obj["Size"] / 1e6)
    if not any(root.rglob("*")):
        raise RuntimeError(f"nothing found under {uri}")
    return copied


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if len(sys.argv) != 4 or sys.argv[1] != "fetch":
        print("usage: python -m clinical_rag.aws fetch s3://bucket/prefix/ /local/dir", file=sys.stderr)
        return 2
    copied = fetch(sys.argv[2], sys.argv[3])
    log.info("done: %d files copied to %s", copied, sys.argv[3])
    return 0


if __name__ == "__main__":
    os.umask(0o022)
    sys.exit(main())
