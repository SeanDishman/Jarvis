import json
from pathlib import Path

TOOL_SCOPES = json.loads(Path(__file__).with_name('tool-scopes.json').read_text(encoding='utf-8'))
TOOL_DOMAINS = {tool: domain for domain, tools in TOOL_SCOPES.items() for tool in tools}
SYSTEM_ACTIONS = ('volume_get', 'volume_set', 'volume_up', 'volume_down', 'volume_mute',
                  'volume_unmute', 'play_pause', 'next_track', 'previous_track',
                  'brightness_get', 'brightness_set', 'clipboard_read', 'clipboard_write')


def capabilities_for(decision):
    if not isinstance(decision, dict):
        raise ValueError('Tool access requires an interpreted owner request.')
    tools = decision.get('capabilities', [])
    if not isinstance(tools, list) or any(not isinstance(tool, str) or tool not in TOOL_DOMAINS for tool in tools):
        raise ValueError('The model selected an unavailable tool.')
    tools = list(dict.fromkeys(tools))
    action = decision.get('action', 'normal')
    if action == 'normal' and decision.get('system_actions'):
        if any(action not in SYSTEM_ACTIONS for action in decision['system_actions']):
            raise ValueError('The model selected an unavailable Windows control.')
        if 'windows_control' not in tools:
            tools.append('windows_control')
    domain = TOOL_DOMAINS[tools[0]] if tools else 'web'
    if action == 'computer':
        domain, tools = 'computer', TOOL_SCOPES['computer'][:]
    elif action == 'compound':
        domain, tools = 'tasks', []
    elif action in ('screen', 'processes'):
        domain, tools = action, []
    if decision.get('needs_clarification') or action in ('break', 'task_status', 'task_cancel', 'task_answer'):
        tools = []
    return tools, None, domain


def require_capability(context, name):
    if name not in context.get('capabilities', []):
        raise ValueError('This action was not authorized for the current owner request.')
