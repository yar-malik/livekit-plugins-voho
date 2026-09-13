import os

import numpy as np
import pytest
from livekit import rtc
from livekit.agents import stt as lk_stt
from livekit.plugins import voho

from conftest import TOKEN, collect


def frame(ms: int = 20, rate: int = 16000) -> rtc.AudioFrame:
    n = rate * ms // 1000
    data = (np.sin(np.arange(n) / 8.0) * 8000).astype(np.int16)
    return rtc.AudioFrame(data=data.tobytes(), sample_rate=rate, num_channels=1, samples_per_channel=n)


async def test_recognize_posts_wav(base_url, seen, http):
    stt = voho.STT(language="ar-OM", api_key=TOKEN, base_url=base_url, http_session=http)
    event = await stt.recognize([frame(100), frame(100)])
    assert event.type == lk_stt.SpeechEventType.FINAL_TRANSCRIPT
    assert event.alternatives[0].text == "هلا والله"
    assert event.alternatives[0].language == "ar-OM"
    assert event.alternatives[0].confidence == pytest.approx(0.93)

    sent = seen.transcribe_forms[0]
    assert sent["language"] == "ar-OM"
    assert sent["name"].endswith(".wav")
    assert sent["bytes"] > 44 + 2 * 16000 * 0.2 * 0.9  # a real WAV of ~200 ms


async def test_stream_emits_interim_then_final(base_url, seen, http):
    stt = voho.STT(api_key=TOKEN, base_url=base_url, http_session=http)
    stream = stt.stream()
    for _ in range(4):
        stream.push_frame(frame())
    stream.end_input()
    events = await collect(stream)
    await stream.aclose()

    kinds = [e.type for e in events]
    assert kinds[0] == lk_stt.SpeechEventType.START_OF_SPEECH
    assert lk_stt.SpeechEventType.INTERIM_TRANSCRIPT in kinds
    assert kinds[-2] == lk_stt.SpeechEventType.FINAL_TRANSCRIPT
    assert kinds[-1] == lk_stt.SpeechEventType.END_OF_SPEECH

    final = [e for e in events if e.type == lk_stt.SpeechEventType.FINAL_TRANSCRIPT][0]
    assert final.alternatives[0].text == "هلا والله، معك ليلى."
    assert final.alternatives[0].language.lower() == "ar-x-gulf"  # LiveKit normalises the tag
    assert final.alternatives[0].confidence == pytest.approx(0.9)

    assert seen.ws_transcribe_start == {"type": "start", "language": "ar-SA", "sample_rate": 16000, "encoding": "pcm"}
    assert seen.ws_transcribe_bytes == 4 * 320 * 2  # four 20 ms frames of 16-bit PCM at 16 kHz


async def test_stream_resamples_to_16k(base_url, seen, http):
    stt = voho.STT(api_key=TOKEN, base_url=base_url, http_session=http)
    stream = stt.stream()
    for _ in range(4):
        stream.push_frame(frame(rate=48000))
    stream.end_input()
    await collect(stream)
    await stream.aclose()
    # 48 kHz in, 16 kHz on the wire: a third of the bytes.
    assert seen.ws_transcribe_bytes == 4 * 320 * 2


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("VOHO_API_KEY"), reason="needs VOHO_API_KEY")
async def test_live_round_trip():
    """Say a sentence with the TTS, hear it back with the STT."""
    import aiohttp

    async with aiohttp.ClientSession() as http:
        tts = voho.TTS(voice="layla", http_session=http)
        stream = tts.synthesize("السلام عليكم ورحمة الله.")
        pcm = b"".join(f.frame.data.tobytes() for f in await collect(stream))
        await stream.aclose()
        assert len(pcm) > 24000

        n = len(pcm) // 2
        spoken = rtc.AudioFrame(data=pcm, sample_rate=24000, num_channels=1, samples_per_channel=n)
        stt = voho.STT(language="ar-SA", http_session=http)
        event = await stt.recognize(spoken)
        assert "السلام" in event.alternatives[0].text
