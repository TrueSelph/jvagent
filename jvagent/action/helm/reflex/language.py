"""Lightweight language detection for ReflexHelm.

Reflex on ``gpt-4o-mini`` occasionally translates short English greetings
to other languages (live-smoke observation: ``Hi`` → ``Hola``,
``Thanks!`` → ``¡De nada!``) despite explicit prompt instructions to
match the user's language. The model treats single-word greetings as
language-ambiguous and picks arbitrarily.

This module provides a deterministic fallback for the bounded case
where the failure happens: short utterances that match a known
greeting / thanks / confirmation in a small lexicon. We detect the
language in code and pass it to Reflex's user prompt as a hard
constraint — far more reliable than relying on the classifier to
follow a "match user's language" instruction.

For longer utterances we return ``None`` and let the model infer from
linguistic features (where it performs well).

This is pattern-agnostic enough that other helms or routers could
consume it; lives under ``helm/reflex/`` for now because Reflex is the
only known consumer. Move up to ``helm/`` or a shared utility module
when a second consumer arrives.
"""

from __future__ import annotations

import re
from typing import Dict, Optional, Set

# Known short greetings / thanks / confirmations per language.
# Lowercased; matched against the utterance after stripping punctuation
# and whitespace. Keep this list SMALL — it exists to fix the failure
# mode where the model is ambiguous, not to replace real language
# detection. Longer / phrased utterances are handled by the model.
_LANGUAGE_LEXICON: Dict[str, Set[str]] = {
    "English": {
        # greetings
        "hi",
        "hey",
        "hello",
        "yo",
        "sup",
        "howdy",
        "good morning",
        "good afternoon",
        "good evening",
        # thanks
        "thanks",
        "thank you",
        "thx",
        "ty",
        "cheers",
        "much appreciated",
        # farewells
        "bye",
        "goodbye",
        "see you",
        "cya",
        "later",
        # confirmations / acknowledgements
        "ok",
        "okay",
        "alright",
        "yes",
        "yep",
        "yeah",
        "yup",
        "no",
        "nope",
        "nah",
        "sure",
        "got it",
        "great",
        "cool",
        "nice",
        "awesome",
        "right",
    },
    "Spanish": {
        # greetings
        "hola",
        "qué tal",
        "buenas",
        "buenos días",
        "buenas tardes",
        "buenas noches",
        # thanks
        "gracias",
        "muchas gracias",
        "mil gracias",
        # farewells
        "adiós",
        "hasta luego",
        "chao",
        "hasta pronto",
        # confirmations
        "sí",
        "vale",
        "claro",
        "de acuerdo",
    },
    "French": {
        "bonjour",
        "salut",
        "coucou",
        "bonsoir",
        "merci",
        "merci beaucoup",
        "mille mercis",
        "au revoir",
        "à bientôt",
        "à plus",
        "bye",
        "oui",
        "d'accord",
        "ouais",
        "bien sûr",
    },
    "German": {
        "hallo",
        "servus",
        "moin",
        "guten tag",
        "guten morgen",
        "danke",
        "vielen dank",
        "danke schön",
        "tschüss",
        "auf wiedersehen",
        # "ciao" is an Italian loan in casual German; let the Italian
        # entry win to keep the lexicon's primary-language semantics clean.
        "ja",
        "klar",
        "genau",
    },
    "Italian": {
        "ciao",
        "salve",
        "buongiorno",
        "buonasera",
        "grazie",
        "grazie mille",
        "ti ringrazio",
        "arrivederci",
        "addio",
        "a presto",
        "sì",
        "certo",
        "va bene",
    },
    "Portuguese": {
        "oi",
        "olá",
        "bom dia",
        "boa tarde",
        "boa noite",
        "obrigado",
        "obrigada",
        "muito obrigado",
        "tchau",
        "até logo",
        "até mais",
        "sim",
        "claro",
        "tá bom",
    },
    "Japanese": {
        "こんにちは",
        "やあ",
        "おはよう",
        "おはようございます",
        "こんばんは",
        "ありがとう",
        "ありがとうございます",
        "どうも",
        "さようなら",
        "またね",
        "じゃあね",
        "はい",
        "いいえ",
        "うん",
    },
    "Chinese": {
        "你好",
        "嗨",
        "您好",
        "谢谢",
        "多谢",
        "感谢",
        "再见",
        "拜拜",
        "是",
        "好",
        "对",
        "嗯",
    },
}

# Reverse-index lookup. Built once at import; reads are O(1).
# Multi-language phrases (e.g. "ciao" is both Italian and a German
# casual goodbye) resolve to the FIRST language in declaration order.
_PHRASE_TO_LANGUAGE: Dict[str, str] = {}
for _lang, _phrases in _LANGUAGE_LEXICON.items():
    for _p in _phrases:
        _PHRASE_TO_LANGUAGE.setdefault(_p.lower(), _lang)

# Strip leading and trailing punctuation. Avoids "Hi!" missing "hi".
_LEADING_PUNCT_RE = re.compile(r"^[\s!?,.;:¡¿\-—'\"]+")
_TRAILING_PUNCT_RE = re.compile(r"[\s!?,.;:¡¿\-—'\"]+$")

# Utterances longer than this fall through to model inference. The
# lexicon is designed for one-to-three-word greetings; beyond that the
# model handles language detection reliably on its own.
_MAX_SHORT_UTTERANCE_CHARS = 30


def detect_short_utterance_language(utterance: str) -> Optional[str]:
    """Return a language name when ``utterance`` is a known short phrase.

    Returns ``None`` for utterances that are too long for the lexicon
    or don't match any entry. Callers should treat ``None`` as
    "let the model decide based on linguistic features."

    The returned language name is a human-readable label (``"English"``,
    ``"Spanish"``, ``"French"``, etc.) — it's intended to splice directly
    into a prompt slot, not to encode an ISO-639 code.
    """
    if not utterance:
        return None
    normalized = utterance.strip()
    if len(normalized) > _MAX_SHORT_UTTERANCE_CHARS:
        return None
    normalized = _LEADING_PUNCT_RE.sub("", normalized)
    normalized = _TRAILING_PUNCT_RE.sub("", normalized)
    normalized = normalized.strip().lower()
    if not normalized:
        return None
    return _PHRASE_TO_LANGUAGE.get(normalized)


def language_hint_line(language: Optional[str]) -> str:
    """Format the language hint for splicing into Reflex's user prompt.

    Returns ``""`` (empty string) when no language was detected — the
    prompt template can include the slot unconditionally without
    producing a stray blank line.
    """
    if not language:
        return ""
    return f"USER LANGUAGE (detected from greeting lexicon): {language}. Reply in {language}.\n"


__all__ = [
    "detect_short_utterance_language",
    "language_hint_line",
]
