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


def build_instructions(profile: LanguageProfile, user_name: str | None = None) -> str:
    instructions = f"{BASE_INSTRUCTIONS}\nLanguage: {profile.reply_style}\n"
    if user_name:
        instructions += f"\nYou are talking with {user_name}. Use their name naturally.\n"
    return instructions


class Assistant(Agent):
    def __init__(self, profile: LanguageProfile, user_name: str | None = None) -> None:
        super().__init__(instructions=build_instructions(profile, user_name))
