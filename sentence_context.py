"""Conservative Japanese continuation hints; no model downloads or API calls."""
import re

BASE_PAUSE = .45
_CONNECTOR = re.compile(r"(?:ので|のに|から|なら|たら|けれど|けど|ですが|ですけど|だけど|ながら|たり|とか|について|として|という|って|でも|それで|そして|それから|は|が|を|に|の|へ|で|と|も|て|し)(?:ね|さ)?$")
_COMPLETE = re.compile(r"(?:です|ます|でした|ました|ません|だった|でしょう|だろう|だよ|だね|ですよ|ですね|だよね|ますよ|ますね|なの|わかった|分かった|わかりました|分かりました|ありがとう|ありがとうございます|はい|うん|そう|そうですね|オッケー|了解)$")


def continuation_kind(text):
    text = text.strip().rstrip('」』）)]\"\'')
    if not text or re.search(r"[。！？!?](?:[。！？!?\s]*)$", text):
        return "complete"
    text = text.rstrip("、，,：:—―… ")
    if _COMPLETE.search(text):
        return "complete"
    if _CONNECTOR.search(text):
        return "unfinished"
    return "uncertain"


def allowed_pause(text, maximum=1.2):
    if type(maximum) not in (int, float) or not .5 <= maximum <= 3:
        raise ValueError("续句停顿上限应为 0.5–3 秒")
    kind = continuation_kind(text)
    return maximum if kind == "unfinished" else min(maximum, .8) if kind == "uncertain" else BASE_PAUSE


def conversation_context(rows, selected_ids, *, maximum=1.2):
    """Include read-only neighbours before AND after every selected subtitle."""
    selected_ids = set(selected_ids)
    included = set()
    for index, row in enumerate(rows):
        if row["id"] in selected_ids:
            included.update(range(max(0, index - 2), min(len(rows), index + 3)))
    result = []
    for index in sorted(included):
        row = rows[index]
        item = {"id": row["id"], "start": row["start"], "end": row["end"],
                "speaker": row.get("speaker", ""), "read_only": row["id"] not in selected_ids}
        if item["read_only"]:
            item["text"] = row["text"]
            if row.get("translation") and row.get("translate", {}).get("status") == "success":
                item["chinese"] = row["translation"]
        if index:
            previous = rows[index - 1]
            gap = row["start"] - previous["end"]
            item["same_speaker_as_previous"] = bool(item["speaker"]) and item["speaker"] == previous.get("speaker")
            item["gap_from_previous"] = round(gap, 3)
            item["possible_continuation"] = (item["same_speaker_as_previous"] and 0 <= gap <= allowed_pause(previous["text"], maximum)
                                               and continuation_kind(previous["text"]) != "complete")
        result.append(item)
    return result
