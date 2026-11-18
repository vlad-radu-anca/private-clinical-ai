import pytest

from clinical_rag.aws import fetch, split_s3_uri


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        ("s3://bucket/models/qwen3-8b", ("bucket", "models/qwen3-8b/")),
        ("s3://bucket/models/qwen3-8b/", ("bucket", "models/qwen3-8b/")),
        ("s3://bucket", ("bucket", "")),
    ],
)
def test_s3_uris_are_split_into_bucket_and_prefix(uri, expected):
    assert split_s3_uri(uri) == expected


def test_anything_but_s3_is_rejected():
    with pytest.raises(ValueError):
        split_s3_uri("https://bucket/models")


class FakeS3:
    def __init__(self, objects):
        self.objects = objects
        self.downloaded = []

    def get_paginator(self, _name):
        objects = self.objects

        class Paginator:
            def paginate(self, Bucket, Prefix):  # noqa: N803 -- boto3's parameter names
                yield {"Contents": [{"Key": k, "Size": len(v)} for k, v in objects.items() if k.startswith(Prefix)]}

        return Paginator()

    def download_file(self, bucket, key, path):
        self.downloaded.append(key)
        with open(path, "wb") as fh:
            fh.write(self.objects[key])


def test_fetch_copies_a_prefix_and_skips_files_already_complete(tmp_path):
    s3 = FakeS3({"models/m/config.json": b"{}", "models/m/weights/a.safetensors": b"1234", "models/other/x": b"no"})
    (tmp_path / "config.json").write_bytes(b"{}")

    copied = fetch("s3://bucket/models/m/", str(tmp_path), s3)

    assert copied == 1
    assert s3.downloaded == ["models/m/weights/a.safetensors"]
    assert (tmp_path / "weights" / "a.safetensors").read_bytes() == b"1234"
    assert not (tmp_path / "x").exists()


def test_fetch_fails_loudly_when_the_prefix_is_empty(tmp_path):
    with pytest.raises(RuntimeError):
        fetch("s3://bucket/models/missing/", str(tmp_path), FakeS3({}))
