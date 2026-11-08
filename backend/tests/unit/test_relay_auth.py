"""원천 인증 — NTLM(IIS 의 Windows 인증)과 클라이언트 인증서(mTLS).

- NTLM 은 진짜 주고받기다 — 가짜 원천이 pyspnego 의 NTLM **서버**로 토큰을 푼다(계정 파일
  `NTLM_USER_FILE`). 틀린 비밀번호는 거절된다.
- mTLS 는 진짜 TLS 다 — 사설 CA 로 서버 · 클라이언트 인증서를 짓고, 클라이언트 인증서를
  요구하는 서버에 닿는다(인증서 없이는 닿지 않는다).
- 정의는 인증서 폴더 안의 **파일 이름만** 받는다.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import ssl
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest

from relay_core.adapters.rest import Auth, RestAdapter, RestSource, SourceError
from relay_core.definition import DefinitionError, build_adapter, check_adapter, env_names


def _rest(
    auth: Auth, *, base_url: str = "http://iis.test", client: httpx.Client | None = None
) -> RestAdapter:
    return RestAdapter(
        RestSource(
            base_url=base_url,
            path="/api/lots",
            key="id",
            columns={"ID": "id"},
            paging="none",
            rows_path="",
            auth=auth,
            retries=1,
        ),
        client=client,
        sleep=lambda _s: None,
    )


def _ntlm_site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> httpx.Client:
    import spnego

    users = tmp_path / "ntlm-users"
    users.write_text("CORP:svc_relay:Pa55word!\n", encoding="utf-8")
    monkeypatch.setenv("NTLM_USER_FILE", str(users))
    state: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        given = request.headers.get("Authorization", "")
        if not given.startswith("NTLM "):
            return httpx.Response(401, headers={"WWW-Authenticate": "NTLM"})
        server = state.setdefault("server", spnego.server(protocol="ntlm"))
        try:
            out = server.step(base64.b64decode(given[5:]))
        except Exception:
            state.clear()
            return httpx.Response(401, text="bad credentials")
        if not server.complete:
            return httpx.Response(
                401,
                headers={"WWW-Authenticate": "NTLM " + base64.b64encode(out or b"").decode()},
            )
        state.clear()
        return httpx.Response(200, json=[{"id": "L1"}])

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_NTLM_은_협상_도전_응답을_주고받는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _ntlm_site(tmp_path, monkeypatch)
    good = _rest(Auth(kind="ntlm", user="CORP\\svc_relay", token="Pa55word!"), client=client)
    assert [one.key for one in good.fetch(None).records] == ["L1"]
    bad = _rest(Auth(kind="ntlm", user="CORP\\svc_relay", token="wrong-pass"), client=client)
    with pytest.raises(SourceError) as caught:
        bad.fetch(None)
    assert "wrong-pass" not in str(caught.value)


def _pem(tmp_path: Path) -> dict[str, Path]:
    """사설 CA · 서버(localhost) · 클라이언트 인증서 — cryptography 로 짓는다."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    now = dt.datetime.now(dt.UTC)

    def name(text: str) -> x509.Name:
        return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, text)])

    def cert(
        subject: str,
        key: Any,
        issuer: x509.Name,
        signer: Any,
        *,
        ca: bool = False,
        usage: Any = None,
        san: bool = False,
    ) -> x509.Certificate:
        built = (
            x509.CertificateBuilder()
            .subject_name(name(subject))
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=1))
            .not_valid_after(now + dt.timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        )
        if usage is not None:
            built = built.add_extension(x509.ExtendedKeyUsage([usage]), critical=False)
        if san:
            import ipaddress

            built = built.add_extension(
                x509.SubjectAlternativeName(
                    [
                        x509.DNSName("localhost"),
                        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                    ]
                ),
                critical=False,
            )
        return built.sign(signer, hashes.SHA256())

    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = cert("Corp Test CA", ca_key, name("Corp Test CA"), ca_key, ca=True)
    out: dict[str, Path] = {}

    def save(label: str, crt: x509.Certificate, key: Any = None) -> None:
        body = crt.public_bytes(serialization.Encoding.PEM)
        if key is not None:
            body += key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        out[label] = tmp_path / f"{label}.pem"
        out[label].write_bytes(body)

    save("ca", ca)
    server_key = ec.generate_private_key(ec.SECP256R1())
    save(
        "server",
        cert(
            "localhost",
            server_key,
            ca.subject,
            ca_key,
            usage=ExtendedKeyUsageOID.SERVER_AUTH,
            san=True,
        ),
        server_key,
    )
    client_key = ec.generate_private_key(ec.SECP256R1())
    save(
        "client",
        cert("relay", client_key, ca.subject, ca_key, usage=ExtendedKeyUsageOID.CLIENT_AUTH),
        client_key,
    )
    return out


