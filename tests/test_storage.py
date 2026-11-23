"""Storage adapters: the R2 (S3 API) client and its request signing."""

from __future__ import annotations

import pytest

from pixelrelay.storage import LocalDiskStorage, R2Storage, StorageError, sigv4_headers
from tests.fake_s3 import ACCESS_KEY, SECRET_KEY

EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_sigv4_matches_the_aws_documentation_example():
    # "GET Object" example from AWS's Signature Version 4 documentation for S3.
    signed = sigv4_headers(
        method="GET",
        host="examplebucket.s3.amazonaws.com",
        path="/test.txt",
        query="",
        headers={"Range": "bytes=0-9"},
        payload_hash=EMPTY_SHA256,
        access_key="AKIAIOSFODNN7EXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        region="us-east-1",
        amz_date="20130524T000000Z",
    )
    assert signed["authorization"] == (
        "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, "
        "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, "
        "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
    )


@pytest.fixture()
def r2(fake_s3, tmp_path):
    fake_s3.objects.clear()
    return R2Storage(fake_s3.endpoint, f"bucket-{tmp_path.name}", ACCESS_KEY, SECRET_KEY, timeout=5)


def test_r2_round_trip_and_write_once(r2):
    key = "projects/p1/original/a b+c.jpg"  # spaces and + must be encoded the same way they're signed
    assert not r2.exists(key)
    r2.put(key, b"original bytes")
    assert r2.exists(key)
    assert r2.read(key) == b"original bytes"
    with pytest.raises(StorageError, match="refusing to overwrite"):
        r2.put(key, b"something else")
    assert r2.read(key) == b"original bytes"


def test_r2_missing_file_and_bad_keys(r2):
    with pytest.raises(StorageError, match="missing"):
        r2.read("projects/p1/original/none.jpg")
    for bad in ("", "/abs", "a/../b", "a\\b"):
        with pytest.raises(StorageError, match="invalid"):
            r2.put(bad, b"x")


def test_r2_wrong_secret_is_refused(fake_s3, tmp_path):
    bad = R2Storage(fake_s3.endpoint, "bucket", ACCESS_KEY, "not-the-secret", timeout=5)
    with pytest.raises(StorageError):
        bad.read("projects/p1/original/x.jpg")


def test_r2_retries_server_errors(r2, fake_s3, monkeypatch):
    monkeypatch.setattr("pixelrelay.storage.time.sleep", lambda s: None)
    fake_s3.fail_next = 2
    r2.put("projects/p1/mask/m.png", b"mask")
    assert r2.read("projects/p1/mask/m.png") == b"mask"
    fake_s3.fail_next = 10
    with pytest.raises(StorageError, match="unreachable"):
        r2.read("projects/p1/mask/m.png")
    fake_s3.fail_next = 0


def test_local_disk_is_write_once(tmp_path):
    store = LocalDiskStorage(tmp_path)
    store.put("projects/p/original/x.jpg", b"1")
    with pytest.raises(StorageError):
        store.put("projects/p/original/x.jpg", b"2")
    assert store.read("projects/p/original/x.jpg") == b"1"
    with pytest.raises(StorageError):
        store.put("../escape.jpg", b"x")
