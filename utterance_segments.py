"""Recover missing punctuation at native phrase, grammar and pause boundaries."""
import re

from japanese_sentences import body, complete, dependency, open_quote, words

_POLITE_END = re.compile(r'(?:です|ます|でした|ました|ません|ませんでした)(?:か|ね|よ|よね|な)?$')
_FORMULA_END = re.compile(r'(?:ありがとう(?:ございました|ございます)?|おめでとう(?:ございます)?|大好き|最高|お疲れ(?:さま|様)(?:でした|です)?)$')
_FILLERS = {'はい', 'うん', 'ええ', 'え', 'あ', 'あの', 'その', 'えっと', 'えー', 'いや', 'まあ', 'じゃあ', 'では'}
_RESTART = re.compile(r'^(?:はい|うん|ええ|いや|じゃあ|では|それでは|さて)(?=\s|[、，。！？!?]|$)')
_ENGLISH_REFRAIN = re.compile(r"([A-Za-z][A-Za-z'-]*)\s+\1(?:\s+\1)*", re.I)
_AUXILIARIES = {'なる', 'する', 'ある', 'いる', 'いく', 'くる', '来る', 'できる',
                'おく', 'しまう', 'みる', 'ござる', 'いたす', 'くださる', 'くれる', 'もらう'}
_DEPENDENT_HEADS = {'いっぱい', 'ため', 'よう', 'ほど', 'ぐらい', 'くらい', 'こと',
                    'ところ', 'もの', 'はず', 'つもり', '予定'}


def significant(text):
    return [w for w in words(text) if w.pos[0] != '記号']


def syntax_edges(rows):
    """Analyze the entire phrase, so a native character token isn't a word."""
    text = ''.join(r['text'] for r in rows)
    edges, offset = {}, len(text) - len(text.lstrip())
    explicit_clauses = any(c in text for c in '、，,；;：:')
    previous = None
    for word in words(text):
        offset += len(word.surface)
        if word.pos[0] == '記号':
            continue
        if word.pos[0] == '助詞':
            if '接続助詞' in word.pos[1]:
                edges[offset] = 'connective'
            elif word.surface in {'が', 'は', 'と'} and previous and previous.surface == 'の' and previous.pos[1] == '非自立':
                edges[offset] = 'nominal_clause'
            elif not explicit_clauses and word.surface in {'は', 'が', 'を', 'に', 'も', 'で'}:
                edges[offset] = 'case_phrase'
        elif word.pos[0] in {'動詞', '形容詞', '助動詞'} and word.form == '基本形':
            edges[offset] = 'relative_clause'
        previous = word
    return edges


def new_subject(text):
    tokens = significant(text)
    return bool(len(tokens) >= 2 and tokens[0].pos[1] == '代名詞'
                and tokens[1].surface in {'は', 'が', 'も'})


