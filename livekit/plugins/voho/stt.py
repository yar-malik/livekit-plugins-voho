"""Voho speech-to-text for LiveKit Agents.

Arabic with English mixed in, which is how the Gulf speaks on the phone: a
caller starts in Najdi and says the product name in English, and a
transcriber locked to one language writes nonsense at exactly that moment.
Any ``ar-*`` language code here transcribes both.

``recognize(buffer)`` sends a finished utterance to ``POST /v1/transcribe``.
``stream()`` opens ``/v1/transcribe/ws`` and sends audio frames as they arrive,
getting interim results back while the person is still talking — which is
what LiveKit's turn detection and interruption handling are built to consume.
Input is resampled to 16 kHz mono by the base class before it reaches here.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, replace

import aiohttp
from livekit import rtc

from livekit.agents import (
    DEFAULT_API_CONNECT_OPTIONS,
    APIConnectOptions,
    stt,
    utils,
)
from livekit.agents.types import NOT_GIVEN, NotGivenOr
from livekit.agents.utils import AudioBuffer, is_given

from . import _api
from .log import logger
from .models import DEFAULT_LANGUAGE, STT_SAMPLE_RATE, STTLanguage


@dataclass
class _Options:
    language: str
    base_url: str


class STT(stt.STT):
    def __init__(
        self,
        *,
        language: STTLanguage = DEFAULT_LANGUAGE,
        api_key: str | None = None,
        base_url: str | None = None,
        http_session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Voho STT.

        Args:
            language: ``ar-SA`` by default. Any Arabic code also picks up English
                in the same sentence. ``en-US`` for an English-only line.
            api_key: A ``voho_sk_live_…`` key, or ``VOHO_API_KEY``.
            base_url: Override for a private or Saudi-hosted deployment, or
                ``VOHO_BASE_URL``.
            http_session: Share LiveKit's session rather than opening one.
        """
        super().__init__(capabilities=stt.STTCapabilities(streaming=True, interim_results=True))
        self._api_key = _api.resolve_api_key(api_key)
        self._opts = _Options(language=language, base_url=_api.resolve_base_url(base_url))
        self._session = http_session

    @property
    def model(self) -> str:
        return "voho-transcribe"

    @property
    def provider(self) -> str:
        return "Voho"

    def _ensure_session(self) -> aiohttp.ClientSession:
        if not self._session:
            self._session = utils.http_context.http_session()
        return self._session

    def update_options(self, *, language: NotGivenOr[STTLanguage] = NOT_GIVEN) -> None:
        if is_given(language):
            self._opts = replace(self._opts, language=language)

    async def _recognize_impl(
        self,
        buffer: AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions,
    ) -> stt.SpeechEvent:
        lang = language if is_given(language) else self._opts.language
        wav = rtc.combine_audio_frames(buffer).to_wav_bytes()

        form = aiohttp.FormData()
        form.add_field("file", wav, filename="utterance.wav", content_type="audio/wav")
        form.add_field("language", lang)

        try:
            async with self._ensure_session().post(
                f"{self._opts.base_url}/v1/transcribe",
                headers=_api.headers(self._api_key),
                data=form,
                timeout=aiohttp.ClientTimeout(total=60, sock_connect=conn_options.timeout),
            ) as resp:
                if resp.status >= 400:
                    raise _api.error_from_body(resp.status, await resp.read())
                body = await resp.json()
        except BaseException as exc:
            raise _api.translate(exc) from exc

        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            request_id=utils.shortuuid("voho_"),
            alternatives=[
                stt.SpeechData(
                    language=lang,
                    text=str(body.get("text") or ""),
                    confidence=float(body.get("confidence") or 0.0),
                )
            ],
        )

    def stream(
        self,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> RecognizeStream:
        return RecognizeStream(
            stt=self,
            language=language if is_given(language) else self._opts.language,
            conn_options=conn_options,
        )

    async def aclose(self) -> None:
        pass


class RecognizeStream(stt.RecognizeStream):
    """Frames in, transcripts out, on one socket for the life of the stream."""

    def __init__(self, *, stt: STT, language: str, conn_options: APIConnectOptions) -> None:
        super().__init__(stt=stt, conn_options=conn_options, sample_rate=STT_SAMPLE_RATE)
        self._stt: STT = stt
        self._language = language

    async def _run(self) -> None:
        opts = self._stt._opts
        request_id = utils.shortuuid("voho_")
        ws: aiohttp.ClientWebSocketResponse | None = None

        try:
            ws = await asyncio.wait_for(
                self._stt._ensure_session().ws_connect(
                    _api.ws_url(opts.base_url, "/v1/transcribe/ws"),
                    headers=_api.headers(self._stt._api_key),
                ),
                self._conn_options.timeout,
            )
            await _expect(ws, "ready")
            await ws.send_str(
                json.dumps(
                    {"type": "start", "language": self._language, "sample_rate": STT_SAMPLE_RATE, "encoding": "pcm"}
                )
            )
            await _expect(ws, "started")

            async def send() -> None:
                assert ws is not None
                async for item in self._input_ch:
                    if isinstance(item, self._FlushSentinel):
                        # A flush is a hint that an utterance has ended. The
                        # server segments on silence itself, so there is
                        # nothing to send; the final result follows on its own.
                        continue
                    await ws.send_bytes(item.data.tobytes())
                await ws.send_str(json.dumps({"type": "stop"}))

            async def receive() -> None:
                assert ws is not None
                speaking = False
                async for msg in ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                            break
                        continue
                    frame = json.loads(msg.data)
                    kind = frame.get("type")
                    if kind == "transcript":
                        text = str(frame.get("text") or "")
                        if not text:
                            continue
                        final = bool(frame.get("final"))
                        data = stt.SpeechData(
                            language=str(frame.get("language") or self._language),
                            text=text,
                            confidence=float(frame.get("confidence") or 0.0),
                        )
                        if not speaking:
                            speaking = True
                            self._event_ch.send_nowait(
                                stt.SpeechEvent(type=stt.SpeechEventType.START_OF_SPEECH, request_id=request_id)
                            )
                        self._event_ch.send_nowait(
                            stt.SpeechEvent(
                                type=stt.SpeechEventType.FINAL_TRANSCRIPT if final else stt.SpeechEventType.INTERIM_TRANSCRIPT,
                                request_id=request_id,
                                alternatives=[data],
                            )
                        )
                        if final:
                            speaking = False
                            self._event_ch.send_nowait(
                                stt.SpeechEvent(type=stt.SpeechEventType.END_OF_SPEECH, request_id=request_id)
                            )
                    elif kind == "done":
                        return
                    elif kind == "error":
                        raise _api.error_from_frame(frame)
                    else:
                        logger.debug("voho stt: ignoring frame %s", kind)
                # The server closed without saying done; if we were still
                # sending, that is a dropped connection and LiveKit may retry.
                if not self._input_ch.closed:
                    raise aiohttp.ClientConnectionError("voho: transcription socket closed unexpectedly")

            send_task = asyncio.create_task(send(), name="voho-stt-send")
            recv_task = asyncio.create_task(receive(), name="voho-stt-recv")
            try:
                await asyncio.gather(send_task, recv_task)
            finally:
                for t in (send_task, recv_task):
                    if not t.done():
                        t.cancel()
        except BaseException as exc:
            raise _api.translate(exc) from exc
        finally:
            if ws is not None:
                try:
                    await ws.close()
                except Exception:
                    pass


async def _expect(ws: aiohttp.ClientWebSocketResponse, kind: str) -> dict:
    async for msg in ws:
        if msg.type != aiohttp.WSMsgType.TEXT:
            continue
        frame = json.loads(msg.data)
        if frame.get("type") == kind:
            return frame
        if frame.get("type") == "error":
            raise _api.error_from_frame(frame)
    raise aiohttp.ClientConnectionError(f"voho: socket closed while waiting for {kind}")
