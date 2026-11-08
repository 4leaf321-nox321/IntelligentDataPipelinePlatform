"""SFTP 파일 원천 — 이름이 맞는 것 중 가장 늦게 바뀐 파일, 그리고 **호스트 키 지문을 적어야**
읽는다(처음 보는 서버를 그냥 믿지 않는다 · 다르면 멈춘다).

진짜 SSH 다 — paramiko 로 이 프로세스 안에 SFTP 서버를 띄운다(임시 폴더를 내보낸다).
"""

from __future__ import annotations

import logging
import os
import socket
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import paramiko
import pytest

from relay_core.adapters.file import FileAdapter, FileSource, fingerprint
from relay_core.adapters.rest import Auth, SourceError

CSV = "LOT_ID,QTY\nL1,10\nL2,20\n"


class _Fake:
    def __init__(self, files: dict[str, tuple[float, bytes]]) -> None:
        self.files = files
        self.read_paths: list[str] = []

    def listdir(self, folder: str) -> list[tuple[str, float]]:
        assert folder == "/export/daily"
        return [(name, mtime) for name, (mtime, _body) in self.files.items()]

    def read(self, path: str) -> bytes:
        self.read_paths.append(path)
        return self.files[path.rsplit("/", 1)[-1]][1]


def _adapter(location: str, **over: Any) -> FileAdapter:
    source = FileSource(
        location=location,
        key="LOT_ID",
        auth=Auth(kind="basic", user="relay", token="pw"),
        **over,
    )
    return FileAdapter(source, sftp=over.pop("sftp", None))


def test_이름이_맞는_것_중_가장_늦게_바뀐_파일을_읽는다() -> None:
    fake = _Fake(
        {
            "lots-1.csv": (100.0, b"LOT_ID\nOLD\n"),
            "lots-2.csv": (200.0, CSV.encode()),
            "x.txt": (300.0, b""),
        }
    )
    adapter = FileAdapter(
        FileSource(location="sftp://mesfs01/export/daily/lots-*.csv", key="LOT_ID"), sftp=fake
    )
    got = adapter.fetch(None)
    assert [one.key for one in got.records] == ["L1", "L2"] and fake.read_paths == [
        "/export/daily/lots-2.csv"
    ]
    for wrong, said in (
        ("sftp://u:p@mesfs01/export/daily/a.csv", "계정"),
        ("sftp://mesfs01/ex*/a.csv", "폴더"),
    ):
        with pytest.raises(SourceError, match=said):
            FileAdapter(FileSource(location=wrong, key="LOT_ID"), sftp=fake).fetch(None)


class _Server(paramiko.ServerInterface):  # type: ignore[misc]  # paramiko 는 타입 꼬리표가 없다
    def check_auth_password(self, username: str, password: str) -> int:
        ok = username == "relay" and password == "s3cret-pw"
        return int(paramiko.AUTH_SUCCESSFUL if ok else paramiko.AUTH_FAILED)

    def get_allowed_auths(self, username: str) -> str:
        return "password"

    def check_channel_request(self, kind: str, chanid: int) -> int:
        return int(
            paramiko.OPEN_SUCCEEDED
            if kind == "session"
            else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED
        )


def _sftp_interface(root: Path) -> type[paramiko.SFTPServerInterface]:
    class Files(paramiko.SFTPServerInterface):  # type: ignore[misc]  # paramiko 는 타입 꼬리표가 없다
        def _real(self, path: str) -> str:
            return str(root / path.lstrip("/"))

        def list_folder(self, path: str) -> Any:
            out = []
            for name in os.listdir(self._real(path)):
                attr = paramiko.SFTPAttributes.from_stat(
                    os.stat(os.path.join(self._real(path), name))
                )
                attr.filename = name
                out.append(attr)
            return out

        def stat(self, path: str) -> Any:
            return paramiko.SFTPAttributes.from_stat(os.stat(self._real(path)))

        lstat = stat

        def open(self, path: str, flags: int, attr: Any) -> Any:
            handle = paramiko.SFTPHandle(flags)
            handle.readfile = open(self._real(path), "rb")  # noqa: SIM115 — 서버가 닫는다
            return handle

    return Files


@pytest.fixture
def sftp_site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[int, str]]:
    # 서버 스레드가 시험이 끝난 뒤 닫힌 로그에 쓰려 하지 않게 — 시험 장치의 소리일 뿐이다.
    monkeypatch.setattr(logging.getLogger("paramiko"), "level", logging.CRITICAL)
    opened: list[paramiko.Transport] = []
    root = tmp_path / "share"
    (root / "export").mkdir(parents=True)
    (root / "export" / "lots-20261108.csv").write_text(CSV, encoding="utf-8")
    host_key = (
        paramiko.Ed25519Key.generate()
        if hasattr(paramiko.Ed25519Key, "generate")
        else paramiko.RSAKey.generate(2048)
    )
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(5)
    stop = threading.Event()

    def serve() -> None:
        listener.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _addr = listener.accept()
            except OSError:
                continue
            transport = paramiko.Transport(conn)
            opened.append(transport)
            transport.add_server_key(host_key)
            transport.set_subsystem_handler("sftp", paramiko.SFTPServer, _sftp_interface(root))
            try:
                transport.start_server(server=_Server())
            except Exception:
                transport.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield listener.getsockname()[1], fingerprint(host_key.asbytes())
    stop.set()
    listener.close()
    thread.join(timeout=2)
    for one in opened:
        one.close()


def test_진짜_SFTP_는_호스트_키를_견주고_맞을_때만_계정을_싣는다(
    sftp_site: tuple[int, str],
) -> None:
    port, known = sftp_site
    where = f"sftp://127.0.0.1:{port}/export/lots-*.csv"
    auth = Auth(kind="basic", user="relay", token="s3cret-pw")

    def adapter(host_key: str, token: str = "s3cret-pw") -> FileAdapter:
        return FileAdapter(
            FileSource(
                location=where,
                key="LOT_ID",
                host_key=host_key,
                timeout=5,
                auth=Auth(kind="basic", user="relay", token=token),
            )
        )

    with pytest.raises(SourceError) as missing:
        adapter("").fetch(None)
    assert known in str(missing.value) and "host_key" in str(missing.value)  # 지문을 보여 준다
    with pytest.raises(SourceError, match="다릅니다"):
        adapter("SHA256:" + "A" * 43).fetch(None)
    got = adapter(known).fetch(None)
    assert [one.key for one in got.records] == ["L1", "L2"]
    with pytest.raises(SourceError) as refused:
        adapter(known, token="wrong-password").fetch(None)
    assert "wrong-password" not in str(refused.value)
    assert auth.kind == "basic"
