"""Acid Deck: black metal, inset LCD screens, and yellow-green hardware accents."""
STYLE = '''
QWidget { color: #d8ddcb; font: 10pt "Segoe UI"; }
QMainWindow, QDialog { background: #090b09; }
QDialog QWidget { background-color: #10140c; }
QWidget#shell, QWidget#page { background: transparent; }
QFrame#metal, QGroupBox { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #252a20,stop:.06 #151912,stop:1 #0d100c); border: 1px solid #454d36; border-radius: 13px; }
QFrame#dock { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #3b4330,stop:.025 #171c12,stop:.11 #0b0e09,stop:1 #171c12); border: 1px solid #697c36; border-radius: 17px; }
QGroupBox { margin-top: 12px; padding: 17px 10px 10px; }
QGroupBox::title { subcontrol-origin: margin; left: 13px; padding: 0 5px; color: #c6f52b; font: 9pt "Consolas"; }
QLabel { background: transparent; border: none; }
QLabel#brand { font: bold 25pt "Bahnschrift"; color: #e4e9d5; letter-spacing: -1px; }
QLabel#acid { color: #c6f52b; font: bold 10pt "Consolas"; }
QLabel#micro { color: #7e896a; font: 8pt "Consolas"; }
QLabel#section { color: #c6f52b; font: bold 10pt "Consolas"; }
QLabel#counter { color: #c6f52b; font: bold 25pt "Consolas"; }
QLabel#nowTitle { color: #e4e9d5; font: bold 10pt "Segoe UI"; }
QLabel#muted { color: #879477; font: 9pt "Segoe UI"; }
QPushButton { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #414735,stop:.10 #2c3224,stop:.46 #1b2114,stop:.51 #0c1108,stop:1 #202718); border: 1px solid #56623d; border-radius: 7px; padding: 8px 12px; color: #c8d1b3; }
QPushButton:hover { border-color: #c6f52b; color: #e1ff8c; }
QPushButton:pressed { background: #111a06; }
QPushButton:disabled { color: #626b52; border-color: #303727; }
QPushButton#primary { color: #172300; font-weight: bold; border: 1px solid #defb6e; background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #d7fa62,stop:.48 #b9ea2a,stop:.51 #9cc718,stop:1 #b7de37); }
QPushButton#primary:disabled { color: #71804e; background: #232d16; border-color: #3f5125; }
QPushButton#nav { text-align: left; padding: 13px 10px; font: 9pt "Consolas"; border-color: #333e23; background: #13190e; }
QPushButton#nav:checked { color: #d9ff7d; border: 1px solid #a2ce25; background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #354719,stop:1 #151e0b); }
QPushButton#score { min-width: 20px; padding: 5px; border-radius: 4px; font: 9pt "Consolas"; }
QPushButton#score:checked { color: #112000; background: #c6f52b; border-color: #d9ff69; }
QLineEdit, QPlainTextEdit, QListWidget, QComboBox, QSpinBox { background: #080c05; color: #d5ddc4; border: 1px solid #3e4c2a; border-radius: 7px; padding: 8px; selection-background-color: #354b17; selection-color: #e7ffb3; }
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #abd628; }
QLineEdit { min-height: 19px; }
QComboBox::drop-down { border: none; width: 22px; }
QSpinBox { font: bold 15pt "Consolas"; color: #c6f52b; }
QSpinBox#criterionScore { font: bold 10pt "Consolas"; padding: 2px 16px 2px 4px; }
QSpinBox#criterionScore:disabled { color: #68715a; border-color: #303727; }
QListWidget#queue { border: none; background: transparent; padding: 0; }
QListWidget::item { padding: 10px 8px; border-bottom: 1px solid #222c19; }
QListWidget::item:selected { background: #273617; color: #e0ff99; }
QTabWidget::pane { border: none; background: transparent; }
QTabBar::tab { background: #171e10; border: 1px solid #46552c; padding: 6px; }
QSlider::groove:horizontal { height: 4px; background: #303a22; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #c6f52b; border-radius: 2px; }
QSlider::handle:horizontal { width: 9px; margin: -4px 0; background: #dcff74; border: 1px solid #92aa3f; border-radius: 5px; }
QScrollBar:vertical { background: #0d1208; width: 8px; margin: 0; }
QScrollBar::handle:vertical { background: #647b34; min-height: 25px; border-radius: 4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QToolTip { background: #141d0a; color: #e3ffa8; border: 1px solid #718d31; }
QPushButton#lyricsButton { padding: 2px 8px; font: 8pt "Segoe UI"; border-radius: 5px; }
QTextEdit#lyricsBody { background: transparent; color: #d8ddcb; border: none; padding: 10px; selection-background-color: #354b17; }
QLabel#lyricsTitle { font: bold 25pt "Segoe UI"; color: #e4e9d5; }
QFrame#lyricsPaper { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #151912,stop:1 #090b09); border: 1px solid #454d36; border-radius: 16px; }
'''

