import os

import pytest
from livekit.agents import APIStatusError, tts as lk_tts
from livekit.plugins import voho

from conftest import PCM_CHUNK, TOKEN, collect, wait_for


async def test_synthesize_streams_pcm(base_url, seen, http):
    tts = voho.TTS(voice="maryam", api_key=TOKEN, base_url=base_url, http_session=http)
    stream = tts.synthesize("حياك الله")
    frames = await collect(stream)
    await stream.aclose()

    # Three chunks of 1,024 bytes of 16-bit mono at 24 kHz, re-cut by LiveKit's
    # emitter into 200 ms frames with the last one padded to length; what
    # survives is that everything sent arrived, and the last flag.
    total = sum(len(f.frame.data.tobytes()) for f in frames)
    assert total >= 3 * len(PCM_CHUNK)
    assert b"".join(f.frame.data.tobytes() for f in frames).startswith(PCM_CHUNK * 3)
    assert frames[-1].is_final
    assert all(f.frame.sample_rate == 24000 and f.frame.num_channels == 1 for f in frames)

    body = seen.speech_bodies[0]
    assert body == {"text": "حياك الله", "voice": "maryam", "model": "sada-1", "format": "pcm"}


async def test_stream_drives_socket(base_url, seen, http):
    # LiveKit 1.8 creates one SynthesizeStream per segment, so one stream is
    # one Voho session: start, the tokens as they come, flush, done.
    tts = voho.TTS(voice="layla", api_key=TOKEN, base_url=base_url, http_session=http)
    stream = tts.stream()
    stream.push_text("هلا ")
    stream.push_text("والله")
    stream.flush()
    stream.end_input()
    frames = await collect(stream)
    await stream.aclose()

    assert seen.ws_speech_sockets == 1
    (sent,) = seen.ws_speech_frames
    assert [f["type"] for f in sent] == ["start", "text", "text", "flush"]
    assert sent[0] == {"type": "start", "voice": "layla", "model": "sada-1", "format": "pcm"}
    assert [f["text"] for f in sent if f["type"] == "text"] == ["هلا ", "والله"]

    # One audio push per token and one on flush, all in one segment.
    total = sum(len(f.frame.data.tobytes()) for f in frames)
    assert total >= 3 * len(PCM_CHUNK)
    assert len({f.segment_id for f in frames}) == 1
    assert frames[-1].is_final


async def test_stream_without_flush_still_finishes(base_url, seen, http):
    tts = voho.TTS(api_key=TOKEN, base_url=base_url, http_session=http)
    stream = tts.stream()
    stream.push_text("بدون فلش")
    stream.end_input()
    frames = await collect(stream)
    await stream.aclose()
    assert [f["type"] for f in seen.ws_speech_frames[0]] == ["start", "text", "flush"]
    assert frames and frames[-1].is_final


async def test_sockets_are_reused_across_sentences(base_url, seen, http):
    tts = voho.TTS(api_key=TOKEN, base_url=base_url, http_session=http)
    for sentence in ["الجملة الأولى", "الجملة الثانية", "الثالثة"]:
        stream = tts.stream()
        stream.push_text(sentence)
        stream.flush()
        stream.end_input()
        frames = await collect(stream)
        await stream.aclose()
        assert frames and frames[-1].is_final
    await tts.aclose()

    # Three utterances, one handshake: the whole point of the pool.
    assert seen.ws_speech_sockets == 1
    (sent,) = seen.ws_speech_frames
    assert [f["type"] for f in sent] == ["start", "text", "flush"] * 3


async def test_prewarm_opens_before_first_sentence(base_url, seen, http):
    tts = voho.TTS(api_key=TOKEN, base_url=base_url, http_session=http)
    tts.prewarm()
    await wait_for(lambda: seen.ws_speech_sockets == 1)
    assert seen.ws_speech_frames == [[]], "connected and ready, nothing spoken yet"
    await tts.aclose()


async def test_update_options_changes_voice(base_url, seen, http):
    tts = voho.TTS(api_key=TOKEN, base_url=base_url, http_session=http)
    tts.update_options(voice="salma")
    stream = tts.synthesize("دحين")
    await collect(stream)
    await stream.aclose()
    assert seen.speech_bodies[0]["voice"] == "salma"


async def test_error_body_becomes_status_error(failing_base_url, http):
    tts = voho.TTS(api_key=TOKEN, base_url=failing_base_url, http_session=http)
    stream = tts.synthesize("x")
    with pytest.raises(APIStatusError) as exc:
        await collect(stream)
    await stream.aclose()
    assert exc.value.status_code == 402
    assert "insufficient_credit" in str(exc.value)
    assert exc.value.retryable is False


async def test_bad_key_is_401(base_url, http):
    tts = voho.TTS(api_key="voho_sk_live_wrong", base_url=base_url, http_session=http)
    stream = tts.synthesize("x")
    with pytest.raises(APIStatusError) as exc:
        await collect(stream)
    await stream.aclose()
    assert exc.value.status_code == 401


def test_economy_tier_is_not_streaming():
    tts = voho.TTS(model="nabra-1", api_key=TOKEN)
    assert tts.capabilities.streaming is False


def test_key_required(monkeypatch):
    monkeypatch.delenv("VOHO_API_KEY", raising=False)
    with pytest.raises(ValueError):
        voho.TTS()


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("VOHO_API_KEY"), reason="needs VOHO_API_KEY")
async def test_live_synthesis():
    import aiohttp

    async with aiohttp.ClientSession() as http:
        tts = voho.TTS(voice="layla", http_session=http)
        stream = tts.synthesize("السلام عليكم، معك ليلى.")
        frames = await collect(stream)
        await stream.aclose()
    assert sum(len(f.frame.data.tobytes()) for f in frames) > 24000  # more than half a second
