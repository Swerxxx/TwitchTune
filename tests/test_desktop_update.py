import subprocess
import sys
import tempfile
import unittest
import zipfile
import errno
import io
import base64
import zlib
import ast
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from desktop_runtime import acquire_lock,update_source
from desktop_launcher import ensure_dependencies


class DesktopUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.target=self.base/'installation';self.target.mkdir()
        self.data=self.base/'profile';self.data.mkdir()
        (self.target/'app.py').write_text('old app')
        (self.target/'core.py').write_text('old core')
        (self.target/'requirements.txt').write_text('old requirements')
        (self.data/'queue.sqlite3').write_bytes(b'personal queue')
        self.archive=self.base/'new.zip'

    def tearDown(self):self.temp.cleanup()

    def archive_with(self,extra=None,prefix='TwitchTune/'):
        files={name:'new '+name for name in ('app.py','core.py','requirements.txt','desktop_launcher.py','desktop_runtime.py')}
        files['assets/icon.png']='image'
        with zipfile.ZipFile(self.archive,'w') as zipped:
            for name,content in files.items():zipped.writestr(prefix+name,content)
            for name,content in (extra or {}).items():zipped.writestr(name,content)

    def test_source_update_keeps_profile_and_existing_environment_and_creates_backup(self):
        (self.target/'settings.json').write_text('personal settings')
        (self.target/'.venv').mkdir();(self.target/'.venv/python.exe').write_text('keep runtime')
        self.archive_with({'TwitchTune/queue.sqlite3':'do not install','TwitchTune/settings.json':'do not install'})
        backup=update_source(self.archive,self.target,self.data)
        self.assertEqual((self.target/'app.py').read_text(),'new app.py')
        self.assertEqual((backup/'app.py').read_text(),'old app')
        self.assertEqual((self.data/'queue.sqlite3').read_bytes(),b'personal queue')
        self.assertEqual((self.target/'settings.json').read_text(),'personal settings')
        self.assertEqual((self.target/'.venv/python.exe').read_text(),'keep runtime')
        self.assertFalse((self.target/'queue.sqlite3').exists())

    def test_clean_distribution_archive_is_also_supported(self):
        self.archive_with(prefix='TwitchTune-Clean/TwitchTune/')
        update_source(self.archive,self.target,self.data)
        self.assertEqual((self.target/'core.py').read_text(),'new core.py')

    def test_malicious_paths_are_rejected_before_changing_source(self):
        for name in ('TwitchTune/../outside.py','/outside.py','TwitchTune/C:/outside.py','TwitchTune/..\\outside.py'):
            with self.subTest(name=name):
                self.archive_with({name:'bad'})
                with self.assertRaises(ValueError):update_source(self.archive,self.target,self.data)
                self.assertEqual((self.target/'app.py').read_text(),'old app')

    def test_existing_symlink_outside_installation_is_not_followed(self):
        outside=self.base/'outside';outside.mkdir()
        (self.target/'assets').symlink_to(outside,target_is_directory=True)
        self.archive_with()
        with self.assertRaises(ValueError):update_source(self.archive,self.target,self.data)
        self.assertFalse((outside/'icon.png').exists())

    def test_incomplete_archive_is_rejected_without_changes(self):
        with zipfile.ZipFile(self.archive,'w') as zipped:zipped.writestr('TwitchTune/app.py','incomplete')
        with self.assertRaises(ValueError):update_source(self.archive,self.target,self.data)
        self.assertEqual((self.target/'app.py').read_text(),'old app')

    def test_failed_file_replacement_rolls_back_completed_replacements(self):
        import os
        self.archive_with();replace=os.replace;calls=[]
        def fail_once(source,destination):
            calls.append(str(destination))
            if len(calls)==2:raise OSError('disk error')
            return replace(source,destination)
        with patch('desktop_runtime.os.replace',side_effect=fail_once):
            with self.assertRaises(OSError):update_source(self.archive,self.target,self.data)
        self.assertEqual((self.target/'app.py').read_text(),'old app')
        self.assertEqual((self.target/'core.py').read_text(),'old core')
        self.assertEqual((self.data/'queue.sqlite3').read_bytes(),b'personal queue')

    def test_running_app_blocks_update_then_closing_allows_it(self):
        self.archive_with()
        with acquire_lock(self.data/'runtime.lock'):
            with self.assertRaises(BlockingIOError):update_source(self.archive,self.target,self.data)
        self.assertEqual((self.target/'app.py').read_text(),'old app')
        update_source(self.archive,self.target,self.data)
        self.assertEqual((self.target/'app.py').read_text(),'new app.py')

    def test_dependencies_are_installed_once_and_again_only_after_requirements_change(self):
        requirement=self.target/'requirements.txt';requirement.write_text('example==1')
        with patch('desktop_launcher.subprocess.run',return_value=subprocess.CompletedProcess([],0)) as run:
            ensure_dependencies(self.target,self.data);ensure_dependencies(self.target,self.data)
            self.assertEqual(run.call_count,1)
            requirement.write_text('example==2');ensure_dependencies(self.target,self.data)
            self.assertEqual(run.call_count,2)

    def test_failed_dependency_install_is_not_marked_successful(self):
        (self.target/'requirements.txt').write_text('example==1')
        with patch('desktop_launcher.subprocess.run',return_value=subprocess.CompletedProcess([],1)) as run:
            for _ in range(2):
                with self.assertRaises(RuntimeError):ensure_dependencies(self.target,self.data)
            self.assertEqual(run.call_count,2)

    def test_windows_lock_permission_error_is_reported_as_app_running(self):
        msvcrt=SimpleNamespace(LK_NBLCK=2,locking=unittest.mock.Mock(side_effect=PermissionError(errno.EACCES,'locked')))
        with patch('desktop_runtime.os',SimpleNamespace(name='nt')),patch.dict(sys.modules,msvcrt=msvcrt):
            with self.assertRaises(BlockingIOError):acquire_lock(self.data/'runtime.lock')

    def test_small_updater_contains_a_valid_bootstrap_and_complete_runtime_payload(self):
        from tools.make_quick_update import build
        output=self.base/'TwitchTune-Update.bat'
        root=Path(__file__).resolve().parents[1];build(root,output)
        encoded=output.read_bytes().split(b'::TWITCHTUNE_PAYLOAD::',1)[1]
        code=zlib.decompress(base64.b64decode(encoded)).decode('utf-8')
        tree=ast.parse(code)
        literal=tree.body[-1].value.args[0].args[0].args[0].value
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(literal))) as zipped:
            self.assertIsNone(zipped.testzip())
            for name in ('app.py','desktop_runtime.py','desktop_launcher.py','setup_shortcuts.ps1','requirements.txt'):
                self.assertIn('TwitchTune/'+name,zipped.namelist())
            self.assertFalse(any(n.endswith(('.sqlite3','.json')) for n in zipped.namelist()))
        self.assertLess(output.stat().st_size,600000)

    def test_small_installer_remembers_source_folder_and_preserves_profile(self):
        from quick_install import install
        self.archive_with()
        profile=self.base/'home/TwitchTune';profile.mkdir(parents=True)
        (profile/'queue.sqlite3').write_bytes(b'keep')
        with patch('quick_install.Path.home',return_value=self.base/'home'), \
             patch('desktop_launcher.choose_location',return_value=str(self.target)) as picker, \
             patch('desktop_launcher.ensure_dependencies') as dependencies, \
             patch('quick_install.subprocess.run',return_value=subprocess.CompletedProcess([],0)):
            self.assertEqual(install(self.archive.read_bytes()),0)
            self.assertEqual(install(self.archive.read_bytes()),0)
        picker.assert_called_once_with(directory=True)
        self.assertEqual(dependencies.call_count,2)
        self.assertEqual((profile/'queue.sqlite3').read_bytes(),b'keep')
        self.assertEqual((profile/'source-folder.txt').read_text(),str(self.target.resolve()))
