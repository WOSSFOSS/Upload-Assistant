# Book and Audiobook Upload Plan

Reference fork: https://github.com/wastaken7/Upload-Assistant

The `wastaken7/development` branch contains a broad book/audiobook implementation. We should use it as a reference, not merge it directly. The fork touches many unrelated areas, so the safer path is to integrate book support using the same isolated processor pattern as music uploads.

## Useful Reference Pieces

- `src/book_prep.py`: book/audiobook detection, EPUB/CBZ/CBR/PDF metadata, ISBN detection, audiobook duration and bitrate.
- `src/google_books.py`: ISBN lookup and local cache.
- `src/myanonamouse.py`: optional MAM lookup via existing torrent comments containing `MID=...`.
- `src/dupe_checking.py`: book-specific duplicate filtering, including ebook vs audiobook and ebook format matching.
- `src/trackers/CBR.py` and `src/trackers/BJS.py`: tracker-specific book/audiobook upload logic.
- `src/get_desc.py`, `src/get_name.py`, `src/uphelper.py`: confirmation, naming, and description examples.

## Intended Local Architecture

- Add a new `src/books.py` with a `BookProcessor`, similar to `src/music.py`.
- Detect book/audiobook early in `Prep.gather_prep()` and let the book processor own metadata, naming inputs, description, file list, and category setup.
- Avoid routing books through movie/TV screenshot, TMDB, language, or video description paths.
- Keep eBook preview images separate from video screenshots and only generate them where useful.
- Treat audiobooks like music-style uploads: no screenshots unless a tracker explicitly needs cover/proof images.

## Metadata Priority

1. CLI overrides
2. Optional MAM lookup
3. Google Books by ISBN
4. Local metadata from EPUB/PDF/CBZ/CBR/MediaInfo
5. Manual prompt in attended mode

MAM must remain optional. Users without a MAM account should still be able to upload books using local metadata, Google Books, or manual CLI overrides. MAM configuration should not be required and should never block the book processor.

## Provider Notes

- Google Books should be the default online lookup once an ISBN is available.
- Open Library is a good future fallback because it does not require a user account.
- MAM can be enabled via config only when a user has an account and wants MAM metadata:
  - `mam_api_key` or `mam_id`
  - environment fallback can be supported, but config should be explicit in docs.

## Tracker Scope For First Version

- ZNTH: map `BOOKS` and `AUDIOBOOKS` categories from its API/category ids.
- CBR/BJS: use the fork as reference for category/type/data payloads.
- UNIT3D base: support book descriptions and optional cover file hooks without breaking movie/TV/music.

## First Test Matrix

- EPUB with OPF metadata.
- PDF with ISBN detected from early/late pages.
- CBZ/CBR with `ComicInfo.xml`.
- MP3/M4B audiobook folder with embedded cover and duration.
- Book without online match, filled manually via CLI/prompt.
- Optional MAM case with an existing client torrent comment containing `MID=...`.
