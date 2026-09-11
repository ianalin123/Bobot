import random

import pytest

from bob import persona

TOOL_NAMES = ("offer_banana", "stop_motion", "look_at_speaker", "set_expression")
EXPRESSIONS = ("neutral", "curious", "happy", "love", "sleepy", "surprised", "sad", "angry_playful")
STOCK_KEYS = (
    "bello",
    "banana",
    "poopaye",
    "laugh",
    "yawn",
    "tank_yu",
    "bee_do",
    "para_tu",
    "whoa",
    "uh_oh",
    "song",
)


def test_exactly_100_unique_greetings():
    assert len(persona.GREETINGS) == 100
    assert len(set(persona.GREETINGS)) == 100
    assert all(isinstance(g, str) and g.strip() == g and g for g in persona.GREETINGS)


def test_greetings_short_and_placeholder_at_most_once():
    for greeting in persona.GREETINGS:
        assert len(greeting) <= 90, greeting
        assert greeting.count("{name}") <= 1, greeting
        assert "{" not in greeting.replace("{name}", ""), greeting


def test_most_greetings_mention_name():
    with_name = sum("{name}" in g for g in persona.GREETINGS)
    assert with_name >= 60


def test_pick_greeting_substitutes_name():
    rng = random.Random(0)
    for _ in range(200):
        text = persona.pick_greeting("Sissi", rng)
        assert "{name}" not in text and "{" not in text
        assert text in {g.format(name="Sissi") for g in persona.GREETINGS}
        if any(g.format(name="Sissi") == text and "{name}" in g for g in persona.GREETINGS):
            assert "Sissi" in text


def test_pick_greeting_defaults_to_random_module():
    text = persona.pick_greeting("Sissi")
    assert isinstance(text, str) and text


def test_pick_greeting_covers_all_templates():
    rng = random.Random(1)
    seen = {persona.pick_greeting("Sissi", rng) for _ in range(5000)}
    assert len(seen) == 100


def test_stock_phrases_keys_and_shape():
    assert tuple(persona.STOCK_PHRASES) == STOCK_KEYS
    for key, line in persona.STOCK_PHRASES.items():
        assert isinstance(line, str) and line.strip() == line and line, key
        assert len(line) <= (200 if key == "song" else 60), key
        assert "{" not in line, key


def test_character_budget_under_elevenlabs_limit():
    total = sum(len(g.format(name="Nameless")) for g in persona.GREETINGS)
    total += sum(len(v) for v in persona.STOCK_PHRASES.values())
    assert total < 6000, total


def test_minionese_lexicon():
    assert isinstance(persona.MINIONESE, dict)
    for phrase in ("bello", "poopaye", "tank yu", "bee do", "papoy", "tulaliloo ti amo", "para tu", "stopa"):
        assert phrase in persona.MINIONESE, phrase
    assert persona.MINIONESE["bello"] == "hello"
    assert persona.MINIONESE["poopaye"] == "goodbye"
    assert "tatata bala tu" in persona.MINIONESE
    # The insult is documented so the model knows to avoid it, but never spoken.
    assert not any("tatata" in g.lower() for g in persona.GREETINGS)
    assert not any("tatata" in v.lower() for v in persona.STOCK_PHRASES.values())


def test_system_prompt_mentions_character_tools_and_safety():
    prompt = persona.SYSTEM_PROMPT
    assert "Bob" in prompt and "Tim" in prompt and "banana" in prompt.lower()
    for tool in TOOL_NAMES:
        assert tool in prompt, tool
    for expression in EXPRESSIONS:
        assert expression in prompt, expression
    lowered = prompt.lower()
    assert "started" in lowered and "completed" in lowered
    assert "capability" in lowered
    assert "markdown" in lowered and "emoji" in lowered
    assert "one or two" in lowered
    assert "tatata bala tu" in lowered


def test_greetings_have_no_markdown_or_emoji():
    for greeting in persona.GREETINGS:
        assert all(ord(ch) < 0x2000 for ch in greeting), greeting
        assert "*" not in greeting and "_" not in greeting and "#" not in greeting, greeting


def test_song_is_original_and_cached_key():
    assert "song" in persona.STOCK_PHRASES
    assert persona.STOCK_PHRASES["song"] == persona.SONG_LYRICS
    assert "ba-na-na" in persona.SONG_LYRICS.lower()


def test_spoken_names_for_the_team():
    assert persona.spoken_name("Iana") == "Yee-ah-na"
    assert persona.spoken_name("Sissi") == "See-see"
    assert persona.spoken_name("Haseab") == "Ha-seeb"
    assert persona.spoken_name("Stranger") == "Stranger"


def test_minionese_levels_have_instructions_and_replies():
    assert set(persona.LEVELS) == {"mixed", "full", "english"}
    for level in persona.LEVELS:
        instruction = persona.minionese_instruction(level)
        assert instruction and "\n" not in instruction.strip("\n")[:1]
        reply = persona.LEVEL_REPLIES[level]
        assert reply and len(reply) <= 90 and "tatata" not in reply.lower()
    assert persona.minionese_instruction("full") != persona.minionese_instruction("mixed")
    with pytest.raises(ValueError):
        persona.minionese_instruction("klingon")


def test_lexicon_is_big_enough_to_speak_minionese():
    assert len(persona.MINIONESE) >= 40
    assert all(k == k.lower() and v for k, v in persona.MINIONESE.items())


def test_system_prompt_carries_the_minionese_guide():
    assert persona.MINIONESE_GUIDE in persona.SYSTEM_PROMPT
    guide = persona.MINIONESE_GUIDE.lower()
    # The guide teaches the words and shows what a fully Minionese line looks like.
    assert sum(word in guide for word in persona.MINIONESE) >= 20
    assert "example" in guide
    assert "never" in guide  # the insult is named only to forbid it


def test_default_level_is_understandable_english_with_minion_fillers():
    assert persona.DEFAULT_LEVEL == "mixed"
    mixed = persona.minionese_instruction("mixed").lower()
    assert "english" in mixed and "filler" in mixed and "understand" in mixed
    assert "hehehe" in mixed and "bello" in mixed
