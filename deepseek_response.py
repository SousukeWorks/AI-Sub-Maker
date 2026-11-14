"""Recover only complete JSON objects; never guess missing subtitle text."""
import json
import re


class ResponseText(str):
    def __new__(cls, content, metadata=None):
        value = super().__new__(cls, content)
        value.metadata = metadata or {}
        return value


def decode_rows(content):
    text = str(content).strip().lstrip('\ufeff')
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.I)
        text = re.sub(r'\s*```$', '', text)
    try:
        value = json.loads(text)
        if not isinstance(value, dict) or not isinstance(value.get('subtitles'), list):
            raise ValueError('API 返回的 JSON 没有 subtitles 列表')
        return value['subtitles'], ''
    except json.JSONDecodeError as exc:
        issue = f'响应 JSON 格式损坏（第 {exc.pos + 1} 字符：{exc.msg}）'
    # Recovery is limited to the expected top-level array. Braces and escaped
    # quotes inside strings are never interpreted as object delimiters.
    match = re.match(r'^\s*\{\s*"subtitles"\s*:\s*\[', text)
    if not match:
        raise ValueError(issue + '；没有可安全恢复的字幕列表')
    rows, depth, quoted, escaped, start = [], 0, False, False, None
    for pos in range(match.end(), len(text)):
        char = text[pos]
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char in '{[':
            if depth == 0:
                if char != '{':
                    break
                start = pos
            depth += 1
        elif char in '}]':
            if depth == 0:
                break
            depth -= 1
            if depth == 0:
                try:
                    row = json.loads(text[start:pos + 1])
                    if isinstance(row, dict):
                        rows.append(row)
                except json.JSONDecodeError:
                    pass
        elif depth == 0 and not (char.isspace() or char == ','):
            break
    return rows, issue + f'；已恢复 {len(rows)} 个完整对象，损坏或缺失条目保留待重试'
