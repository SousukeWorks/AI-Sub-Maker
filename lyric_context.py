"""The selected stage's complete lyrics, in the user's saved song order."""
import hashlib
import json


def stage_reference(settings):
    from lyrics import selected_stage, chinese_lines
    stage = selected_stage(settings)
    if stage is None:
        return None
    return {'stage_id': stage['id'], 'stage': stage['name'], 'songs': [
        {'song_id': song['id'], 'title': song['title'],
         'japanese': song.get('japanese', '').replace('\r\n', '\n').replace('\r', '\n'),
         **({'chinese_by_line': chinese_lines(song)} if song.get('chinese', '').strip() else {})}
        for song in stage['songs']]}


def describe_reference(reference):
    songs = reference['songs'] if reference else []
    return {'stage': reference['stage'] if reference else '',
            'songs_available': len(songs), 'songs_with_lyrics': sum(bool(s['japanese'].strip()) for s in songs),
            'lines': sum(sum(bool(line.strip()) for line in s['japanese'].splitlines()) for s in songs),
            'characters': sum(len(s['japanese']) for s in songs),
            'sha256': hashlib.sha256(json.dumps(reference, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest() if reference else ''}


def ordered_text(reference):
    if not reference:
        return ''
    return '\n\n'.join(f'[{index:02d} {song["title"]}]\n{song["japanese"]}'
                       for index, song in enumerate(reference['songs'], reference.get('first_song_number', 1)) if song['japanese'].strip())
