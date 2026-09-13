"""A Voho API small enough to fit in a test.

Speaks the same four contracts the plugin does — the two REST routes and the
two sockets — with the same frame names and error shapes, so a test that
passes here fails for the same reasons it would fail against production. It
does not synthesise or transcribe anything: audio out is a recognisable byte
pattern, text out is a fixed phrase, and what is asserted is that the plugin
drove the protocol correctly and turned the answers into LiveKit's types.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

import aiohttp
import pytest
import pytest_asyncio
from aiohttp import web

TOKEN = "voho_sk_live_test"
PCM_CHUNK = bytes(range(256)) * 4  # 1,024 bytes, recognisable and not silence


@dataclass
class Seen:
    """What the mock saw, for assertions."""

    speech_bodies: list[dict] = field(default_factory=list)
    transcribe_forms: list[dict] = field(default_factory=list)
    ws_speech_frames: list[list[dict]] = field(default_factory=list)
    ws_speech_sockets: int = 0
    ws_transcribe_bytes: int = 0
    ws_transcribe_start: dict | None = None


def _unauthorized() -> web.Response:
    return web.json_response({"error": {"code": "unauthorized", "message": "Invalid or missing API token."}}, status=401)


def _authed(request: web.Request) -> bool:
    return request.headers.get("Authorization") == f"Bearer {TOKEN}"


def make_app(seen: Seen, *, fail_speech_with: tuple[int, str] | None = None) -> web.Application:
    async def speech_stream(request: web.Request) -> web.StreamResponse:
        if not _authed(request):
            return _unauthorized()
        body = await request.json()
        seen.speech_bodies.append(body)
        if fail_speech_with:
            status, code = fail_speech_with
            return web.json_response({"error": {"code": code, "message": "as configured"}}, status=status)
        resp = web.StreamResponse(status=200, headers={"Content-Type": "audio/L16", "X-Voho-Format": "pcm"})
        await resp.prepare(request)
        for _ in range(3):
            await resp.write(PCM_CHUNK)
        await resp.write_eof()
        return resp

    async def transcribe(request: web.Request) -> web.Response:
        if not _authed(request):
            return _unauthorized()
        form = await request.post()
        file = form["file"]
        seen.transcribe_forms.append({"language": form.get("language"), "bytes": len(file.file.read()), "name": file.filename})
        return web.json_response({"text": "هلا والله", "seconds": 1.2, "confidence": 0.93, "cost_cents": 3})

    async def speech_ws(request: web.Request) -> web.WebSocketResponse:
        if not _authed(request):
            return _unauthorized()
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        # Like production: one socket, any number of utterances in sequence,
        # each opened by start and closed by flush -> done.
        seen.ws_speech_sockets += 1
        frames: list[dict] = []
        seen.ws_speech_frames.append(frames)
        await ws.send_json({"type": "ready"})
        chars = 0
        async for msg in ws:
            if msg.type != web.WSMsgType.TEXT:
                continue
            frame = json.loads(msg.data)
            frames.append(frame)
            if frame["type"] == "start":
                chars = 0
                await ws.send_json({"type": "started", "voice": frame["voice"], "model": frame.get("model"), "format": frame.get("format")})
            elif frame["type"] == "text":
                chars += len(frame["text"])
                await ws.send_bytes(PCM_CHUNK)
            elif frame["type"] == "flush":
                await ws.send_bytes(PCM_CHUNK)
                await ws.send_json({"type": "done", "bytes": 0, "characters": chars, "cost_cents": 1})
        return ws

    async def transcribe_ws(request: web.Request) -> web.WebSocketResponse:
        if not _authed(request):
            return _unauthorized()
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": "ready"})
        async for msg in ws:
            if msg.type == web.WSMsgType.BINARY:
                seen.ws_transcribe_bytes += len(msg.data)
                # Two interims and a final once enough audio has arrived.
                if seen.ws_transcribe_bytes == len(msg.data):
                    await ws.send_json({"type": "transcript", "text": "هلا", "final": False})
                elif seen.ws_transcribe_bytes >= 3 * len(msg.data) and not getattr(ws, "_final_sent", False):
                    await ws.send_json({"type": "transcript", "text": "هلا والله معك", "final": False})
                    await ws.send_json({"type": "transcript", "text": "هلا والله، معك ليلى.", "final": True, "confidence": 0.9, "language": "ar-x-gulf"})
                    ws._final_sent = True  # type: ignore[attr-defined]
            elif msg.type == web.WSMsgType.TEXT:
                frame = json.loads(msg.data)
                if frame["type"] == "start":
                    seen.ws_transcribe_start = frame
                    await ws.send_json({"type": "started", "language": frame.get("language"), "sample_rate": frame.get("sample_rate"), "encoding": frame.get("encoding")})
                elif frame["type"] == "stop":
                    await ws.send_json({"type": "done", "seconds": seen.ws_transcribe_bytes / 32000, "cost_cents": 3})
                    break
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_post("/v1/speech/stream", speech_stream)
    app.router.add_post("/v1/transcribe", transcribe)
    app.router.add_get("/v1/speech/ws", speech_ws)
    app.router.add_get("/v1/transcribe/ws", transcribe_ws)
    return app


@pytest.fixture
def seen() -> Seen:
    return Seen()


@pytest_asyncio.fixture
async def base_url(seen: Seen):
    runner = web.AppRunner(make_app(seen))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        await runner.cleanup()


@pytest_asyncio.fixture
async def failing_base_url(seen: Seen):
    runner = web.AppRunner(make_app(seen, fail_speech_with=(402, "insufficient_credit")))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        await runner.cleanup()


@pytest_asyncio.fixture
async def http():
    """Plugins outside a LiveKit worker must be handed a session; see the
    plugin's own error message. Tests are exactly that case."""
    async with aiohttp.ClientSession() as session:
        yield session


async def collect(aiter):
    out = []
    async for item in aiter:
        out.append(item)
    return out


async def wait_for(predicate, timeout: float = 5.0):
    loop = asyncio.get_event_loop()
    end = loop.time() + timeout
    while not predicate():
        if loop.time() > end:
            raise TimeoutError("condition not met")
        await asyncio.sleep(0.02)
