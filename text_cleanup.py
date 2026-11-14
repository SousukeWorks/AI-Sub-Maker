"""Caption text cleanup, shared by recognition, requests and every ASS export."""
import unicodedata
from dataclasses import replace


def clean_start(text):
    text = text.strip()
    while text and (text[0].isspace() or unicodedata.category(text[0]).startswith('P')):
        text = text[1:]
    return text.strip()


def has_words(text):
    return any(unicodedata.category(c)[0] in 'LN' for c in text)


def clean_cues(cues):
    return [replace(cue, text=clean_start(cue.text), translation=clean_start(cue.translation))
            for cue in cues if has_words(clean_start(cue.text))]
