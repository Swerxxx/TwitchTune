import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock,patch
from types import SimpleNamespace
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from binary_updates import UpdateError,version_tuple,https_url,open_https,read_manifest,download_package,stage_package,write_health_marker
from tools.prepare_binary_release import stamp,package_build

CHANNEL='https://github.com/example/twitchtune/releases/latest/download/latest.json'
ASSET='https://github.com/example/twitchtune/releases/download/v1.1.0/TwitchTune-Windows-x64.zip'


def response(body=b'',status=200,headers=None):
    result=Mock(status_code=status,headers=headers or {})
    result.__enter__=Mock(return_value=result);result.__exit__=Mock(return_value=False)
    result.iter_content.return_value=[body]
    return result


def manifest(body=b'package',**changes):
    return dict(dict(app='TwitchTune',platform='windows-x64',version='1.1.0',url=ASSET,
                     sha256=hashlib.sha256(body).hexdigest(),size=len(body),updater=1),**changes)


class BinaryUpdateTests(unittest.TestCase):
    def test_semantic_versions_and_https_validation(self):
        self.assertGreater(version_tuple('1.10.0'),version_tuple('1.9.9'))
        for value in ('1.2','v1.2.3','1.0.0;command','01.1.0'):
            with self.assertRaises(UpdateError):version_tuple(value)
        for value in ('http://example.com/a','https://user:password@example.com/a','https://example.com:bad/a'):
            with self.assertRaises(UpdateError):https_url(value)

    def test_manifest_identity_checksum_and_repository_are_validated(self):
        with patch('binary_updates.requests.get',return_value=response(json.dumps(manifest()).encode())):
            self.assertEqual(read_manifest(CHANNEL)['version'],'1.1.0')
        for changes in (dict(app='Other'),dict(sha256='bad'),dict(size=-1),dict(platform='linux'),dict(url='https://github.com/other/repo/releases/download/v1/a.zip')):
            with self.subTest(changes=changes),patch('binary_updates.requests.get',return_value=response(json.dumps(manifest(**changes)).encode())):
                with self.assertRaises(UpdateError):read_manifest(CHANNEL)

    def test_insecure_redirect_is_rejected_before_sending_an_http_request(self):
        redirect=response(status=302,headers={'Location':'http://example.com/package.zip'})
        with patch('binary_updates.requests.get',return_value=redirect) as request:
            with self.assertRaises(UpdateError):open_https(CHANNEL)
        request.assert_called_once();redirect.close.assert_called_once()

    def test_package_checksum_and_cancellation_leave_no_partial_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with patch('binary_updates.requests.get',return_value=response(b'package')):
                self.assertEqual(download_package(manifest(),root).read_bytes(),b'package')
            for body,cancelled in ((b'changed',lambda:False),(b'package',lambda:True)):
                with patch('binary_updates.requests.get',return_value=response(body)):
                    with self.assertRaises(UpdateError):download_package(manifest(),root,cancelled=cancelled)
                self.assertFalse((root/'package.zip.part').exists())

    def make_zip(self,path,extra=None,version='1.1.0'):
        with zipfile.ZipFile(path,'w') as zipped:
            zipped.writestr('TwitchTune/TwitchTune.exe',b'MZfake compiled fixture')
            zipped.writestr('TwitchTune/_internal/runtime.dll',b'fake runtime')
            zipped.writestr('TwitchTune/release-info.json',json.dumps(dict(version=version,updater=1)))
            for name,value in (extra or {}).items():zipped.writestr(name,value)

    def test_staging_never_modifies_installed_files_or_user_data(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);target=root/'installed';target.mkdir()
            (target/'TwitchTune.exe').write_bytes(b'old executable')
            (target/'queue.sqlite3').write_bytes(b'personal queue')
            archive=root/'new.zip';self.make_zip(archive)
            stage=stage_package(archive,target,'1.1.0')
            try:
                self.assertEqual((stage/'TwitchTune.exe').read_bytes(),b'MZfake compiled fixture')
                self.assertEqual((target/'TwitchTune.exe').read_bytes(),b'old executable')
                self.assertEqual((target/'queue.sqlite3').read_bytes(),b'personal queue')
            finally:shutil.rmtree(stage)

    def test_unsafe_paths_profile_files_and_wrong_version_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);target=root/'installed';target.mkdir();archive=root/'new.zip'
            for name in ('TwitchTune/../outside','TwitchTune/queue.sqlite3','TwitchTune/settings.json','/outside','TwitchTune/_internal/..\\evil'):
                with self.subTest(name=name):
                    self.make_zip(archive,{name:b'bad'})
                    with self.assertRaises(UpdateError):stage_package(archive,target,'1.1.0')
                    self.assertEqual(list(root.glob('.TwitchTune-update-*')),[])
            self.make_zip(archive,version='1.2.0')
            with self.assertRaises(UpdateError):stage_package(archive,target,'1.1.0')

    def test_health_marker_can_only_be_written_under_profile_updates(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            self.assertFalse(write_health_marker(root/'outside.ready',root,'1.1.0'))
            health=root/'updates/health.ready'
            self.assertTrue(write_health_marker(health,root,'1.1.0'))
            self.assertEqual(health.read_text(),'1.1.0')

    def test_release_packager_builds_matching_manifest_and_rejects_personal_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);build=root/'dist';build.mkdir();(build/'_internal').mkdir()
            (build/'TwitchTune.exe').write_bytes(b'MZcompiled fixture')
            (build/'_internal/runtime.dll').write_bytes(b'fake runtime')
            channel=stamp(root,'1.1.0','example/twitchtune')
            self.assertEqual(channel,CHANNEL)
            archive=package_build(build,root/'release','1.1.0','example/twitchtune')
            payload=json.loads((root/'release/latest.json').read_text())
            self.assertEqual(payload['size'],archive.stat().st_size)
            self.assertEqual(payload['sha256'],hashlib.sha256(archive.read_bytes()).hexdigest())
            (build/'settings.json').write_text('private')
            with self.assertRaises(ValueError):package_build(build,root/'other','1.2.0','example/twitchtune')

    def test_release_repository_validation_prevents_path_injection(self):
        with tempfile.TemporaryDirectory() as folder:
            for repository in ('a/..','a/.','a/b/c','a/b?x','a/б'):
                with self.subTest(repository=repository),self.assertRaises(ValueError):stamp(folder,'1.1.0',repository)
            self.assertFalse((Path(folder)/'release_version.py').exists())


class BinaryUpdateSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.application=QApplication.instance() or QApplication([])

    def owner(self):
        owner=SimpleNamespace(cfg={},closing=False,current=None,smoke_test=True,close=Mock(),incoming=Mock())
        def run(fn,done,failed):
            try:result=fn()
            except Exception as error:failed(str(error))
            else:done(result)
        owner.run_async=run
        return owner

    def tearDown(self):
        from PySide6.QtCore import QCoreApplication,QEvent
        for widget in self.application.topLevelWidgets():
            if widget.__class__.__name__=='BinaryUpdateSettings':widget.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
        self.application.processEvents()

    def test_network_failure_is_visible_and_does_not_claim_up_to_date(self):
        from binary_update_settings import BinaryUpdateSettings
        with tempfile.TemporaryDirectory() as folder:
            owner=self.owner();panel=BinaryUpdateSettings(owner,folder);panel.url=CHANNEL
            with patch('binary_update_settings.read_manifest',side_effect=UpdateError('Сервер обновлений недоступен.')):panel.check()
            self.assertEqual(panel.status.text(),'Сервер обновлений недоступен.')
            self.assertFalse(panel.install_button.isEnabled());self.assertFalse(panel.busy)
            owner.close.assert_not_called();panel.close()

    def test_failed_helper_handoff_keeps_application_open_and_removes_staging(self):
        from binary_update_settings import BinaryUpdateSettings
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);stage=root/'.TwitchTune-update-test';stage.mkdir()
            owner=self.owner();panel=BinaryUpdateSettings(owner,root/'profile');panel.frozen=True;panel.manifest=manifest()
            with patch('binary_update_settings.download_package',return_value=root/'package.zip'),patch('binary_update_settings.stage_package',return_value=stage),patch('binary_update_settings.launch_installer',side_effect=UpdateError('Не удалось запустить установку.')):
                panel.install()
            owner.close.assert_not_called();self.assertFalse(stage.exists());self.assertFalse(panel.busy)
            self.assertEqual(panel.status.text(),'Не удалось запустить установку.');panel.close()

    def test_successful_helper_handoff_closes_application_after_preparation(self):
        from binary_update_settings import BinaryUpdateSettings
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);stage=root/'.TwitchTune-update-test';stage.mkdir()
            owner=self.owner();panel=BinaryUpdateSettings(owner,root/'profile');panel.frozen=True;panel.manifest=manifest()
            with patch('binary_update_settings.download_package',return_value=root/'package.zip'),patch('binary_update_settings.stage_package',return_value=stage),patch('binary_update_settings.launch_installer') as launch:
                panel.install()
            launch.assert_called_once();owner.close.assert_called_once();self.assertTrue(stage.exists());panel.close()
