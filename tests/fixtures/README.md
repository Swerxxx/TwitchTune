These fixtures contain six seconds of generated silence, with no song audio.
They reproduce Mutagen FileType objects that have valid stream information but
are falsey because their tag mappings are empty.

Generated with FFmpeg from `anullsrc=r=44100:cl=mono`, duration 6 seconds:
MP3 uses libmp3lame at 32 kbps; FLAC uses the flac encoder. Metadata was removed
with Mutagen's `File(path).delete()`. Tests use the committed files directly;
FFmpeg is not required to run them or to download/play these formats.