def phrase_reason(left, right, edge):
    before, after = significant(body(left)), significant(right)
    if not before or not after:
        return ''
    following = after[0]
    if following.pos[0] in {'助詞', '助動詞'} or following.pos[1] in {'非自立', '接尾'}:
        return ''
    if following.surface in _DEPENDENT_HEADS:
        return ''
    if following.base in {'ない', '無い'}:
        return ''
    # Keep lexical verbs with their inflections and auxiliary verb chains.
    if following.pos[0] == '動詞' and following.base in _AUXILIARIES:
        return ''
    if edge == 'connective':
        last = before[-1].surface
        if last in {'て', 'で'}:
            if following.surface == 'ありがとう':
                return ''
            predicates = [w for w in before[:-1] if w.pos[0] in {'動詞', '形容詞'}]
            if predicates and predicates[-1].base in {'くださる', 'くれる', 'もらう', '思う', '考える', '願う'}:
                return '并列动作或意图从句'
            if following.pos[0] == '接続詞' or new_subject(right):
                return '从句后的转折或新主语'
        elif last in {'けど', 'けれど', 'けれども', 'ので', 'から', 'が', 'し', 'たら', 'なら', 'のに', 'ながら', 'たり'}:
            return '句内连接词边界'
    if edge == 'nominal_clause' and any(w.pos[0] in {'動詞', '形容詞', '助動詞'} for w in before[:-2]):
        return '名词化从句边界'
    if edge == 'relative_clause' and following.pos[0] == '名詞' and following.pos[1] not in {'非自立', '代名詞', '数', '接尾'}:
        arguments = sum(w.pos[0] == '助詞' and w.surface in {'は', 'が', 'を', 'に', 'へ', 'も', 'で'} for w in before)
        if arguments >= 2:
            return '复合修饰从句'
    if edge == 'case_phrase':
        if '」' in left or '』' in left:
            suffix = re.split('[」』]', left)[-1]
            before = significant(suffix)
            if len(before) < 2:
                return ''
        case_count = sum(w.pos[0] == '助詞' and w.surface in {'は', 'が', 'を', 'に', 'へ', 'も', 'で'} for w in before)
        head = next((w for w in reversed(before[:-1]) if w.pos[0] != '助詞'), None)
        if not head or head.pos[0] != '名詞' or head.pos[1] in {'代名詞', '形容動詞語幹', '数', '接尾'}:
            return ''
        if before[-1].surface in {'は', 'が'}:
            has_predicate = any(w.pos[0] in {'動詞', '助動詞'} for w in before[:-2])
            return '复合主语语段' if case_count >= 3 and has_predicate else ''
        modified = any(w.pos[0] == '動詞' and w.form == '基本形' or
                       w.pos[0] == '助動詞' and (w.surface == 'な' or w.base == 'た') for w in before[:-2])
        nominal_predicates = sum(w.pos[0] == '形容詞' and i + 1 < len(before) and
                                 before[i+1].surface in {'こと', 'もの'} for i, w in enumerate(before))
        nouns = sum(w.pos[0] == '名詞' and w.pos[1] not in {'非自立', '代名詞', '数', '接尾'} for w in before)
        descriptive = any(w.pos[0] == '形容詞' for w in before[:-2]) and nouns >= 2
        if modified or descriptive or case_count >= 3 or nominal_predicates >= 2:
            return '复合宾语或状语语段'
    return ''


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


def boundary_reason(left, right, *, separated, gap, edge=''):
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
    if separated and _ENGLISH_REFRAIN.fullmatch(body(left)):
        if not re.match(r'[A-Za-z]', following):
            return '英文重复唱词结束'
    if separated and _ENGLISH_REFRAIN.match(following) and complete(left):
        return '英文重复唱词开始'
    if edge:
        reason = phrase_reason(left, semantic_right, edge)
        if reason:
            return reason
    # Respect explicit ASR spaces. Joining completed ました + a name before
    # tokenization can otherwise manufacture a nonexistent compound word.
    if dependency(left, semantic_right, separated=separated) and not (complete(left) and new_subject(semantic_right)):
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
    edges = syntax_edges(rows)
    offset = 0
    for index, row in enumerate(rows):
        if not row['text'].strip():
            current.append(row)
            offset += len(row['text'])
            continue
        if previous_word is not None and current:
            left = ''.join(part['text'] for part in current)
            separated = bool(left and left[-1].isspace() or row['text'][:1].isspace())
            gap = row['start'] - previous_word['end']
            edge = edges.get(offset - (len(left) - len(left.rstrip())), '')
            if separated or gap > .45 or strong_end(left) or edge:
                preview = ''
                for following in rows[index:]:
                    preview += following['text']
                    if len(preview) >= 64:
                        break
                if preview.strip():
                    reason = boundary_reason(left, preview, separated=separated, gap=gap, edge=edge)
                    if reason:
                        result.append(current)
                        reasons.append({'before_token': row['index'], 'reason': reason})
                        current = []
        current.append(row)
        previous_word = row
        offset += len(row['text'])
    if current:
        result.append(current)
    return result or [rows], reasons
