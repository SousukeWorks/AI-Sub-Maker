"""Readable punctuation-based display units, with native token boundaries."""
from japanese_sentences import body, reply, words

CLAUSE_MARKS = '、，,；;：:'
_FILLERS = {'はい', 'うん', 'ええ', 'そう', 'え', 'お', 'あ', 'あの', 'その', 'えっと',
            'えー', 'えーと', 'ええと', 'あのね', 'いや', 'なんか', 'まあ', 'うーん',
            'で', 'それで', 'そして', 'でも', 'だから', 'なので'}
_PRONOUNS = {'私', 'わたし', '僕', 'ぼく', '俺', '君', 'あなた', '彼', '彼女', 'みんな', '皆さん', 'みなさん'}
_AUX_VERBS = {'なる', 'する', 'ある', 'いる', 'いく', 'くる', '来る', 'できる', 'おく', 'しまう',
              'みる', 'ござる', 'いたす', 'くださる'}
_CLIPPED = {'る', 'す', 'う', 'た', 'だ', 'て', 'で', 'ん', 'った', 'って', 'んだ', 'んです'}


def weak_prefix(text):
    """Fillers, a hanging pronoun and bare noun lists go with what follows."""
    tokens = [w for w in words(body(text).rstrip(CLAUSE_MARKS)) if w.pos[0] != '記号']
    if not tokens or all(w.surface in _FILLERS or w.pos[0] in {'感動詞', 'フィラー'} for w in tokens):
        return True
    if tokens[-1].surface in _PRONOUNS:
        return True
    content = [w for w in tokens if w.pos[0] in {'名詞', '動詞', '形容詞'}
               and w.surface not in _FILLERS | _PRONOUNS and w.pos[1] not in {'非自立', '接尾', '代名詞'}]
    if not content:
        return True
    # A list such as ディナーとかランチ、カフェ... is one noun phrase.
    return tokens[-1].pos[0] == '名詞' and not any(w.pos[0] in {'動詞', '形容詞', '助動詞'} for w in tokens)


def tail_only(text):
    """A dependent ending, not an entire next clause beginning with a particle."""
    value = body(text).rstrip(CLAUSE_MARKS).lstrip(' 」』）)]”')
    if not value or reply(value) or value in _FILLERS:
        return False
    if value in _CLIPPED:
        return True
    tokens = [w for w in words(value) if w.pos[0] != '記号']
    if not tokens:
        return False
    first = tokens[0]
    if first.pos[0] not in {'助詞', '助動詞'} and first.pos[1] not in {'非自立', '接尾'} and not value.startswith('っ'):
        return False
    for token in tokens:
        if token.pos[0] in {'助詞', '助動詞'} or token.pos[1] in {'非自立', '接尾'}:
            continue
        if token.pos[0] == '動詞' and token.base in _AUX_VERBS:
            continue
        if token.surface in _CLIPPED:
            continue
        return False
    return True


def _text(rows):
    return ''.join(row['text'] for row in rows).strip()


def split_rows(rows):
    """Only cut between native tokens. Never invent a clock inside a token."""
    parts, current = [], []
    for index, row in enumerate(rows):
        current.append(row)
        value = row['text'].rstrip()
        if not value or value[-1] not in CLAUSE_MARKS:
            continue
        following = ''.join(r['text'] for r in rows[index + 1:]).lstrip()
        before = _text(current)[:-1].rstrip()
        # Thousands separators / clock notation / URLs are not clause marks.
        if before and following and (before[-1].isdigit() and following[0].isdigit()
                                     or value[-1] == ':' and following.startswith('//')):
            continue
        parts.append(current)
        current = []
    if current:
        parts.append(current)
    if len(parts) < 2:
        return [rows]

    result, pending = [], []
    for part in parts:
        # A comma can be misrecognized inside an inflection. Don't publish its
        # ending alone, nor carry that ending forward into the next sentence.
        if not pending and result and tail_only(_text(part)):
            result[-1].extend(part)
            continue
        pending.extend(part)
        if not weak_prefix(_text(pending)):
            result.append(pending)
            pending = []
    if pending:
        if result:
            result[-1].extend(pending)
        else:
            result.append(pending)
    return result or [rows]
