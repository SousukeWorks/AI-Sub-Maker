"""Repair small Japanese inflection fragments without guessing audio clocks."""
import re
from dataclasses import replace

from japanese_sentences import open_quote, reply
from punctuation_segments import tail_only

_TRAILING = re.compile(r'[\s、。，．！？!?…「」『』（）()\[\]"\']+$')


def suffix_body(text):
    return _TRAILING.sub('', text.strip())


def can_attach(previous, current):
    """Keep complete sentences after lyric replacement as well as native parsing."""
    a, b = previous.get('lyric'), current.get('lyric')
    if a and b and (a.get('stage_id'), a.get('song_id')) != (b.get('stage_id'), b.get('song_id')):
        return False
    speaker = previous.get('speaker', '')
    if not speaker or speaker != current.get('speaker', ''):
        return False
    left, right = previous['text'].strip(), current['text'].strip()
    if not left or not right or reply(left) or reply(right):
        return False
    if re.search(r'[。！？!?][」』）)\s]*$', left) and not open_quote(left):
        return False
    if current['start'] < previous['end'] - .02:
        return False
    # A continuation can be understood across display units. Only a genuine
    # isolated ending belongs back on the preceding unit, not a whole clause.
    return tail_only(right)


def joined_reference(previous, current, text):
    """Keep canonical parts for inspection; translate the performed FULL sentence."""
    references = [r for r in (previous.lyric, current.lyric) if r]
    if not references:
        return None
    parts = []
    for reference in references:
        parts.extend(reference.get('canonical_parts') or [{key: reference.get(key, '') for key in
                     ('stage_id', 'song_id', 'song', 'line', 'japanese', 'chinese', 'score')}])
    return {**references[0], 'japanese': text, 'chinese': '', 'canonical_parts': parts,
            'sentence_joined': True}


def join_cues(cues, *, known_speakers=None):
    """Only pre-translation cues; translated jobs require explicit retranslation."""
    result, repairs, known = [], [], []
    for index, cue in enumerate(cues):
        trusted = known_speakers is None or known_speakers[index]
        if (result and trusted and known[-1] and not result[-1].translation and not cue.translation
                and can_attach(vars(result[-1]), vars(cue))):
            previous = result[-1]
            text = previous.text.rstrip() + cue.text.lstrip()
            result[-1] = replace(previous, end=max(previous.end, cue.end), text=text,
                                 lyric=joined_reference(previous, cue, text))
            repairs.append({'start': previous.start, 'end': result[-1].end,
                            'before': [previous.text, cue.text], 'after': result[-1].text})
        else:
            result.append(cue)
            known.append(trusted)
    return result, repairs


