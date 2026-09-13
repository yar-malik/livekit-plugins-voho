---
title: Voho for LiveKit Agents
description: Saudi and Gulf Arabic speech-to-text and text-to-speech inside LiveKit Agents, with a SIP trunk so it answers a phone.
---

# Voho for LiveKit Agents

Arabic speech for LiveKit: Saudi and Gulf dialects, mixed Arabic and English in one sentence, and a Saudi-hosted deployment when the data cannot leave the Kingdom. This page takes you from nothing to an agent answering a phone number in Najdi, in about fifteen minutes.

## 1. Five minutes to a talking agent

```bash
pip install "livekit-agents[openai,silero]" livekit-plugins-voho
export VOHO_API_KEY=voho_sk_live_...      # app.voho.ai/tokens
export OPENAI_API_KEY=...                  # or any LiveKit LLM plugin
```

```python
from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.plugins import openai, silero, voho

async def entrypoint(ctx: JobContext):
    await ctx.connect()
    session = AgentSession(
        stt=voho.STT(language="ar-SA"),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=voho.TTS(voice="layla"),
        vad=silero.VAD.load(),
    )
    await session.start(
        agent=Agent(instructions="You answer the phone for a Riyadh clinic. Speak Najdi Arabic. Short sentences."),
        room=ctx.room,
    )
    await session.say("أهلاً وسهلاً، معك ليلى. كيف أقدر أساعدك؟")

if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
```

```bash
python agent.py console
```

Speak to it from your terminal. Interrupt it; it stops. Say the product name in English mid-sentence; it keeps up.

