"""Find a lyrics block in a SoundCloud description, excluding release credits."""
import re
from html import unescape

HEADING = re.compile(r'^\s*[#*📝🎤\s]*(?:lyrics|текст(?:\s+(?:песни|трека))?|слова\s+песни)\s*(?::|：|[-—])?\s*$', re.I)
SECTION = re.compile(r'^\[?(?:verse|chorus|bridge|intro|outro|hook|куплет|припев|бридж)(?:\s+\d+)?\]?:?$', re.I)
JUNK = re.compile(r'^(?:(?:prod\.?|produced|music|lyrics|written|mixed|mastered|beat|cover\s+art)\s+by\b|(?:prod\.?|credits|сведение|мастеринг|музыка|автор|обложка|соцсети|контакты|follow|subscribe)\s*[:：]|(?:follow\s+(?:(?:me|us)\s+on|on|@)|subscribe\s+(?:to|on|@)|listen\s+on|stream\s+(?:on|now)|подписывай\w*|слушай\s+на)\b)', re.I)


def description_lyrics(description):
    if not isinstance(description, str):
        return ''
    lines = unescape(description[:60_000]).replace('\r\n','\n').splitlines()
    lines = [line.strip() for line in lines]
    explicit = False
    start = 0
    for index,line in enumerate(lines):
        if HEADING.fullmatch(line):
            start, explicit = index+1, True
            break
    if not explicit:
        for index,line in enumerate(lines):
            if SECTION.fullmatch(line):
                start, explicit = index, True
                break
    block = []
    for line in lines[start:]:
        if re.search(r'https?://|www\.|^@\w|^#\w', line, re.I) or JUNK.search(line):
            break
        # Decoration under a "Lyrics" heading is not part of the song.
        if re.fullmatch(r'[-—_=*\s]+', line) and line:
            if block:
                break
            continue
        block.append(line)
    text = '\n'.join(block).strip()
    # Use the same LRC cleaner as the reader, even for plain descriptions.
    from lyrics import plain_text
    text = plain_text({'plainLyrics': text})
    content = [line for line in text.splitlines() if line.strip() and not SECTION.fullmatch(line.strip())]
    if explicit:
        return text[:40_000] if len(content) >= 2 and sum(map(len,content)) >= 25 else ''
    # Unlabelled descriptions need a substantial block of short verse-like lines.
    if len(content) < 6 or sum(map(len,content)) < 120 or any(len(line) > 160 for line in content):
        return ''
    if sum(len(line.split()) >= 2 for line in content) < len(content)*.8:
        return ''
    return text[:40_000]


def description_text(description):
    if not isinstance(description,str):return ''
    from lyrics import plain_text
    text=unescape(description[:100_000]).replace('\r\n','\n').replace('\r','\n').replace('\x00','')
    return plain_text({'plainLyrics':text}).strip()


def lyrics_record(row,full=False):
    recognized=row.get('description_lyrics') or ''
    raw=row.get('soundcloud_description') or ''
    text = raw if full else recognized or raw
    if not text:
        return None
    from lyrics import song_query
    artist, track = song_query(row)
    return dict(id='soundcloud:'+row['id'], artistName=artist, trackName=track or row['title'],
                duration=row.get('duration') or 0, plainLyrics=text, instrumental=False,
                source='SoundCloud', sourceUrl=row['url'],descriptionFallback=bool(full or not recognized))