@pytest.fixture
def mtls_site(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[str, dict[str, Path]]]:
    pems = _pem(tmp_path)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = json.dumps([{"id": "M1"}]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(pems["server"])
    context.load_verify_locations(pems["ca"])
    context.verify_mode = ssl.CERT_REQUIRED  # 클라이언트 인증서를 요구한다
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("SSL_CERT_FILE", str(pems["ca"]))  # 사내 CA(`network.apply` 가 거는 것)
    yield f"https://localhost:{server.server_address[1]}", pems
    server.shutdown()


def test_클라이언트_인증서를_요구하는_원천에_닿는다(
    mtls_site: tuple[str, dict[str, Path]],
) -> None:
    url, pems = mtls_site
    with_cert = Auth(kind="none", cert=str(pems["client"]))
    adapter = RestAdapter(
        RestSource(
            base_url=url,
            path="/x",
            key="id",
            columns={"ID": "id"},
            paging="none",
            rows_path="",
            auth=with_cert,
            retries=1,
        ),
        sleep=lambda _s: None,
    )
    assert [one.key for one in adapter.fetch(None).records] == ["M1"]
    without = RestAdapter(
        RestSource(
            base_url=url,
            path="/x",
            key="id",
            columns={"ID": "id"},
            paging="none",
            rows_path="",
            retries=1,
        ),
        sleep=lambda _s: None,
    )
    with pytest.raises(SourceError):
        without.fetch(None)


def test_정의는_인증서_폴더_안의_파일_이름만_받고_비밀번호도_이름으로(tmp_path: Path) -> None:
    base: dict[str, Any] = {
        "kind": "rest",
        "base_url": "https://plm.test",
        "path": "/x",
        "key": "id",
        "columns": {"ID": "id"},
    }
    for wrong in ("../etc/passwd", "/etc/ssl/key.pem", "a/b.pem"):
        with pytest.raises(DefinitionError, match="파일 이름만"):
            check_adapter({**base, "auth": {"kind": "none", "cert": wrong}})
    with pytest.raises(DefinitionError, match="user"):
        check_adapter({**base, "auth": {"kind": "ntlm", "token_env": "IIS_PASSWORD"}})

    certs = tmp_path / "certs"
    certs.mkdir()
    (certs / "plm.pem").write_bytes(_pem(tmp_path)["client"].read_bytes())
    spec = {
        **base,
        "auth": {
            "kind": "bearer",
            "token_env": "PLM_TOKEN",
            "cert": "plm.pem",
            "cert_password_env": "PLM_CERT_PASS",
        },
    }
    assert env_names(spec) == ["PLM_TOKEN", "PLM_CERT_PASS"]  # 울타리 · 금지 이름이 둘 다 본다
    env = {"PLM_TOKEN": "t", "PLM_CERT_PASS": "p4ssword"}
    with pytest.raises(DefinitionError, match="인증서 폴더에 없습니다"):
        build_adapter(
            {**spec, "auth": {**spec["auth"], "cert": "none.pem"}}, env, cert_root=certs
        )
    with pytest.raises(DefinitionError, match="RELAY_CERT_DIR"):
        build_adapter(spec, env)
    built = build_adapter(spec, env, cert_root=certs)
    auth = built.source.auth
    assert auth.cert == str((certs / "plm.pem").resolve()) and auth.cert_password == "p4ssword"
    assert "p4ssword" in auth.secrets()  # 오류 글에서 지운다
