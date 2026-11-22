import re
from desktop.common import local_request


def normalize_model(value):
    value = value.casefold().replace('point', '.')
    for word, digit in {'one':'1','two':'2','three':'3','four':'4','five':'5','six':'6','seven':'7'}.items():
        value = re.sub(r'\b' + word + r'\b', digit, value)
    return re.sub(r'[^a-z0-9]', '', value)


def switch_model(scope, query):
    data = local_request(18791, '/models', timeout=5)
    wanted = normalize_model(query)
    if scope == 'search' and wanted in ('same', 'sameasconversation', 'sameaschat', 'sameasconversationmodel'):
        selected, label = 'same', 'the same model as our conversation'
    else:
        rows = data['models']
        matches = [row for row in rows if wanted in (normalize_model(row['id']), normalize_model(row['name']))]
        if not matches:
            family = wanted.removeprefix('gpt')
            if family in ('luna', 'sol', 'astra', 'terra', 'daybreak'):
                matches = [row for row in rows if family in re.split(r'[^a-z0-9]+', row['id'].lower())]
        if not matches:
            return {'ok': False, 'text': 'That model is not in your signed-in list, sir. Open Model settings to see the available choices.'}
        selected, label = matches[0]['id'], matches[0]['name']
    settings = {key:data[key] for key in ('conversationModel','searchModel')}
    settings['searchModel' if scope == 'search' else 'conversationModel'] = selected
    local_request(18791, '/models', settings, timeout=5)
    role = 'background search' if scope == 'search' else 'conversation'
    return {'ok': True, 'text': f'Switched the {role} model to {label}, sir.', 'model': selected}