# Every old green shade has a matching tinted shade. This also covers custom
# QPainter surfaces, rather than recoloring only Qt's standard controls.
import colorsys
import re
from pathlib import Path

THEMES = {
    'violet': dict(name='Фиолетовый', tag='PHANTOM VIOLET', accent='#b58aea',
                   description='Дымчатый фиолетовый и холодная подсветка LCD.', hue=.755, saturation=.68,
                   primary=('#d4b9f6', '#b699e4', '#9674ce', '#b99be9')),
    'green': dict(name='Зелёный', tag='ACID GREEN', accent='#c6f52b',
                  description='Кислотный зелёный — исходная тема ACID DECK.', hue=.21, saturation=1,
                  primary=('#d7fa62', '#b9ea2a', '#9cc718', '#b7de37')),
    'black': dict(name='Чёрный', tag='CARBON BLACK', accent='#c0c6ce',
                  description='Чёрный графит, серебристые кнопки и монохромный LCD.', hue=0, saturation=0,
                  primary=('#e0e3e8', '#bac0c9', '#939ca9', '#bdc4ce')),
    'red': dict(name='Красный', tag='CRIMSON RED', accent='#ee7888',
                description='Глубокий карминовый с мягкой красной подсветкой.', hue=.975, saturation=.72,
                primary=('#f5a6b0', '#ed8190', '#cc6074', '#e98695')),
}


def normalize_theme(key):
    return key if isinstance(key, str) and key in THEMES else 'green'


def theme_color(value, key='green'):
    if key == 'dark_goth':
        if value.lower() == '#c6f52b':
            return '#d79a9c'
        rgb = tuple(int(value[i:i+2], 16) / 255 for i in (1, 3, 5))
        _, lightness, _ = colorsys.rgb_to_hls(*rgb)
        hue = .83 if lightness < .32 else .055
        saturation = .19 if lightness < .32 else .26
        channels = colorsys.hls_to_rgb(hue, lightness, saturation)
        return '#' + ''.join(f'{round(channel * 255):02x}' for channel in channels)
    key = normalize_theme(key)
    if key == 'green':
        return value
    theme = THEMES[key]
    if value.lower() == '#c6f52b':
        return theme['accent']
    gradient = ('#d7fa62', '#b9ea2a', '#9cc718', '#b7de37')
    if value.lower() in gradient:
        return theme['primary'][gradient.index(value.lower())]
    rgb = tuple(int(value[i:i+2], 16)/255 for i in (1, 3, 5))
    _, lightness, saturation = colorsys.rgb_to_hls(*rgb)
    rgb = colorsys.hls_to_rgb(theme['hue'], lightness, min(.85, saturation*theme['saturation']))
    return '#' + ''.join(f'{round(channel*255):02x}' for channel in rgb)


INTERFACE_STYLES = {'classic': 'Базовый', 'dark_goth': 'Dark Goth', 'biohazard': 'Biohazard PSP'}


def normalize_interface_style(key):
    return key if isinstance(key, str) and key in INTERFACE_STYLES else 'classic'


