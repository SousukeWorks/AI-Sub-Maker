"""Pure ASS style serialization: RGB input, ASS AABBGGRR output."""
import hashlib
import re
from member_colors import rgb, validate_palette


def ass_color(value):
    value = rgb(value)[1:]
    return '&H00' + value[4:6] + value[2:4] + value[0:2]


def member_style_name(language, member_id, name):
    label = re.sub(r'[,\r\n{}\\]', '', name)[:30]
    digest = hashlib.sha256(member_id.encode('utf-8')).hexdigest()[:8]
    return f'{language}_{label}_{digest}'


def extra_styles(palette, font, size):
    validate_palette(palette)
    rows, mapping, ids = [], {}, {}
    if not palette or not palette['enabled']:
        return rows, mapping, ids
    schemes = [(key, value['name'], value) for key, value in palette['members'].items() if value['enabled']]
    schemes.append(('__chorus__', 'chorus', palette['chorus']))
    for key, name, colors in schemes:
        for language in ('japanese', 'chinese'):
            style = f'{language}_chorus' if key == '__chorus__' else member_style_name(language, key, name)
            ids[key, language] = style
            mapping[style] = key
            family = font if language == 'japanese' else 'Microsoft YaHei'
            height = size if language == 'japanese' else max(12, round(size * .85))
            fill, outline, shadow = (ass_color(colors[c]) for c in ('font', 'outline', 'shadow'))
            rows.append(f'Style: {style},{family},{height},{fill},{fill},{outline},{shadow},0,0,0,0,100,100,0,0,1,2.4,2,2,80,80,54,1')
    return rows, mapping, ids
