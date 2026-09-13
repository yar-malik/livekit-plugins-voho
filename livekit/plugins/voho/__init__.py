"""Voho for LiveKit Agents: Arabic speech, Saudi and Gulf dialects.

    from livekit.plugins import voho

    session = AgentSession(
        stt=voho.STT(language="ar-SA"),
        tts=voho.TTS(voice="layla"),
        llm=...,
    )

Set ``VOHO_API_KEY`` or pass ``api_key=``. Keys are created at
https://app.voho.ai/tokens and billed there. Docs: https://docs.voho.ai/livekit
"""

from .models import DEFAULT_LANGUAGE, DEFAULT_MODEL, DEFAULT_VOICE, STTLanguage, TTSModel, TTSVoice
from .stt import STT, RecognizeStream
from .tts import TTS, ChunkedStream, SynthesizeStream
from .version import __version__

__all__ = [
    "STT",
    "TTS",
    "RecognizeStream",
    "ChunkedStream",
    "SynthesizeStream",
    "STTLanguage",
    "TTSModel",
    "TTSVoice",
    "DEFAULT_LANGUAGE",
    "DEFAULT_MODEL",
    "DEFAULT_VOICE",
    "__version__",
]

from livekit.agents import Plugin  # noqa: E402

from .log import logger  # noqa: E402


class VohoPlugin(Plugin):
    def __init__(self) -> None:
        super().__init__(__name__, __version__, __package__, logger)


Plugin.register_plugin(VohoPlugin())

# Cleanup docs of unexported modules
_module = dir()
NOT_IN_ALL = [m for m in _module if m not in __all__]

__pdoc__ = {}
for n in NOT_IN_ALL:
    __pdoc__[n] = False
