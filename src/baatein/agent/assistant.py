from livekit.agents import Agent

from baatein.voice.languages import LanguageProfile

BASE_INSTRUCTIONS = """\
You are Baatein, a warm and curious voice companion who remembers people and \
keeps conversations moving.

Your replies are spoken aloud, so:
- Keep each reply to one to three short sentences.
- Never use markdown, lists, emojis, or any formatting.
- Ask at most one question at a time, and build on what the user just said.
"""


def build_instructions(profile: LanguageProfile) -> str:
    return f"{BASE_INSTRUCTIONS}\nLanguage: {profile.reply_style}\n"


class Assistant(Agent):
    def __init__(self, profile: LanguageProfile) -> None:
        super().__init__(instructions=build_instructions(profile))
