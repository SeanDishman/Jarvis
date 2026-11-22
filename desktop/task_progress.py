from desktop.acknowledgements import FALLBACK_ACKNOWLEDGEMENT

# A task paused on a question for the owner stays on show until he answers it.
ACTIVE = {'queued', 'running', 'waiting'}
TOOL_NOTES = {
    'launch app': 'Opening the app', 'list apps': 'Finding the app',
    'start web search': 'Searching the web', 'web search status': 'Checking search results',
    'search files': 'Finding files', 'search document content': 'Searching documents',
    'read file': 'Reading the file', 'browser navigate': 'Opening the page',
    'browser read': 'Reading the page', 'browser click': 'Working in the browser',
    'browser fill': 'Filling in the page', 'browser scroll': 'Reading the page',
    'get network info': 'Checking the connection', 'start computer task': 'Working on your computer',
    'start powershell': 'Running the requested command', 'powershell status': 'Checking the command',
    'search email': 'Searching email', 'read email': 'Reading email',
    'create reminder': 'Setting the reminder', 'list reminders': 'Checking reminders',
}


def short_note(note, stage='working'):
    note = ' '.join(str(note or '').split())
    if stage == 'acknowledging':
        return 'Starting'
    if stage == 'queued':
        return 'Queued'
    if note.startswith('Running '):
        return TOOL_NOTES.get(note[8:].lower(), 'Working on your request')
    if not note or note == 'Thinking' or note.startswith(('Selected ', 'Choosing Luna', 'Waiting for a task worker')):
        return 'Working on your request'
    return note


def current_tasks(jobs, request=None, state='thinking', note=''):
    children = {job.get('childId') for job in jobs if job.get('childId')}
    rows = []
    if request and state != 'speaking':
        rows.append({'id': request['id'], 'title': request.get('ownerText') or 'Your request',
                     'detail': short_note(note)})
    for job in sorted(jobs, key=lambda row: row.get('status') != 'running'):
        if job.get('status') not in ACTIVE or job['id'] in children:
            continue
        label = job.get('acknowledgement')
        if not label or label == FALLBACK_ACKNOWLEDGEMENT:
            label = job.get('question') or 'Your request'
        rows.append({'id': job['id'], 'title': label,
                     'detail': short_note(job.get('note'), job.get('stage'))})
    return rows
