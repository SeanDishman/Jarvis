import re

from desktop.app_identity import program_score, significant
from desktop.windows import normalized


def named_apps(apps, text):
    words = normalized(text).split()
    phrase = ' ' + ' '.join(words) + ' '
    matches = []
    for app in apps:
        name = normalized(app['Name'])
        if len(name) > 3 and (' ' + name + ' ' in phrase or name.replace(' ', '') in words):
            matches.append((len(name), app))
    if not matches:
        return []
    longest = max(score for score, _ in matches)
    return [app for score, app in matches if score == longest]


def window_score(app, window):
    declared = program_score(app['Name'], window.get('path'))
    if declared > .5:
        return 300 + round(declared * 100)
    name, title = normalized(app['Name']), normalized(window['title'])
    if len(name) > 2 and (title == name or title.endswith(' ' + name)):
        return 200 + len(name)
    stem = window['process'].casefold().removesuffix('.exe')
    return 100 + len(stem) if len(stem) >= 4 and stem in significant(app['Name']) else 0


def app_windows(app, windows):
    return [key for key, row in windows.items() if window_score(app, row)]


def window_app(apps, window):
    score, app = max(((window_score(app, window), app) for app in apps), key=lambda row: row[0], default=(0, None))
    return app['Name'] if score else None
