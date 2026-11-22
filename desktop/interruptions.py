import re
from difflib import SequenceMatcher

NAME = re.compile(r'\b(?:jarvis|javis|jar viss|jar ves)\b', re.I)


def words(text):
    return re.sub(r'[^a-z0-9 ]', '', text.lower().replace("'", '')).strip()


def sounds_like_echo(text, spoken):
    candidate, reference = words(text), words(spoken)
    if not candidate or not reference:
        return False
    if ' ' + candidate + ' ' in ' ' + reference + ' ':
        return True
    tokens, ref = candidate.split(), reference.split()
    if len(tokens) < 3:
        return False
    size = len(tokens)
    return any(SequenceMatcher(None, candidate, ' '.join(ref[i:i + size])).ratio() > .84
               for i in range(max(1, len(ref) - size + 1)))
