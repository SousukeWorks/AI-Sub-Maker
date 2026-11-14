"""Offline sentence boundaries using Japanese morphology, rather than a clock cap."""
from functools import lru_cache
import re
import threading
from typing import NamedTuple

_local = threading.local()
_REPLIES = {'はい', 'うん', 'ええ', 'そう', 'いいえ', 'いや', 'え', 'ああ', 'へえ',
            'ありがとう', 'ありがとうございます', 'そうですね', '了解', 'オッケー', '分かりました', 'わかりました'}
_PAIRS = {'「': '」', '『': '』', '“': '”', '（': '）', '(': ')'}


class Word(NamedTuple):
    surface: str
    pos: tuple
    form: str
    base: str


@lru_cache(maxsize=4096)
def words(text):
    if not hasattr(_local, 'tokenizer'):
        from janome.tokenizer import Tokenizer
        _local.tokenizer = Tokenizer()
    return tuple(Word(t.surface, tuple(t.part_of_speech.split(',')), t.infl_form, t.base_form)
                 for t in _local.tokenizer.tokenize(text))


def open_quote(text):
    stack = []
    for char in text:
        if char in _PAIRS:
            stack.append(_PAIRS[char])
        elif stack and char == stack[-1]:
            stack.pop()
    return bool(stack)


def body(text):
    return text.strip().rstrip('。、，．！？!?…」』）)]”"\' ')


def reply(text):
    return body(text) in _REPLIES


def dependency(left, right, *, separated=False):
    """Return why the next phrase depends on the previous phrase, if it does."""
    right = right.lstrip(' 」』）)]”')
    if not right or reply(right):
        return ''
    prefix = body(left)
    # A token straddling the boundary is direct evidence of a split word.
    offset, contextual = 0, None
    for word in words(prefix + (' ' if separated else '') + right):
        end = offset + len(word.surface)
        if offset < len(prefix) < end and word.pos[0] != '記号':
            return '同一个词被切开'
        if offset >= len(prefix) and word.pos[0] != '記号':
            contextual = word
            break
        offset = end
    following = contextual or next((w for w in words(right) if w.pos[0] != '記号'), None)
    if separated:
        from punctuation_segments import tail_only
        if tail_only(right.split()[0]):
            return '空格后的词尾依附前文'
    if following and (following.pos[0] in {'助詞', '助動詞'}
                      or following.pos[1] in {'非自立', '接尾'}):
        return '助词、助动词或非独立词依附前文'
    previous = next((w for w in reversed(words(prefix)) if w.pos[0] != '記号'), None)
    if (previous and following and (previous.pos[0] in {'形容詞', '動詞'}
            or previous.pos[0] == '助動詞' and previous.base in {'た', 'ない', 'れる', 'られる'})
            and previous.form == '基本形' and following.pos[0] == '名詞'
            and not re.search(r'(?:です|ます|でした|ました|ません)$', prefix)):
        return '修饰语尚未接到后面的名词'
    return ''


def complete(text):
    """A noun/stem/connecting particle alone is not proof that a sentence ended."""
    if open_quote(text) or text.rstrip().endswith(('、', '，', ',')):
        return False
    if reply(text):
        return True
    if re.search(r'(?:ありがとう(?:ございました|ございます)?|おめでとう(?:ございます)?|大好き|最高|お疲れ(?:さま|様)(?:でした|です)?)$',
                 re.sub(r'\s+', '', body(text))):
        return True
    tokens = [w for w in words(body(text)) if w.pos[0] != '記号']
    if not tokens:
        return False
    last = tokens[-1]
    return (last.pos[0] in {'動詞', '形容詞', '助動詞'} and (last.form == '基本形' or last.form.startswith('命令'))
            or last.pos[0] == '助詞' and '終助詞' in last.pos[1])


def pause_boundary(left, right):
    """Pause only separates independently completed utterances, never a stem."""
    if open_quote(left) or dependency(left, right):
        return False
    if reply(right):
        return True
    return complete(left)


def nickname_boundary(source, start, end):
    """Don't find えりちゃん inside the ordinary greeting おかえりちゃん."""
    offset = 0
    for word in words(source):
        stop = offset + len(word.surface)
        if offset < start < stop and word.pos[0] in {
                '感動詞', '動詞', '形容詞', '副詞'}:
            return False
        offset = stop
    return True
