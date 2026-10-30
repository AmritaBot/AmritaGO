"""Self-check for the probe credential path.

A probe must run with the credentials the store holds, never with a key the
browser sends: a plaintext token in the request body is a token in the network
tab and in any log between. This checks both halves.

* ``probe_models`` against a local endpoint that demands a bearer token and a
  project header, which is what a gateway with extra headers looks like: an
  empty key is rejected, the stored key alone gets past the first check but not
  the second, and stored extra headers make the third call succeed.
* The endpoint contract: ``ProbePayload`` names a saved provider and carries no
  key field, and probing a provider that is not stored is a 404.
"""

import asyncio
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Set store and secret environment variables before importing ``app.main``.
os.environ.setdefault("AMRITA_GO_DATA", tempfile.mkdtemp(prefix="amrita-probe-"))
from cryptography.fernet import Fernet

os.environ.setdefault("AMRITA_GO_SECRET", Fernet.generate_key().decode())

from fastapi import HTTPException

from app.core.providers import PROVIDERS, Credentials, probe_models
from app.main import ProbePayload, ProviderPayload, probe_provider, upsert_provider

KEY = "sk-secret-123"
PROJECT = "proj-42"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        auth = self.headers.get("authorization") or ""
        project = self.headers.get("x-project-id") or ""
        if auth != f"Bearer {KEY}":
            self.send_response(401)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"unauthorized"}')
            return
        if project != PROJECT:
            self.send_response(403)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"missing project header"}')
            return
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"data":[{"id":"gateway-large"},{"id":"gateway-small"}]}')

    def log_message(self, *args):  # type: ignore
        pass


def serve() -> HTTPServer:
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


async def check_transport(base: str) -> None:
    anonymous = await probe_models(base, "")
    assert not anonymous.ok, "an empty key must not pass"
    assert "401" in (anonymous.error or ""), anonymous.error
    print("empty key rejected ok")

    key_only = await probe_models(base, KEY)
    assert not key_only.ok, "a key without the project header must not pass"
    assert "403" in (key_only.error or ""), key_only.error
    print("missing header rejected ok")

    full = await probe_models(base, KEY, extra_headers={"X-Project-Id": PROJECT})
    assert full.ok, full.error
    assert full.models == ["gateway-large", "gateway-small"], full.models
    print("extra headers ok")


async def check_endpoint(base: str) -> None:
    fields = set(ProbePayload.model_fields)
    assert "api_key" not in fields, f"probe payload accepts a key: {sorted(fields)}"
    assert "provider_id" in fields, f"probe payload lost provider_id: {sorted(fields)}"
    print("payload takes no key ok")

    saved = await upsert_provider(
        ProviderPayload(
            name="probe-gateway",
            base_url=base,
            protocol="openai",
            api_key=KEY,
        )
    )
    provider_id = saved["id"]
    # Extra headers are stored next to the key, not part of the form payload.
    stored = await PROVIDERS.get(provider_id)
    assert stored is not None
    await PROVIDERS.save(stored, Credentials(api_key=KEY, extra_headers={"X-Project-Id": PROJECT}))

    public = await probe_provider(ProbePayload(provider_id=provider_id))
    assert public["ok"], public
    assert public["models"] == ["gateway-large", "gateway-small"], public
    print("probe reads stored credentials ok")

    try:
        await probe_provider(ProbePayload(provider_id="does-not-exist"))
    except HTTPException as exc:
        assert exc.status_code == 404, exc.status_code
        print("unknown provider 404 ok")
    else:
        raise AssertionError("probing an unknown provider must 404")


async def main() -> None:
    server = serve()
    port = server.server_address[1]
    base = f"http://127.0.0.1:{port}/v1"

    await check_transport(base)
    await check_endpoint(base)

    server.shutdown()
    print("probe auth ok")


if __name__ == "__main__":
    asyncio.run(main())
