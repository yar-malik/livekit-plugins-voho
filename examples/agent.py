"""A Saudi customer-service agent on LiveKit, speaking through Voho.

    export VOHO_API_KEY=voho_sk_live_...
    export OPENAI_API_KEY=...            # or any LiveKit LLM plugin
    export LIVEKIT_URL=... LIVEKIT_API_KEY=... LIVEKIT_API_SECRET=...
    pip install "livekit-agents[openai,silero]" livekit-plugins-voho
    python examples/agent.py console      # talk to it from the terminal
    python examples/agent.py dev          # join a LiveKit room

What it demonstrates, because these are the things a buyer asks to hear:
Najdi rather than textbook Arabic; the caller switching to English and back;
being interrupted and stopping; and a tool call mid-sentence that the caller
hears the result of. Point a SIP trunk at the room and it answers a phone —
see docs.voho.ai/livekit for the trunk and dispatch rule.
"""

import logging

from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli, function_tool
from livekit.plugins import openai, silero, voho

logger = logging.getLogger("voho-example")

INSTRUCTIONS = """You answer the customer line for Sanam Stores, a Riyadh retailer.

You speak Saudi Arabic in the Najdi dialect: الحين for now, إي for yes, وش for what, زين for good, أبشر when agreeing to do something, يعطيك العافية to close. If the caller switches to English, switch with them and switch back when they do.

Short sentences, two at a time. One question per turn. Read numbers back and get a yes before using them. If the caller interrupts, stop and listen. If you do not know something, say so and offer a person. Never invent an order, a price or a date.

To check an order, use the tool. Never guess an order's status."""


class SanamAgent(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=INSTRUCTIONS)

    @function_tool
    async def get_order_status(self, order_number: str) -> str:
        """Look up an order by its number and return its status.

        Args:
            order_number: The order number the caller read out, digits only.
        """
        # A real line reaches the order system here. The demo answers from a
        # table so the call has something true to say.
        orders = {
            "4471": "shipped yesterday, arriving tomorrow before 6pm with SMSA",
            "2209": "being packed, ships today",
        }
        return orders.get(order_number.strip(), "no order found with that number")


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()

    session = AgentSession(
        stt=voho.STT(language="ar-SA"),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=voho.TTS(voice="layla"),
        vad=silero.VAD.load(),
        # Barge-in: the caller talking over the agent stops it mid-sentence.
        allow_interruptions=True,
        min_endpointing_delay=0.4,
    )

    await session.start(agent=SanamAgent(), room=ctx.room)
    await session.say("أهلاً وسهلاً في متاجر سنام، معك ليلى. كيف أقدر أساعدك؟", allow_interruptions=True)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
