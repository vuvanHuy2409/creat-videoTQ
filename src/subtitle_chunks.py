"""Short caption phrases with a strict size limit and unchanged speech bounds."""

import math
import re
import unicodedata

MAX_CAPTION_WORDS = 15
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uff66-\uff9f]")


def _units(text):
    text = unicodedata.normalize("NFC", text).strip()
    compact = bool(_CJK.search(text)) and not re.search(r"\s", text)
    return (list(text) if compact else text.split()), ("" if compact else " ")


def split_caption_text(text, max_words=MAX_CAPTION_WORDS):
    """Usually 10–15 words; allow shorter phrases at punctuation or sentence end.

    Unspaced CJK uses characters as display units. Text order and punctuation
    are retained; adjacent words are never repeated or dropped.
    """
    if not isinstance(max_words, int) or max_words < 1:
        raise ValueError("Giới hạn số từ phải là số nguyên dương.")
    units, separator = _units(text)
    chunks = []
    while len(units) > max_words:
        # Balance the tail, avoiding a final flash of just one or two words.
        target = round(len(units) / math.ceil(len(units) / max_words))
        candidates = [i for i in range(max(1, min(6, target)), max_words + 1)
                      if re.search(r"[.!?。！？;；][\"'”’）)]*$", units[i - 1]) and abs(i - target) <= 4]
        if not candidates:
            candidates = [i for i in range(max(1, min(8, target)), max_words + 1)
                          if re.search(r"[,，:：][\"'”’）)]*$", units[i - 1]) and abs(i - target) <= 3]
        cut = min(candidates, key=lambda i: (abs(i - target), i)) if candidates else target
        chunks.append(separator.join(units[:cut]))
        units = units[cut:]
    if units:
        chunks.append(separator.join(units))
    return chunks


def split_caption_interval(entry, speech_spans=None, max_words=MAX_CAPTION_WORDS):
    """Fit short phrases inside the measured sentence, snapping cuts to pauses.

    Without word timestamps, word-count weights estimate interior boundaries.
    A nearby real pause supplies a better cut and remains a caption-free gap.
    The first/last speech boundaries and overlaps with other sentences stay put.
    """
    texts = split_caption_text(entry["text"], max_words)
    start_ms, end_ms = round(entry["start"] * 1000), round(entry["end"] * 1000)
    if not texts or end_ms <= start_ms:
        return []
    if len(texts) == 1:
        return [{**entry, "text": texts[0]}]
    if end_ms - start_ms < len(texts):
        raise ValueError("Đoạn lời đọc quá ngắn để chia phụ đề thành các mốc mili giây riêng.")
    weights = [len(_units(text)[0]) for text in texts]
    total = sum(weights)
    spans = speech_spans or []
    gaps = [(round(left[1] * 1000), round(right[0] * 1000))
            for left, right in zip(spans, spans[1:]) if right[0] > left[1]]
    chunks, position, cumulative = [], start_ms, 0
    for index, (text, weight) in enumerate(zip(texts, weights)):
        cumulative += weight
        if index == len(texts) - 1:
            stop, following = end_ms, end_ms
        else:
            remaining = len(texts) - index - 1
            expected = round(start_ms + (end_ms - start_ms) * cumulative / total)
            stop = max(position + 1, min(end_ms - remaining, expected))
            following = stop
            nearby = [(a, b) for a, b in gaps
                      if position < a < b < end_ms - remaining + 1
                      and abs((a + b) / 2 - expected) <= min(400, (end_ms - start_ms) * .2)]
            if nearby:
                stop, following = min(nearby, key=lambda gap: abs(sum(gap) / 2 - expected))
        chunks.append({**entry, "start": position / 1000, "end": stop / 1000, "text": text})
        position = following
    return chunks


def caption_word_budgets(entries, max_words=MAX_CAPTION_WORDS):
    """Share the on-screen word budget when two or more voices overlap."""
    events = []
    for index, entry in enumerate(entries):
        if entry["end"] > entry["start"]:
            events.extend([(entry["start"], 1, index), (entry["end"], 0, index)])
    active, peaks = set(), [1] * len(entries)
    for _, starting, index in sorted(events):
        if starting:
            active.add(index)
            for current in active:
                peaks[current] = max(peaks[current], len(active))
        else:
            active.discard(index)
    return [max(1, max_words // peak) for peak in peaks]


def split_caption_entries(entries, max_words=MAX_CAPTION_WORDS):
    budgets = caption_word_budgets(entries, max_words)
    return [chunk for entry, budget in zip(entries, budgets)
            for chunk in split_caption_interval(entry, entry.get("speech_spans"), budget)]
