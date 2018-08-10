import errno
import os
import socket
from contextlib import closing
from zipfile import ZipFile, is_zipfile

import pytest

from tf_skein._internal import (
    iter_available_sock_addrs,
    encode_fn,
    decode_fn,
    xset_environ,
    zip_inplace
)


def test_iter_available_sock_addrs():
    with closing(iter_available_sock_addrs()) as it:
        sock_addrs = {next(it) for _ in range(5)}
        assert len(sock_addrs) == 5  # No duplicates.

        for host, port in sock_addrs:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            with pytest.raises(OSError) as exc_info:
                s.bind((host, port))

            # Ensure that the iterator holds the sockets open.
            assert exc_info.value.errno == errno.EADDRINUSE


def test_xset_environ(monkeypatch):
    monkeypatch.setattr(os, "environ", {})
    xset_environ(foo="boo")
    assert os.environ["foo"] == "boo"


def test_xset_environ_failure(monkeypatch):
    monkeypatch.setattr(os, "environ", {"foo": "bar"})
    with pytest.raises(RuntimeError):
        xset_environ(foo="boo")

    assert os.environ["foo"] == "bar"


def test_encode_fn_decode_fn():
    def g(x):
        return x

    def f():
        return g(42)

    assert decode_fn(encode_fn(f))() == f()


def test_zip_inplace(tmpdir):
    s = "Hello, world!"
    tmpdir.mkdir("foo").join("bar.txt").write_text(s, encoding="utf-8")
    b = 0xffff.to_bytes(4, "little")
    tmpdir.join("boo.bin").write_binary(b)

    zip_path = zip_inplace(str(tmpdir))
    assert os.path.isfile(zip_path)
    assert zip_path.endswith(".zip")
    assert is_zipfile(zip_path)
    with ZipFile(zip_path) as zf:
        zipped = {zi.filename for zi in zf.filelist}
        assert "foo/" in zipped
        assert "foo/bar.txt" in zipped
        assert "boo.bin" in zipped

        assert zf.read("foo/bar.txt") == s.encode()
        assert zf.read("boo.bin") == b


def test_zip_inplace_replace(tmpdir):
    zip_path = zip_inplace(str(tmpdir))
    stat = os.stat(zip_path)
    zip_inplace(str(tmpdir))
    assert os.stat(zip_path).st_mtime == stat.st_mtime
    zip_inplace(str(tmpdir), replace=True)
    assert os.stat(zip_path).st_mtime > stat.st_mtime


