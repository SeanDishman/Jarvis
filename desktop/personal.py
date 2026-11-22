from desktop.memory import Memory
from desktop.documents import Documents
from desktop.document_search import DocumentSearch
from desktop.monitoring import Monitor
from desktop.routing import require_capability

DOCUMENT_TOOLS = ['list_file_locations', 'create_document', 'edit_document', 'move_document']


class PersonalTools:
    def __init__(self, preferences, files, events):
        self.preferences, self.files, self.events = preferences, files, events
        self.memory = Memory(preferences)
        self.documents = Documents(preferences, files)
        self.search = DocumentSearch(files)
        self.monitor = Monitor(preferences,events)

    def execute(self, context, name, arguments):
        if name == 'monitor_status':
            return self.monitor.status()
        if name == 'dismiss_alerts':
            require_capability(context, name)
            return self.monitor.dismiss(arguments.get('id',''))
        if name == 'monitor_settings':
            require_capability(context, name)
            enabled = arguments.get('enabled')
            if type(enabled) is not bool:
                raise ValueError('Choose whether monitoring is enabled')
            self.preferences.save({'monitorEnabled':enabled})
            return self.monitor.status()
        if name == 'windows_control':
            from desktop.system_tools import control
            return control(arguments.get('action'),arguments.get('value'),context.get('ownerText',''),context.get('systemActions', []))
        if name == 'search_document_content':
            result = self.search.search(arguments.get('query', ''), arguments.get('limit', 10))
            self.events.put(('files_found', result))
            return result
        if name == 'list_file_locations':
            return self.documents.locations()
        if name in DOCUMENT_TOOLS:
            require_capability(context, name)
            if name == 'create_document':
                return self.documents.create(arguments.get('location'), arguments.get('name'), arguments.get('text', ''), arguments.get('rows'))
            if name == 'edit_document':
                return self.documents.edit(arguments.get('id'), arguments.get('revision'), arguments.get('operation'),
                    arguments.get('find', ''), arguments.get('text', ''), arguments.get('cells'))
            if name == 'move_document':
                return self.documents.move(arguments.get('id'), arguments.get('revision'), arguments.get('location'), arguments.get('name'))
        if name == 'search_memory':
            return self.memory.search(arguments.get('query', ''), arguments.get('limit', 8))
        if name == 'read_memory':
            return self.memory.read(arguments.get('id', ''))
        if name in ('save_memory', 'forget_memory'):
            require_capability(context, name)
            if name == 'save_memory':
                return self.memory.save(arguments.get('title', ''), arguments.get('text', ''),
                                        arguments.get('id', ''), arguments.get('revision', ''))
            return self.memory.forget(arguments.get('id', ''), arguments.get('revision', ''))
        return None
