"""Names the API accepts, so a typo is a type error rather than a 400 at runtime.

These mirror the catalogue served by ``GET /v1/voices``. The literal types are
for editor completion and static checking; any string is still passed through,
so a voice added to the catalogue after this release works without an upgrade.
"""

from typing import Literal, Union

TTSModel = Literal["sada-1", "nabra-1"]
"""``sada-1`` is the flagship and the only tier with a streaming path."""

TTSVoice = Union[
    Literal[
        # Najdi — Riyadh and the centre
        "layla", "nouf", "faisal", "omar",
        # Hijazi — Jeddah, Makkah, Madinah
        "salma", "rawan", "hisham", "tariq",
        # Omani — Muscat and the interior
        "maryam", "salim",
        # Gulf, Egyptian, Modern Standard
        "reem", "maha", "khalid", "yousef",
        # English
        "astra", "bancroft", "clementine", "marlow", "vespera", "cupola",
    ],
    str,
]

STTLanguage = Union[Literal["ar-SA", "ar-OM", "ar-AE", "ar-EG", "ar", "en-US", "en-GB"], str]
"""Any Arabic code also transcribes English mixed into the same sentence."""

DEFAULT_VOICE: TTSVoice = "layla"
DEFAULT_MODEL: TTSModel = "sada-1"
DEFAULT_LANGUAGE: STTLanguage = "ar-SA"

TTS_SAMPLE_RATE = 24000
"""What ``format=pcm`` streams: 16-bit little-endian mono at 24 kHz."""

STT_SAMPLE_RATE = 16000
"""What the transcription socket is opened at. Input is resampled to this."""
