"""Recover missing punctuation at native phrase, grammar and pause boundaries."""
import re

from japanese_sentences import body, complete, dependency, open_quote, words

_POLITE_END = re.compile(r'(?:です|ます|でした|ました|ません|ませんでした)(?:か|ね|よ|よね|な)?$')
_FORMULA_END = re.compile(r'(?:ありがとう(?:ございました|ございます)?|おめでとう(?:ございます)?|大好き|最高|お疲れ(?:さま|様)(?:でした|です)?)$')
_FILLERS = {'はい', 'うん', 'ええ', 'え', 'あ', 'あの', 'その', 'えっと', 'えー', 'いや', 'まあ', 'じゃあ', 'では'}
_RESTART = re.compile(r'^(?:はい|うん|ええ|いや|じゃあ|では|それでは|さて)(?=\s|[、，。！？!?]|$)')


def compact(text):
    return re.sub(r'\s+', '', body(text))


def strong_end(text):
    value = compact(text)
    return bool(_POLITE_END.search(value) or _FORMULA_END.search(value))


def clause_end(left, right):
    before = [word for word in words(body(left)) if word.pos[0] != '記号']
    if not before or before[-1].pos[0] != '助詞' or '接続助詞' not in before[-1].pos[1]:
        return False
    if not any(word.pos[0] in {'動詞', '形容詞', '助動詞'} for word in before[:-1]):
        return False
    if before[-1].surface in {'けど', 'けれど', 'けれども', 'ので', 'から', 'が', 'し', 'たら', 'なら'}:
        return True
    # A new subject after て is a display clause; an adverbial verb chain such
    # as 落ち着いて話した or 見上げて嬉しくなる remains connected.
    if before[-1].surface in {'て', 'で'}:
        after = [word for word in words(right) if word.pos[0] != '記号']
        for word in after:
            if word.pos[0] in {'動詞', '形容詞'}:
                return False
            if word.pos[0] == '助詞' and word.surface in {'は', 'が', 'も'}:
                return True
    return False


def nominal_reply(text):
    """A standalone age/count can be spoken without a copula, unlike a stem."""
    tokens = [w for w in words(body(text)) if w.pos[0] != '記号']
    return bool(tokens and tokens[-1].pos[:3] == ('名詞', '接尾', '助数詞')
                and all(w.pos[0] == '名詞' for w in tokens))


def boundary_reason(left, right, *, separated, gap):
    if not left.strip() or not right.strip() or open_quote(left):
        return ''
    value = compact(left)
    # A quick はい introducing the next sentence is not a separate subtitle.
    if value in _FILLERS and gap <= .45:
        return ''
    following = right.lstrip()
    if following[0] in '。、，,；;：:！？!?…」』）)]”\"\'':
        return ''
    discourse_de = separated and following.startswith('で ')
    semantic_right = following[2:].lstrip() if discourse_de else right
    # Respect explicit ASR spaces. Joining completed ました + a name before
    # tokenization can otherwise manufacture a nonexistent compound word.
    if dependency(left, semantic_right, separated=separated):
        return ''
    if _RESTART.match(following) and (separated or gap > .45):
        return '独立应答或转入下一句'
    if complete(left):
        return '完整句末与原生语段边界'
    if gap > .45 and nominal_reply(left):
        return '数量名词应答后的实际停顿'
    if separated and clause_end(left, semantic_right):
        return '连接词后的独立从句'
    return ''


def split_rows(rows):
    """Return only boundaries between existing tokens; no duration/length cap."""
    result, current, reasons = [], [], []
    previous_word = None
    for index, row in enumerate(rows):
        if not row['text'].strip():
            current.append(row)
            continue
        if previous_word is not None and current:
            left = ''.join(part['text'] for part in current)
            separated = bool(left and left[-1].isspace() or row['text'][:1].isspace())
            gap = row['start'] - previous_word['end']
            if separated or gap > .45 or strong_end(left):
                preview = ''
                for following in rows[index:]:
                    preview += following['text']
                    if len(preview) >= 64:
                        break
                if preview.strip():
                    reason = boundary_reason(left, preview, separated=separated, gap=gap)
                    if reason:
                        result.append(current)
                        reasons.append({'before_token': row['index'], 'reason': reason})
                        current = []
        current.append(row)
        previous_word = row
    if current:
        result.append(current)
    return result or [rows], reasons
