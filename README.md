# livekit-plugins-voho

**Saudi Arabic voice for LiveKit Agents.** Speech-to-text and text-to-speech that sound like the Kingdom answers the phone: Najdi for Riyadh, Hijazi for Jeddah and Makkah, plus Omani, Gulf, Egyptian and Modern Standard Arabic. It also handles English mixed into the same sentence, because that is how Saudi callers actually talk.

```bash
pip install livekit-plugins-voho
```

```python
from livekit.agents import AgentSession
from livekit.plugins import voho

session = AgentSession(
    stt=voho.STT(language="ar-SA"),   # Saudi Arabic, with English mixed in
    tts=voho.TTS(voice="layla"),      # a Najdi voice from Riyadh
    llm=...,                          # any LiveKit LLM plugin
    vad=...,                          # e.g. silero
)
```

Get a key at [app.voho.ai](https://app.voho.ai), then `export VOHO_API_KEY=voho_sk_live_...`.

---

## Why a Saudi-specific plugin

Most "Arabic" voices are Modern Standard Arabic: the register of the news, not of a phone call. A Riyadh caller hears it as a recording. A Jeddah caller hears a Najdi agent as out of town, the way a London voice sounds in Glasgow. The dialect is not a detail. It is whether the caller stays on the line.

Voho is built around that:

- **Saudi dialects as separate voices.** Pick Najdi or Hijazi by name, and switch mid-call when a Jeddah caller rings a Riyadh line.
- **Arabic and English in one sentence.** "أبغى أغيّر الـ delivery address" is transcribed as said, rather than turned into nonsense at the switch.
- **Streaming both ways.** Text is spoken while the LLM is still writing the sentence, and transcripts arrive while the caller is still talking, so LiveKit's turn-taking and barge-in work as designed.
- **Telephone-ready.** Put a LiveKit SIP trunk in front of it and it answers a Saudi 9200, 800 or geographic number.
- **Saudi hosting for enterprise.** The same plugin points at an in-Kingdom or on-premise Voho deployment with one `base_url` change.

## Voices

The dialect is the voice.

| Voice | Dialect | Where it sounds local | Good for |
| --- | --- | --- | --- |
| `layla` | Najdi, female | Riyadh, Qassim, the centre | Reception, appointments (default) |
| `nouf` | Najdi, female | Riyadh | Collections, compliance, escalations |
| `faisal` | Najdi, male | Riyadh | Banking, government, long policy text |
| `omar` | Najdi, male | Riyadh | Outbound confirmations, offers |
| `salma` | Hijazi, female | Jeddah, Makkah, Madinah | Everyday customer service |
| `rawan` | Hijazi, female | Jeddah | Retail, delivery |
| `hisham` | Hijazi, male | Jeddah | Banking, insurance |
| `tariq` | Hijazi, male | Jeddah | Bookings, follow-ups |
| `maryam` | Omani, female | Muscat, the interior | Energy, logistics, marine |
| `salim` | Omani, male | Muscat | Fleet, retail |
| `reem` | Gulf, female | UAE | Light, conversational |
| `maha` | Egyptian, female | Egypt | Reassuring, unhurried |
| `khalid`, `yousef` | Modern Standard | Region-neutral | Announcements, IVR |
| `clementine`, `astra`, `marlow`, `vespera` | English | | Expat and international lines |

```python
tts.update_options(voice="salma")      # a Jeddah caller on a Riyadh line
stt.update_options(language="en-US")   # an English-only stretch
```

**The voice sets the sound. The words set the dialect.** Tell your LLM which dialect to write in, or a Najdi voice will be reading Fusha. A Najdi agent says الحين, وش, زين and أبشر; a Hijazi one says دحين, إيش, إيوه and never says أبشر. [`examples/agent.py`](examples/agent.py) has a Najdi instruction that works.

## A Saudi customer-service agent in one file

[`examples/agent.py`](examples/agent.py) is a complete agent for a Riyadh retailer: it speaks Najdi, follows the caller into English and back, stops when interrupted, and looks up an order with a tool mid-call.

```bash
pip install "livekit-agents[openai,silero]" livekit-plugins-voho
export VOHO_API_KEY=... OPENAI_API_KEY=...
python examples/agent.py console
```

To answer a phone number, create a LiveKit SIP inbound trunk and dispatch rule. The [LiveKit guide](https://docs.voho.ai/livekit) walks through it.

## What the plugin calls

| Method | Voho API | |
| --- | --- | --- |
| `TTS.synthesize()` | `POST /v1/speech/stream` | Whole sentence in, chunked PCM out |
| `TTS.stream()` | `WS /v1/speech/ws` | Tokens in as the LLM writes, audio out as it is produced |
| `STT.recognize()` | `POST /v1/transcribe` | One utterance |
| `STT.stream()` | `WS /v1/transcribe/ws` | Interim and final transcripts while the caller speaks |

Audio is 16-bit mono PCM: 24 kHz from the TTS, 16 kHz into the STT. LiveKit resamples for the room.

## Pricing

| | |
| --- | --- |
| Text-to-speech (`sada-1`, streaming) | 5¢ per 1,000 characters |
| Text-to-speech (`nabra-1`) | 2¢ per 1,000 characters |
| Speech-to-text | 3¢ per started minute |

Prepaid, per key. `GET /v1/usage` returns what a key used this month, in the units it was billed in.

## Data and hosting

- Audio is not stored. Billing keeps character and minute counts, nothing else.
- The public API runs in London.
- In-Kingdom and on-premise deployments are available for enterprise contracts, with the same API and plugin. See [docs.voho.ai/on-prem/residency](https://docs.voho.ai/on-prem/residency).

## Errors

Voho error codes appear in the exception message (`unauthorized`, `insufficient_credit`, `unknown_voice`, `text_too_long`), so the log says what to fix. Client errors are not retried. Server errors and rate limits are, using LiveKit's `APIConnectOptions`.

## Development

```bash
pip install -e ".[dev]"
pytest                               # against a local mock of the API, no key needed
VOHO_API_KEY=... pytest -m live      # real synthesis, and a speak-then-transcribe round trip in Arabic
```

## Links

- LiveKit guide: [docs.voho.ai/livekit](https://docs.voho.ai/livekit)
- API reference: [docs.voho.ai](https://docs.voho.ai)
- Hear the voices: [voho.ai/demos](https://voho.ai/demos)
- Contact: support@voho.ai

Apache-2.0
