"""Keep the explicitly imported yt-dlp plugin discoverable in frozen builds.

yt-dlp's PluginFinder requires real namespace directories before it allows
imports, even when the module itself is already present in PyInstaller's PYZ.
Resolve the pinned distribution without importing its namespace at build time.
"""
from importlib.metadata import distribution

plugin = distribution('yt-dlp-yandex-music').locate_file(
    'yt_dlp_plugins/extractor/yandex_music_v2.py')
if not plugin.is_file():
    raise RuntimeError('The Yandex Music plugin is missing. Reinstall requirements.txt.')

datas = [(str(plugin), 'yt_dlp_plugins/extractor')]
hiddenimports = ['yt_dlp_plugins.extractor.yandex_music_v2']
