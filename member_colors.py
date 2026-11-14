"""Editable subtitle colors and conservative, turn-local member attribution."""
from copy import deepcopy
import re
from member_color_catalog import OFFICIAL_COLORS

RGB_PALETTE = {'青': '#246BFF', '白': '#FFFFFF', '赤': '#FF3030', '黄': '#FFFF00',
 'ピンク': '#FF69B4', '緑': '#20C45A', '水色': '#50D5FF', '紫': '#A050E0',
 '濃ピンク': '#FF1493', '薄ピンク': '#FFB6D9', 'オレンジ': '#FF8C00', '黄緑': '#ADFF2F'}
DEFAULT_STYLE = {'enabled': False, 'font': '#FFFFFF', 'outline': '#181818', 'shadow': '#000000'}
CHORUS_STYLE = {'enabled': True, 'font': '#FF69B4', 'outline': '#FFFFFF', 'shadow': '#000000'}


def rgb(value):
    if isinstance(value, str):
        value = value.strip()
    if not isinstance(value, str) or not re.fullmatch(r'#[0-9A-Fa-f]{6}', value):
        raise ValueError('颜色请填写 #RRGGBB，或点击选色按钮')
    return value.upper()


def validate_style(style):
    if not isinstance(style, dict) or type(style.get('enabled')) is not bool:
        raise ValueError('字幕配色启用状态无效')
    for key in ('font', 'outline', 'shadow'):
        style[key] = rgb(style.get(key))
    return style


def official_style(member_id):
    entry = OFFICIAL_COLORS.get(member_id)
    if not entry:
        return dict(DEFAULT_STYLE)
    colors = [RGB_PALETTE[name] for name in entry['colors']]
    return {'enabled': True, 'font': colors[0], 'outline': colors[1] if len(colors) > 1 else '#181818',
            'shadow': colors[2] if len(colors) > 2 else '#000000'}


def ensure_style(member):
    entry = OFFICIAL_COLORS.get(member['id'])
    if entry and re.sub(r'\s+', '', member.get('japanese_name', '')) == entry['name']:
        member.setdefault('penlight_colors', list(entry['colors']))
        member.setdefault('color_source_url', entry['source_url'])
        member.setdefault('color_checked_at', entry['checked_at'])
        member.setdefault('subtitle_style', official_style(member['id']))
    else:
        member.setdefault('subtitle_style', dict(DEFAULT_STYLE))
    validate_style(member['subtitle_style'])
    if not isinstance(member.get('penlight_colors', []), list) or any(not isinstance(c, str) or len(c) > 40 for c in member.get('penlight_colors', [])):
        raise ValueError('应援色色名格式无效')
    for field in ('color_source_url', 'color_checked_at'):
        if not isinstance(member.get(field, ''), str) or len(member.get(field, '')) > 400:
            raise ValueError('应援色来源信息无效')
    return member


def export_palette(settings):
    if not getattr(settings, 'use_member_colors', True):
        return {'enabled': False, 'members': {}, 'chorus': dict(CHORUS_STYLE)}
    chorus = {'enabled': True, 'font': rgb(getattr(settings, 'chorus_font_color', CHORUS_STYLE['font'])),
              'outline': rgb(getattr(settings, 'chorus_outline_color', CHORUS_STYLE['outline'])),
              'shadow': rgb(getattr(settings, 'chorus_shadow_color', CHORUS_STYLE['shadow']))}
    members = {}
    if getattr(settings, 'use_member_glossary', True):
        from member_terms import load_library
        for member in load_library(getattr(settings, 'member_library', ''))['members']:
            ensure_style(member)
            if member['subtitle_style']['enabled']:
                members[member['id']] = {'name': member['japanese_name'], **deepcopy(member['subtitle_style'])}
    return {'enabled': True, 'members': members, 'chorus': chorus}


def validate_palette(palette):
    if palette is None:
        return
    if not isinstance(palette, dict) or type(palette.get('enabled')) is not bool or not isinstance(palette.get('members'), dict):
        raise ValueError('ASS 配色记录格式无效')
    if len(palette['members']) > 2000:
        raise ValueError('ASS 配色成员过多')
    validate_style(palette['chorus'])
    for key, member in palette['members'].items():
        if not isinstance(key, str) or not key or not isinstance(member, dict) or not isinstance(member.get('name'), str):
            raise ValueError('ASS 配色成员格式无效')
        validate_style(member)


def _self_introduction(text, members):
    if re.search(r'[「『“]|(?:こちら|次は|紹介|といえば|と言って|って言|好き|推し)', text):
        return ''
    found = set()
    for member in members:
        aliases = [member['japanese_name'], member.get('chinese_name', ''), member.get('reading_japanese', ''),
                   *member.get('nicknames_japanese', []), *member.get('aliases_japanese', [])]
        for name in aliases:
            match = re.search(re.escape(name) + r'(?:です|と申します|といいます)[。！!\s]*$', text) if len(name) >= 2 else None
            if match:
                prefix = text[:match.start()].strip(' 、，。！!')
                if (not prefix or re.search(r'(?:私は|僕は|わたしは|私の名前は|出身|歳|期生|研究生|こと)', prefix)
                        or prefix in {'こんにちは', 'こんばんは', '皆さん', 'みなさん'}):
                    found.add(member['id'])
    return next(iter(found)) if len(found) == 1 else ''


def annotate_identities(document, settings, reference=None):
    if not getattr(settings, 'use_member_colors', True):
        return 0
    if reference is None:
        from member_terms import member_reference
        reference = member_reference(settings)
    members = reference['members'] if reference else []
    changed, current, previous_speaker, previous_end = 0, '', '', None
    for row in document['cues']:
        speaker = row.get('speaker', '')
        if (speaker != previous_speaker or not speaker or row.get('lyric')
                or previous_end is not None and row['start'] - previous_end > 3):
            current = ''
        previous_speaker = speaker
        previous_end = row['end']
        if row.get('role_source') == 'manual':
            current = ''
            continue
        old = (row.get('member_id', ''), row.get('chorus', False), row.get('role_source', ''))
        row.pop('member_id', None)
        row.pop('chorus', None)
        row.pop('role_source', None)
        if speaker.casefold() in {'chorus', 'all', '合唱', '全員', '全员'} or re.match(r'^\s*[【\[(（]?(?:合唱|全員|全员|コーラス|CHORUS)[】\])）:：]', row['text']):
            row.update(chorus=True, role_source='explicit_chorus')
            current = ''
        elif not row.get('lyric'):
            member = _self_introduction(row['text'], members)
            if member:
                current = member if speaker else ''
                row.update(member_id=member, role_source='self_intro')
            elif current:
                row.update(member_id=current, role_source='continuous_turn')
        changed += old != (row.get('member_id', ''), row.get('chorus', False), row.get('role_source', ''))
    return changed
