"""Voho text-to-speech for LiveKit Agents.

Two paths, because LiveKit asks for two:

``synthesize(text)`` has the whole sentence up front and wants audio back as
fast as it can be produced. That is ``POST /v1/speech/stream`` with a raw
PCM body: chunked HTTP, first audio in roughly 200 ms.

``stream()`` is fed tokens as an LLM emits them and must start speaking before
the sentence is finished. That is ``/v1/speech/ws``: text frames in, audio
frames out, pipelined rather than batched at the end.

Sockets are pooled. Opening one costs a TLS handshake and a key check, several
hundred milliseconds that a caller hears as the agent hesitating before every
sentence; once open, first audio follows the text in about 200 ms. So a socket
that has finished an utterance goes back to the pool for the next, ``prewarm()``
opens one before the first sentence is needed, and a socket idle for longer
than the proxy in front of the API allows is replaced rather than reused.

Audio is 16-bit little-endian mono PCM at 24 kHz in both cases. LiveKit
resamples for the room, so nothing here does.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, replace

import aiohttp

from livekit.agents import (
    DEFAULT_API_CONNECT_OPTIONS,
    APIConnectOptions,
    tts,
    utils,
)
from livekit.agents.types import NOT_GIVEN, NotGivenOr
from livekit.agents.utils import is_given

from . import _api
from .log import logger
from .models import DEFAULT_MODEL, DEFAULT_VOICE, TTS_SAMPLE_RATE, TTSModel, TTSVoice


@dataclass
class _Options:
    voice: str
    model: str
    base_url: str


class TTS(tts.TTS):
    def __init__(
        self,
        *,
        voice: TTSVoice = DEFAULT_VOICE,
        model: TTSModel = DEFAULT_MODEL,
        api_key: str | None = None,
        base_url: str | None = None,
        http_session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Voho TTS.

        Args:
            voice: A voice id from ``GET /v1/voices`` — ``layla`` (Najdi) by
                default; ``salma`` for Hijazi, ``maryam`` for Omani, ``clementine``
                for English. The dialect is the voice.
            model: ``sada-1`` (default, streaming) or ``nabra-1`` (cheaper,
                chunked only). Passing ``nabra-1`` makes ``stream()`` fall back to
                per-sentence synthesis.
            api_key: A ``voho_sk_live_…`` key, or ``VOHO_API_KEY``.
            base_url: Override for a private or Saudi-hosted deployment, or
                ``VOHO_BASE_URL``.
            http_session: Share LiveKit's session rather than opening one.
        """
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=model == "sada-1"),
            sample_rate=TTS_SAMPLE_RATE,
            num_channels=1,
        )
        self._api_key = _api.resolve_api_key(api_key)
        self._opts = _Options(voice=voice, model=model, base_url=_api.resolve_base_url(base_url))
        self._session = http_session
        self._pool = utils.ConnectionPool[aiohttp.ClientWebSocketResponse](
            connect_cb=self._connect_ws,
            close_cb=self._close_ws,
            # Idle sockets are cut by the proxy at 120 s; refresh on use, so a
            # socket in steady use lives on and one left idle is replaced.
            max_session_duration=50,
            mark_refreshed_on_get=True,
        )

    async def _connect_ws(self, timeout: float) -> aiohttp.ClientWebSocketResponse:
        ws = await asyncio.wait_for(
            self._ensure_session().ws_connect(
                _api.ws_url(self._opts.base_url, "/v1/speech/ws"),
                headers=_api.headers(self._api_key),
            ),
            timeout,
        )
        try:
            await asyncio.wait_for(_expect(ws, "ready"), timeout)
        except BaseException:
            await ws.close()
            raise
        return ws

    async def _close_ws(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        await ws.close()

    def prewarm(self) -> None:
        """Open a socket before the first sentence, so it is not paid for then."""
        if self._opts.model == "sada-1":
            self._pool.prewarm()

    @property
    def model(self) -> str:
        return self._opts.model

    @property
    def provider(self) -> str:
        return "Voho"

    def _ensure_session(self) -> aiohttp.ClientSession:
        if not self._session:
            self._session = utils.http_context.http_session()
        return self._session

    def update_options(
        self,
        *,
        voice: NotGivenOr[TTSVoice] = NOT_GIVEN,
        model: NotGivenOr[TTSModel] = NOT_GIVEN,
    ) -> None:
        """Change the voice or model for subsequent requests. Switching dialect
        mid-call — a Hijazi caller on a Najdi line — is this and nothing else."""
        if is_given(voice):
            self._opts = replace(self._opts, voice=voice)
        if is_given(model):
            self._opts = replace(self._opts, model=model)

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> ChunkedStream:
        return ChunkedStream(tts=self, input_text=text, conn_options=conn_options)

    def stream(
        self, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> SynthesizeStream:
        if self._opts.model != "sada-1":
            # The economy tier has no streaming path upstream; LiveKit's own
            # adapter turns sentence-at-a-time synthesis into a stream.
            return super().stream(conn_options=conn_options)  # type: ignore[return-value]
        return SynthesizeStream(tts=self, conn_options=conn_options)

    async def aclose(self) -> None:
        # The pool is ours; the HTTP session is LiveKit's or the caller's.
        await self._pool.aclose()


class ChunkedStream(tts.ChunkedStream):
    """Whole text in, chunked PCM out."""

    def __init__(self, *, tts: TTS, input_text: str, conn_options: APIConnectOptions) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._tts: TTS = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        opts = self._tts._opts
        request_id = utils.shortuuid("voho_")
        try:
            async with self._tts._ensure_session().post(
                f"{opts.base_url}/v1/speech/stream",
                headers={**_api.headers(self._tts._api_key), "Accept": "audio/L16"},
                json={"text": self._input_text, "voice": opts.voice, "model": opts.model, "format": "pcm"},
                timeout=aiohttp.ClientTimeout(total=60, sock_connect=self._conn_options.timeout),
            ) as resp:
                if resp.status >= 400:
                    raise _api.error_from_body(resp.status, await resp.read())

                output_emitter.initialize(
                    request_id=request_id,
                    sample_rate=self._tts.sample_rate,
                    num_channels=1,
                    mime_type="audio/pcm",
                    frame_size_ms=50,
                )
                async for data, _ in resp.content.iter_chunks():
                    output_emitter.push(data)
                output_emitter.flush()
        except BaseException as exc:
            raise _api.translate(exc) from exc


class SynthesizeStream(tts.SynthesizeStream):
    """Tokens in as they arrive, PCM out as it is produced, on a pooled socket."""

    def __init__(self, *, tts: TTS, conn_options: APIConnectOptions) -> None:
        super().__init__(tts=tts, conn_options=conn_options)
        self._tts: TTS = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        output_emitter.initialize(
            request_id=utils.shortuuid("voho_"),
            sample_rate=self._tts.sample_rate,
            num_channels=1,
            mime_type="audio/pcm",
            # LiveKit holds 200 ms of audio before its first frame by default.
            # On a phone line that is 200 ms of silence before every sentence.
            frame_size_ms=50,
            stream=True,
        )
        pool = self._tts._pool

        async def acquire() -> aiohttp.ClientWebSocketResponse:
            # A pooled socket can have been closed by the far end while it sat
            # idle; that one is discarded and the next is tried.
            for _ in range(3):
                ws = await pool.get(timeout=self._conn_options.timeout)
                if not ws.closed:
                    return ws
                pool.remove(ws)
            raise aiohttp.ClientConnectionError("voho: could not obtain an open speech socket")

        async def utterance(first: str) -> bool:
            """Speak one segment. Returns False when input ended without a flush."""
            opts = self._tts._opts
            ws = await acquire()
            ok = False
            try:
                await ws.send_str(json.dumps({"type": "start", "voice": opts.voice, "model": opts.model, "format": "pcm"}))
                await _expect(ws, "started")
                output_emitter.start_segment(segment_id=utils.shortuuid("seg_"))
                self._mark_started()

                async def receive() -> None:
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.BINARY:
                            output_emitter.push(msg.data)
                        elif msg.type == aiohttp.WSMsgType.TEXT:
                            frame = json.loads(msg.data)
                            if frame.get("type") == "done":
                                return
                            if frame.get("type") == "error":
                                raise _api.error_from_frame(frame)
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                            break
                    raise aiohttp.ClientConnectionError("voho: socket closed before the utterance finished")

                recv = asyncio.create_task(receive(), name="voho-tts-recv")
                try:
                    await ws.send_str(json.dumps({"type": "text", "text": first}))
                    flushed = False
                    async for item in self._input_ch:
                        if isinstance(item, self._FlushSentinel):
                            flushed = True
                            break
                        if item:
                            await ws.send_str(json.dumps({"type": "text", "text": item}))
                        if recv.done():
                            recv.result()  # surface a receive-side failure now
                    await ws.send_str(json.dumps({"type": "flush"}))
                    await recv
                finally:
                    if not recv.done():
                        recv.cancel()
                output_emitter.end_segment()
                ok = True
                return flushed
            finally:
                # A socket that finished cleanly goes back for the next
                # sentence; anything else is closed, never reused half-way.
                if ok:
                    pool.put(ws)
                else:
                    pool.remove(ws)

        try:
            async for item in self._input_ch:
                if isinstance(item, self._FlushSentinel) or not item:
                    continue
                if not await utterance(item):
                    break
        except BaseException as exc:
            raise _api.translate(exc) from exc


async def _expect(ws: aiohttp.ClientWebSocketResponse, kind: str) -> dict:
    """Read control frames until the one we are waiting for, raising on error."""
    async for msg in ws:
        if msg.type != aiohttp.WSMsgType.TEXT:
            continue
        frame = json.loads(msg.data)
        if frame.get("type") == kind:
            return frame
        if frame.get("type") == "error":
            raise _api.error_from_frame(frame)
        logger.debug("voho tts: ignoring frame %s while waiting for %s", frame.get("type"), kind)
    raise aiohttp.ClientConnectionError(f"voho: socket closed while waiting for {kind}")
