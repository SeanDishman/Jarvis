import json
from desktop.common import local_request
from desktop.research import response_text
from desktop.routing import capabilities_for, SYSTEM_ACTIONS


def classify_request(text, session, context=None):
    context = dict(context or {})
    context['recentTasks'] = list(context.get('recentTasks', []))
    context['waitingQuestions'] = list(context.get('waitingQuestions', []))
    payload = {'utterance': text, 'context': context}
    while context['recentTasks'] and len(json.dumps(payload, ensure_ascii=False)) > 5800:
        context['recentTasks'].pop()
    while context['waitingQuestions'] and len(json.dumps(payload, ensure_ascii=False)) > 5800:
        context['waitingQuestions'].pop()
    if context.get('lastTaskId') not in {row['id'] for row in context['recentTasks']}:
        context['lastTaskId'] = context['recentTasks'][0]['id'] if context['recentTasks'] else None
    response = local_request(18791, '/v1/responses', {
        'model': 'openclaw/intent', 'user': session + '-intent',
        'input': json.dumps(payload, ensure_ascii=False),
    }, timeout=25)
    result = json.loads(response_text(response))
    return validate_decision(result, context)


def validate_decision(result, context=None):
    if not isinstance(result, dict) or result.get('action') not in ('normal', 'break', 'screen', 'processes', 'computer', 'task_status', 'task_cancel', 'task_answer', 'compound'):
        raise RuntimeError('Could not interpret that request. Please try again.')
    if result.get('model_tier') not in ('default', 'sol', 'astra') or type(result.get('needs_clarification')) is not bool:
        raise RuntimeError('Could not choose how to handle that request. Please try again.')
    if result['action'] == 'compound':
        steps = result.get('steps')
        if not isinstance(steps, list) or not 2 <= len(steps) <= 8:
            raise RuntimeError('Could not separate the actions in that request. Please try again.')
        validated = []
        for index, step in enumerate(steps):
            if (not isinstance(step, dict) or step.get('action') not in ('normal', 'screen', 'processes', 'computer')
                    or not isinstance(step.get('request'), str) or not 1 <= len(step['request'].strip()) <= 4000):
                raise RuntimeError('Could not identify the individual actions. Please try again.')
            dependencies = step.get('depends_on')
            if not isinstance(dependencies, list) or any(type(value) is not int or not 0 <= value < index for value in dependencies):
                raise RuntimeError('Could not determine the order of those actions. Please try again.')
            selected = validate_decision({**step, 'model_tier': result['model_tier'], 'needs_clarification': False,
                                         'task_id': '', 'delay_seconds': None, 'wake_at': None}, context)
            if selected['needs_clarification']:
                result['needs_clarification'] = True
            validated.append(selected)
        result.update(steps=validated, capabilities=[], process_names=[], system_actions=[], browser='')
        return result
    try:
        if 'capabilities' not in result:
            raise ValueError('Missing tool selection')
        granted, _, _ = capabilities_for(result)
        actions = result.get('system_actions')
        if not isinstance(actions, list) or any(action not in SYSTEM_ACTIONS for action in actions):
            raise ValueError('Invalid system actions')
        if not isinstance(result.get('browser'), str) or len(result['browser']) > 120:
            raise ValueError('Invalid browser choice')
        result['capabilities'] = granted
    except (ValueError, TypeError):
        raise RuntimeError('Could not choose the tools for that request. Please try again.') from None
    if result['action'] == 'processes' or any(tool in result['capabilities'] for tool in ('list_running_processes', 'terminate_processes')):
        names = result.get('process_names')
        if not isinstance(names, list) or len(names) > 8 or any(
                not isinstance(name, str) or not name.strip() or len(name) > 120 for name in names):
            raise RuntimeError('Could not interpret which running apps to check. Please try again.')
        if 'terminate_processes' in result['capabilities'] and not names:
            result.update(needs_clarification=True, capabilities=[])
    if result['action'] in ('task_status', 'task_cancel'):
        task_id = result.get('task_id', '')
        available = {row['id'] for row in (context or {}).get('recentTasks', [])}
        if not isinstance(task_id, str) or (task_id and task_id not in available):
            raise RuntimeError('That task reference is no longer available. Ask for the latest task update.')
    if result['action'] == 'task_answer':
        # An answer must belong to a question that is still waiting on the owner.
        waiting = {row['id'] for row in (context or {}).get('waitingQuestions', [])}
        if result.get('task_id') not in waiting:
            raise RuntimeError('That question is no longer waiting for an answer. Please say the whole request again.')
    return result
