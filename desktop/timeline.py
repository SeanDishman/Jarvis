"""How long ago things happened, in the words Jarvis would use. Shared so every prompt describes time the same way."""
from __future__ import annotations

import time


def ago(seconds):
    """A spoken phrase for a past moment, such as 'just now', '3 minutes ago' or 'yesterday'."""
    if seconds is None:
        return None
    seconds = max(0.0, float(seconds))
    if seconds < 10:
        return 'just now'
    if seconds < 60:
        return f'{int(seconds)} seconds ago'
    minutes = seconds / 60
    if minutes < 2:
        return 'a minute ago'
    if minutes < 60:
        return f'{int(minutes)} minutes ago'
    hours = minutes / 60
    if hours < 2:
        return 'an hour ago'
    if hours < 24:
        return f'{int(hours)} hours ago'
    days = hours / 24
    return 'yesterday' if days < 2 else f'{int(days)} days ago'


def local_time(epoch=None):
    """Weekday and clock time, such as 'Wednesday 2:41 PM'."""
    moment = time.localtime(epoch if epoch is not None else time.time())
    return time.strftime('%A ', moment) + time.strftime('%I:%M %p', moment).lstrip('0')