def goth_button_image(active=False):
    name = 'goth-button-active.svg' if active else 'goth-button.svg'
    return (Path(__file__).resolve().parent / 'assets' / name).as_posix()


def stylesheet(key='green', interface_style='classic'):
    palette = 'dark_goth' if interface_style == 'dark_goth' else key
    base = re.sub(r'#[0-9a-fA-F]{6}\b', lambda match: theme_color(match.group(), palette), STYLE)
    if interface_style == 'biohazard':
        surfaces = BIO_SURFACES.replace('__PANEL__', bio_image('panel', key)).replace('__BUTTON__', bio_image('button', key))
        return base + re.sub(r'#[0-9a-fA-F]{6}\b', lambda match: theme_color(match.group(), key), surfaces)
    if interface_style != 'dark_goth':
        return base
    panel = (Path(__file__).resolve().parent / 'assets' / 'goth-panel.svg').as_posix()
    arrow = (Path(__file__).resolve().parent / 'assets' / 'step-down.svg').as_posix()
    surfaces = GOTH_SURFACES.replace('__GOTH_PANEL__', panel).replace('__GOTH_ARROW__', arrow)
    buttons = GOTH_BUTTON_STYLE.replace('__GOTH__', goth_button_image()).replace(
        '__GOTH_ACTIVE__', goth_button_image(active=True))
    return base + surfaces + buttons


GOTH_SURFACES = """
QMainWindow, QDialog { background: #100c13; }
QDialog QWidget { background-color: #18121c; }
QFrame#metal, QFrame#dock, QGroupBox, QFrame#lyricsPaper {
    background: transparent;
    border: 8px solid transparent;
    border-radius: 0;
    border-image: url("__GOTH_PANEL__") 26 26 26 26 stretch stretch;
}
QGroupBox { padding: 10px 4px 4px; }
QGroupBox::title {
    color: #deb6af;
    background: #18121c;
    font: bold 9pt "Georgia";
}
QLabel#brand { color: #ede0d5; font: bold 24pt "Georgia"; }
QLabel#brandMark {
    border: 6px solid transparent;
    border-radius: 0;
    border-image: url("__GOTH_PANEL__") 26 26 26 26 stretch stretch;
    background: #1d1420;
    color: #e1b3a7;
    font: bold 20pt "Georgia";
}
QLabel#section, QLabel#acid { font: bold 10pt "Georgia"; color: #deb6af; }
QLabel#lyricsTitle { font: bold 23pt "Georgia"; }
QLabel#micro { color: #aa929f; }
QLabel#muted { color: #b5a3ad; }
QLabel#coverBadge {
    background: #211620;
    border-radius: 2px;
    border-color: #75505e;
    color: #e1b3a7;
    font: bold 16pt "Georgia";
}
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QSpinBox#criterionScore, QSpinBox#durationDigits {
    background: #100c13;
    border-radius: 2px;
    border-color: #5c444f;
    selection-background-color: #683643;
    selection-color: #fff0e5;
}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus,
QSpinBox#criterionScore:focus, QSpinBox#durationDigits:focus { border-color: #ce9091; }
QComboBox::down-arrow { image: url("__GOTH_ARROW__"); width: 10px; height: 6px; }
QComboBox QAbstractItemView { background: #1c151f; selection-background-color: #683643; }
QListWidget::item:selected { background: #482530; color: #f7ded2; }
QFrame#rewardCard, QLabel#rewardBadge, QCheckBox#rewardCheck::indicator { border-radius: 2px; }
QFrame#rewardCard[selected="true"] { border-color: #a36775; background: #30202c; }
QSlider::groove:horizontal { background: #412b38; border-radius: 0; }
QSlider::sub-page:horizontal { background: #9c5367; border-radius: 0; }
QSlider::handle:horizontal { background: #e8baab; border-color: #a67480; border-radius: 2px; }
QScrollBar::handle:vertical { background: #775260; border-radius: 1px; }
QTextEdit#lyricsBody { selection-background-color: #683643; }
"""


