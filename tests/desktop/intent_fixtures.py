def decision(*capabilities, action='normal', **values):
    return {'action': action, 'capabilities': list(capabilities), 'system_actions': [], 'browser': '',
            'model_tier': 'default', 'needs_clarification': False, 'task_id': '',
            'delay_seconds': None, 'wake_at': None, 'display': 'current', 'process_names': [], **values}
