import os
import unittest
from unittest.mock import patch

from desktop import app_identity
from desktop.app_targets import window_app, window_score
from desktop.windows import AppCatalog

DECLARED = {r'C:\Apps\Wordsmith\WSMAIN.EXE': ('Wordsmith Writer',), r'C:\Apps\Chat\Telechat.exe': ('Telechat Desktop',),
            r'C:\Apps\Studio\StudioBeta2.exe': ('StudioBeta2', 'Pixel Forge Studio'),
            r'C:\Apps\Surf\surf.exe': ('Surf Internet Browser',)}


def window(path, title):
    return {'hwnd': 1, 'pid': 2, 'path': path, 'process': path.rsplit('\\', 1)[-1].lower(), 'title': title, 'rect': [0, 0, 9, 9]}


class ProgramIdentityTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(app_identity, 'declared_names', side_effect=lambda path: DECLARED.get(path, ()))
        patcher.start()
        self.addCleanup(patcher.stop)
        app_identity.default_program.cache_clear()

    def test_windows_are_identified_by_the_program_name_windows_records(self):
        self.assertTrue(window_score({'Name': 'Wordsmith Writer'}, window(r'C:\Apps\Wordsmith\WSMAIN.EXE', 'Chapter 1')))
        self.assertTrue(window_score({'Name': 'Pixel Forge Studio'}, window(r'C:\Apps\Studio\StudioBeta2.exe', 'Untitled')))

    def test_window_text_cannot_impersonate_another_app(self):
        chat = window(r'C:\Apps\Chat\Telechat.exe', "Plumbing Service's group - project chat")
        apps = [{'Name': 'Services'}, {'Name': 'Telechat Desktop'}]
        self.assertEqual(max(apps, key=lambda app: window_score(app, chat))['Name'], 'Telechat Desktop')
        self.assertFalse(window_score({'Name': 'Services'}, chat))
        tab = window(r'C:\Apps\Surf\surf.exe', 'Melody Stream - Web Player - Surf')
        self.assertFalse(window_score({'Name': 'Melody Stream'}, tab), 'A browser tab is not the app it shows')

    def test_each_window_is_labelled_with_the_installed_app_it_belongs_to(self):
        apps = [{'Name': 'Services'}, {'Name': 'Telechat Desktop'}, {'Name': 'Surf Browser'}, {'Name': 'Melody Stream'}]
        self.assertEqual(window_app(apps, window(r'C:\Apps\Chat\Telechat.exe', "Plumbing Service's group")), 'Telechat Desktop')
        self.assertEqual(window_app(apps, window(r'C:\Apps\Surf\surf.exe', 'Melody Stream - Web Player - Surf')), 'Surf Browser')
        self.assertIsNone(window_app(apps, window(r'C:\Tools\unknown.exe', 'Untitled')))
        self.assertIsNone(window_app([], window(r'C:\Apps\Surf\surf.exe', 'Surf')))

    def test_conventional_titles_identify_apps_whose_programs_say_nothing(self):
        self.assertTrue(window_score({'Name': 'Ledger Pad'}, window(r'C:\Host\frame.exe', 'Budget.lp - Ledger Pad')))
        self.assertFalse(window_score({'Name': 'Ledger Pad'}, window(r'C:\Host\frame.exe', 'Ledger Pad tips and tricks - Surf')))

    def test_generic_requests_follow_the_owner_s_windows_defaults(self):
        catalog = AppCatalog()
        catalog.apps = [{'Name': 'Surf Browser', 'AppID': 'surf'}, {'Name': 'Onion Browser', 'AppID': 'onion'},
                        {'Name': 'Melody Stream', 'AppID': 'melody'}]
        with patch.object(app_identity, 'default_program', return_value=r'C:\Apps\Surf\surf.exe'):
            for request in ('browser', 'my browser', 'the internet', 'web browser'):
                self.assertEqual([row['Name'] for row in catalog.match(request)], ['Surf Browser'], request)
            self.assertEqual([row['Name'] for row in catalog.match('onion browser')], ['Onion Browser'])
        with patch.object(app_identity, 'default_program', return_value=None):
            self.assertEqual([row['Name'] for row in catalog.match('browser')], ['Surf Browser', 'Onion Browser'])

    def test_misheard_names_resolve_only_when_one_app_is_clearly_meant(self):
        catalog = AppCatalog()
        catalog.apps = [{'Name': 'Melody Stream', 'AppID': 'm'}, {'Name': 'Pixel Forge Studio', 'AppID': 'p'},
                        {'Name': 'Fable Reader', 'AppID': 'f'}, {'Name': 'Table Reader', 'AppID': 't'}]
        with patch.object(app_identity, 'default_program', return_value=None):
            self.assertEqual([row['Name'] for row in catalog.match('melody streem')], ['Melody Stream'])
            self.assertEqual([row['Name'] for row in catalog.match('pixel forj studio')], ['Pixel Forge Studio'])
            self.assertEqual(catalog.match('cable reader'), [], 'Two equally close apps must not be guessed')
            self.assertEqual(catalog.match('weather radar'), [])

    def test_registry_commands_name_the_program(self):
        self.assertEqual(app_identity.command_program('"C:\\Apps\\Surf\\surf.exe" --single-argument %1'), 'C:\\Apps\\Surf\\surf.exe')
        self.assertEqual(app_identity.command_program('C:\\Apps\\Surf\\surf.exe %1'), 'C:\\Apps\\Surf\\surf.exe')
        self.assertIsNone(app_identity.command_program(''))


class RealProgramMetadataTests(unittest.TestCase):
    def test_windows_programs_declare_their_names_without_the_os_product_label(self):
        app_identity.declared_names.cache_clear()
        self.assertEqual(app_identity.declared_names(os.path.join(os.environ['SystemRoot'], 'explorer.exe')), ('Windows Explorer',))
        self.assertEqual(app_identity.declared_names(''), ())
        self.assertEqual(app_identity.declared_names(os.path.join(os.environ['SystemRoot'], 'no-such-program.exe')), ())


if __name__ == '__main__':
    unittest.main()