GOTH_BUTTON_STYLE = """
QPushButton, QPushButton#primary, QPushButton#nav, QPushButton#score, QPushButton#lyricsButton {
    background: transparent;
    border: 8px solid transparent;
    border-radius: 0;
    border-image: url("__GOTH__") 12 18 12 18 stretch stretch;
    padding: 1px 5px;
    color: #e2d4cb;
}
QPushButton#primary { color: #fff0da; font-weight: bold; }
QPushButton#nav { padding: 4px 8px; }
QPushButton#nav[compact="true"] { padding: 4px 2px; }
QPushButton#score, QPushButton#lyricsButton { border-width: 5px 7px; padding: 1px 4px; }
QPushButton:hover, QPushButton:focus, QPushButton:checked, QPushButton:pressed,
QPushButton#primary, QPushButton#nav:checked, QPushButton#score:checked,
QPushButton#nav:hover, QPushButton#nav:focus, QPushButton#nav:pressed,
QPushButton#score:hover, QPushButton#score:focus, QPushButton#score:pressed,
QPushButton#lyricsButton:hover, QPushButton#lyricsButton:focus, QPushButton#lyricsButton:pressed {
    border-image: url("__GOTH_ACTIVE__") 12 18 12 18 stretch stretch;
    color: #fff0da;
}
QPushButton#nav:checked {
    background: transparent;
    border-width: 8px;
}
QPushButton#score:checked { background: transparent; }
QPushButton:checked, QPushButton#nav:checked, QPushButton#score:checked {
    color: #ffd18e;
    font-weight: bold;
}
QPushButton:pressed, QPushButton#primary:pressed, QPushButton#nav:pressed,
QPushButton#score:pressed, QPushButton#lyricsButton:pressed { color: #cdb8b0; }
QPushButton:disabled, QPushButton#primary:disabled, QPushButton#nav:disabled,
QPushButton#score:disabled, QPushButton#lyricsButton:disabled {
    background: transparent;
    border-image: url("__GOTH__") 12 18 12 18 stretch stretch;
    color: #a09596;
}
"""


STYLE += '''
QLabel#brandMark { border: 1px solid #b5df29; border-radius: 15px; background: #19220b; color: #c6f52b; font: bold 20pt "Bahnschrift"; }
QLabel#coverBadge { border: 1px solid #78943a; border-radius: 12px; background: #131c08; color: #c6f52b; font: bold 10pt "Consolas"; }
QScrollArea#settingsScroll { border: none; background: transparent; }
QScrollArea#settingsScroll > QWidget > QWidget { background: transparent; }
'''


STYLE += '''
QDialog#rewardDialog QLabel { background: transparent; }
QLabel#rewardHeading { font: bold 23pt "Segoe UI"; color: #e4e9d5; }
QLabel#rewardBadge { border: 1px solid #56623d; border-radius: 10px; padding: 12px; color: #c6f52b; background: #131c08; font: bold 9pt "Consolas"; }
QFrame#rewardCard { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #151912,stop:1 #0d100c); border: 1px solid #333e23; border-radius: 12px; }
QFrame#rewardCard[selected="true"] { border-color: #78943a; background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #202718,stop:1 #10140c); }
QFrame#rewardCard QLabel, QWidget#durationControl { background: transparent; }
QLabel#rewardTitle { font: bold 11pt "Segoe UI"; color: #e4e9d5; }
QSpinBox#durationDigits { background: #080c05; color: #c6f52b; padding: 7px 21px 7px 7px; border: 1px solid #3e4c2a; border-radius: 7px; font: bold 21pt "Consolas"; }
QSpinBox#durationDigits:focus { border-color: #abd628; }
QSpinBox#durationDigits:disabled { color: #626b52; border-color: #303727; }
QCheckBox#rewardCheck::indicator { width: 22px; height: 22px; border: 1px solid #56623d; border-radius: 6px; background: #080c05; }
QCheckBox#rewardCheck::indicator:checked { background: #c6f52b; border: 4px solid #354b17; }
QCheckBox#rewardCheck::indicator:hover { border-color: #c6f52b; }
QScrollArea#rewardScroll { border: none; background: transparent; }
QWidget#rewardList { background: transparent; }
QScrollArea#rewardScroll > QWidget { background: transparent; }
'''