A fuller example with a tool call is in the package: [`examples/agent.py`](https://github.com/yar-malik/livekit-plugins-voho/blob/main/examples/agent.py).

## 2. What the plugin does

| Call | Voho endpoint | Behaviour |
| --- | --- | --- |
| `TTS.synthesize(text)` | `POST /v1/speech/stream` | Whole sentence in, chunked PCM out. |
| `TTS.stream()` | `/v1/speech/ws` | Tokens in as the LLM emits them, audio out while the sentence is still being written. |
| `STT.recognize(audio)` | `POST /v1/transcribe` | One utterance, Arabic and English together. |
| `STT.stream()` | `/v1/transcribe/ws` | Interim results while the caller is still talking, then a final. Feeds LiveKit's turn detection and interruption handling. |

Audio is 16-bit mono PCM: 24 kHz from the TTS, 16 kHz into the STT. LiveKit resamples for the room; you never touch a sample.

## 3. Voices and dialects

The dialect is the voice. Pick one from `GET /v1/voices` and pass its id:

| Voice | Register | Notes |
| --- | --- | --- |
| `layla` | Najdi, female | The default. Reception, appointments. |
| `nouf` | Najdi, female | Senior, measured. Collections, compliance. |
| `faisal` | Najdi, male | Authoritative. Long policy text. |
| `omar` | Najdi, male | Quick. Outbound confirmations. |
| `salma` | Hijazi, female | Jeddah's everyday register. |
| `hisham` | Hijazi, male | Formal edge. Banking, insurance. |
| `maryam` | Omani, female | Muscat and the interior. |
| `salim` | Omani, male | Fleet, retail, customer lines. |
| `reem` | Gulf, female | UAE. |
| `khalid`, `yousef` | Modern Standard | Announcements, IVR. |
| `clementine`, `astra`, `marlow`, `vespera` | English | |

The register — the words chosen, whether a caller hears الحين or دحين — comes from the agent's instructions, not only the voice. Tell the LLM which dialect to write in; the voice reads it. The example agent shows the Najdi instruction that works.

Switch mid-call:

```python
tts.update_options(voice="salma")     # a Jeddah caller on a Riyadh line
stt.update_options(language="en-US")  # an English-only stretch
```

## 4. Answering a phone number

LiveKit's SIP service connects a number to a room; the agent joins the room. Nothing in the plugin changes.

**Inbound trunk** — point your carrier's SIP trunk at LiveKit. Saudi numbers (9200, 800, geographic) come from a Saudi carrier with a SIP offering.

```json
// inbound-trunk.json
{
  "trunk": {
    "name": "Riyadh clinic line",
    "numbers": ["+9668001234567"],
    "krisp_enabled": true
  }
}
```

```bash
lk sip inbound create inbound-trunk.json
```

**Dispatch rule** — every call on that number gets its own room and your agent:

```json
// dispatch.json
{
  "dispatch_rule": {
    "rule": { "dispatchRuleIndividual": { "roomPrefix": "call-" } },
    "roomConfig": {
      "agents": [{ "agentName": "clinic-line" }]
    }
  }
}
```

```bash
lk sip dispatch create dispatch.json
```

Register the worker under that name:

```python
cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, agent_name="clinic-line"))
```

Ring the number. Telephone audio is 8 kHz; LiveKit resamples in both directions, so nothing needs configuring for it.

**Outbound** — the agent dials: `lk sip participant create` with an outbound trunk, or `ctx.api.sip.create_sip_participant(...)` from the agent. Same plugin, same voices.

## 5. Pricing and usage

| | Rate |
| --- | --- |
| Text-to-speech, `sada-1` | 5¢ per 1,000 characters |
| Text-to-speech, `nabra-1` | 2¢ per 1,000 characters (no streaming) |
| Speech-to-text | 3¢ per started minute of audio |

A typical two-minute customer-service call is roughly 400 characters spoken by the agent and two minutes heard: about 8¢. Billing is per key, from a prepaid balance at [app.voho.ai](https://app.voho.ai). Errors say when it runs out (`insufficient_credit`, HTTP 402) rather than silently degrading.

```bash
curl -H "Authorization: Bearer $VOHO_API_KEY" "https://app.voho.ai/v1/usage?days=30"
```

```json
{
  "window_days": 30,
  "balance_cents": 4120,
  "speech": { "requests": 1840, "characters": 512400, "cost_cents": 2562 },
  "products": [{ "product": "ai-voice-assistant", "action": "transcribe", "unit": "minute", "units": 3610, "cost_cents": 10830 }],
  "total_cost_cents": 13392
}
```

`?token=self` narrows to the calling key — one key per environment, one line per environment.

## 6. Data, residency and retention

- **Audio is not stored.** Speech in and speech out are processed and discarded; what remains is the character count and the minute count that billing needs, and the transcript text only if your own agent stores it.
- **Where it runs.** The public API runs in London. A Saudi-resident deployment — same API, same plugin, `base_url=` pointed at it — is available for enterprise contracts, on a KSA region or on your own hardware. See [voho.ai/security](https://voho.ai/security) and [Private Enterprise AI](https://voho.ai/solutions/private-enterprise-ai).
- **Keys.** `voho_sk_live_…` keys are stored hashed; the server never holds the plain key. Revoke and reissue from the console at any time; usage is attributed per key.
- **Latency.** Measured from a client about 100 ms from the API, with a warm connection: the agent starts speaking a sentence 430 to 480 ms after the plugin receives it, of which about 200 ms is synthesis and the rest is the network. The plugin keeps its speech socket warm between sentences and opens it on `prewarm()`, so the first sentence of a call is no slower than the rest. A client in the same region as an in-Kingdom deployment removes most of the network share. Streaming STT returns interim transcripts while the caller is still speaking, and a final one at each pause.

## 7. Errors and retries

Voho's error codes surface in the exception message, so a log line says what to fix:

| Code | HTTP | Means |
| --- | --- | --- |
| `unauthorized` | 401 | Wrong or missing key |
| `insufficient_credit` | 402 | Balance exhausted; top up |
| `unknown_voice` / `unknown_model` / `unknown_format` | 400 | Check `GET /v1/voices` |
| `text_too_long` | 400 | Over 5,000 characters in one request |
| `unavailable` | 503 | Upstream engine down; retried |

Client errors are not retried. Server errors and rate limits are, on LiveKit's normal `APIConnectOptions`. A dropped socket mid-utterance is a connection error and LiveKit reconnects.

## 8. Running the tests

```bash
git clone https://github.com/yar-malik/livekit-plugins-voho
cd livekit-plugins-voho
pip install -e ".[dev]"
pytest                          # against an in-process mock, no key needed
VOHO_API_KEY=... pytest -m live # one real synthesis and one real round trip
```

## Reference

- Package: [`livekit-plugins-voho` on PyPI](https://pypi.org/project/livekit-plugins-voho/)
- API reference: [docs.voho.ai](https://docs.voho.ai)
- Voices: `GET https://app.voho.ai/v1/voices`
- Support: hello@voho.ai