STYLE += '''
QSpinBox#durationDigits::up-button { subcontrol-origin: border; subcontrol-position: top right; width: 20px; height: 25px; border: none; }
QSpinBox#durationDigits::down-button { subcontrol-origin: border; subcontrol-position: bottom right; width: 20px; height: 25px; border: none; }
QSpinBox#durationDigits::up-button:hover, QSpinBox#durationDigits::down-button:hover { background: #273617; }
QSpinBox#durationDigits::up-arrow { image: url("__REWARD_UP__"); width: 8px; height: 5px; }
QSpinBox#durationDigits::down-arrow { image: url("__REWARD_DOWN__"); width: 8px; height: 5px; }
QSpinBox#criterionScore::up-button { subcontrol-origin: border; subcontrol-position: top right; width: 17px; height: 13px; border: none; }
QSpinBox#criterionScore::down-button { subcontrol-origin: border; subcontrol-position: bottom right; width: 17px; height: 13px; border: none; }
QSpinBox#criterionScore::up-button:hover, QSpinBox#criterionScore::down-button:hover { background: #273617; }
QSpinBox#criterionScore::up-arrow { image: url("__REWARD_UP__"); width: 8px; height: 5px; }
QSpinBox#criterionScore::down-arrow { image: url("__REWARD_DOWN__"); width: 8px; height: 5px; }
'''.replace('__REWARD_UP__', (Path(__file__).resolve().parent / 'assets' / 'step-up.svg').as_posix()).replace('__REWARD_DOWN__', (Path(__file__).resolve().parent / 'assets' / 'step-down.svg').as_posix())


def bio_image(kind, key='green'):
    return (Path(__file__).resolve().parent / 'assets' / f'bio-{kind}-{normalize_theme(key)}.svg').as_posix()


BIO_SURFACES = """
QMainWindow, QDialog { background: #090b09; }
QFrame#metal, QFrame#dock, QGroupBox, QFrame#lyricsPaper, QFrame#rewardCard {
    background: transparent; border: 12px solid transparent; border-radius: 0;
    border-image: url("__PANEL__") 18 18 18 18 stretch stretch;
}
QGroupBox { padding: 8px 2px 2px; }
QGroupBox::title { color: #c6f52b; background: #090b09; font: bold 9pt "Consolas"; }
QPushButton, QPushButton#primary, QPushButton#nav, QPushButton#score, QPushButton#lyricsButton {
    background: transparent; border: 8px solid transparent; border-radius: 0;
    border-image: url("__BUTTON__") 18 18 18 18 stretch stretch;
    padding: 2px 6px; color: #d8ddcb;
}
QPushButton#nav { text-align: left; padding: 6px; }
QPushButton#score, QPushButton#lyricsButton { border-width: 6px; padding: 1px 4px; }
QPushButton#primary, QPushButton#nav:checked, QPushButton#score:checked,
QPushButton:hover, QPushButton#nav:hover, QPushButton#score:hover, QPushButton#lyricsButton:hover {
    color: #c6f52b; font-weight: bold;
}
QPushButton:pressed, QPushButton#primary:pressed { color: #e7ffb3; padding-top: 3px; padding-bottom: 1px; }
QPushButton:disabled, QPushButton#primary:disabled, QPushButton#nav:disabled,
QPushButton#score:disabled, QPushButton#lyricsButton:disabled { color: #626b52; }
QLabel#brand { font: bold 25pt "Bahnschrift"; color: #e4e9d5; }
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox {
    background: #080c05; border: 1px solid #56623d; border-radius: 3px;
}
QTabBar::tab { border-radius: 2px; }
QSlider::groove:horizontal { height: 6px; background: #080c05; border: 1px solid #454d36; }
QSlider::handle:horizontal { width: 12px; border-radius: 2px; background: #414735; border: 1px solid #c6f52b; }
"""
