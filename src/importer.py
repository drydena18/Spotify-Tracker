# ==================================================================
# Spotify Extended History Importer
# Imports StreamingHistory_music_*.json files from Spotify's
# "Extended streaming history" data export into the local db.
#
# Usage:
#   python src/importer.py path/to/StreamingHistory_music_0.json
#   python src/importer.py path/to/StreamingHistory_music_*.json
#
# Notes:
# - Entries under 30 seconds played are treated as skips and ignored
# - Podcast episodes are ignored (music tracks only)
# - Genre lookups via the Spotify API are only attempted for plays
#   on or after January 1, 2026 to keep runtime reasonable
# - Artist genres are cached in memory so the same artist is only
#   looked up once per run
# - Duplicate plays are detected by played_at timestamp. Because the
#   extended report uses second-precision while the live tracker uses
#   millisecond-precision timestamps, recent plays may appear in both
#   sources. A fuzzy duplicate check (same track within 60 seconds)
#   is used to catch these overlaps.
# ==================================================================

import json
import os
import sys
import sqlite3
import time
from datetime import datetime, timezone

import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv

# ------------------------------------------------------
# [CONFIG]
# ------------------------------------------------------
DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'db', 'spotipy.db')
LOOKUP_CUTOFF = datetime(2026, 1, 1, tzinfo = timezone.utc)
MIN_MS_PLAYED = 30000
API_DELAY = 0.12

# ------------------------------------------------------
# [AUTH]
# ------------------------------------------------------
load_dotenv()

sp = spotipy.Spotify(auth_manager = SpotifyOAuth(
    client_id = os.getenv('SPOTIPY_CLIENT_ID'),
    client_secret = os.getenv('SPOTIPY_CLIENT_SECRET'),
    redirect_uri = os.getenv('SPOTIPY_REDIRECT_URI'),
    scope = 'user-read-recently-played user-top-read',
    cache_path = os.path.join(os.path.dirname(__file__), '..', '.cache'),
    open_browser = False
))

# ------------------------------------------------------
# [CACHE] -- Artst genre cache (persists for the run)
# ------------------------------------------------------
genre_cache = {} # artist_id -> genre string or None
artist_cache = {} # track_id -> artist_id

# ------------------------------------------------------
# [FETCH] -- Resolve artist_id from track_id
# ------------------------------------------------------
def get_artist_id(track_id):
    if track_id in artist_cache:
        return artist_cache[track_id]
    try:
        track = sp.track(track_id)
        artist_id = track['artists'][0]['id']
        artist_cache[track_id] = artist_id
        time.sleep(API_DELAY)
        return artist_id
    except Exception as e:
        print(f'Track lookup error ({track_id}): {e}')
    return None

# ------------------------------------------------------
# [FETCH] -- Get genre for an artist (cached)
# ------------------------------------------------------
def get_genre(artist_id):
    if artist_id in genre_cache:
        return genre_cache[artist_id]
    try:
        artist = sp.artist(artist_id)
        genres = artist.get('genres', [])
        genre = ', '.join(genres) if genres else None
        genre_cache[artist_id] = genre
        time.sleep(API_DELAY)
        return genre
    except Exception as e:
        print(f'Genre lookup error ({artist_id}): {e}')
    return None

# ------------------------------------------------------
# [PARSE] -- Convert extended history timestamp to datetime
# ------------------------------------------------------
def parse_ts(ts_str):
    return datetime.fromisoformat(ts_str.replace('Z', '+00:00'))

# ------------------------------------------------------
# [IMPORT] -- Main import logic
# ------------------------------------------------------
def import_files(json_paths):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Load all existing played_at values for exact duplicate check
    existing_ts = set(
        row[0] for row in c.execute('SELECT played_at FROM plays').fetchall()
    )

    # Load existing plays for fuzzy duplciate check (track + timestamp)
    existing_plays = c.execute(
        "SELECT track_name, artist, strftime('%s', played_at) FROM plays"
    ).fetchall()
    existing_fuzzy = set(
        (name, artist, int(ts)) for name, artist, ts in existing_plays if ts
    )

    # Load all JSON files
    all_entries = []
    for path in json_paths:
        print(f'Reading {os.path.basename(path)}...')
        with open(path, 'r', encoding = 'utf-8') as f:
            all_entries.extend(json.load(f))

    print(f'\nLoaded {len(all_entries):,} total entries.\n')

    # Filter to valid music plays
    to_import = []
    skipped_episode = 0
    skipped_short = 0
    skipped_dupe = 0

    for entry in all_entries:
        uri = entry.get('spotify_track_uri') or ''

        # Skip podcast episodes
        if not uri.startswith('spotify:track:'):
            skipped_episode += 1
            continue

        # Skip short plays (likely skips)
        if entry.get('ms_played', 0) < MIN_MS_PLAYED:
            skipped_short += 1
            continue

        ts_str = entry.get('ts', '')
        track_name = entry.get('master_metadata_track_name') or ''
        artist_name = entry.get('master_metadata_album_artist_name') or ''

        # Exact dupe check
        if ts_str in existing_ts:
            skipped_dupe += 1
            continue

        # Fuzzy duplicate check (same track within 60 seconds)
        try:
            ts_epoch = int(parse_ts(ts_str).timestamp())
            is_dupe = any(
                name == track_name and artist == artist_name and abs(ts_epoch - t) < 60
                for name, artist, t in existing_fuzzy
            )
            if is_dupe:
                skipped_dupe += 1
                continue
        except Exception:
            pass

        to_import.append(entry)
 
    print(f'Filtering summary:')
    print(f'Episodes / non-music skipped : {skipped_episode:,}')
    print(f'Short plays (skips) ignored : {skipped_short:,}')
    print(f'Duplicate skipped : {skipped_dupe:,}')
    print(f'Tracks to import : {len(to_import):,}')

    if not to_import:
        print('\nNothing new to import.')
        conn.close()
        return
    
    needs_lookup = sum(
        1 for e in to_import
        if parse_ts(e['ts']) >= LOOKUP_CUTOFF
    )
    print(f'Genre lookups required : {needs_lookup:,} (plays since Jan 2026)\n')

    added = 0
    errors = 0

    for i, entry in enumerate(to_import, 1):
        ts_str = entry['ts']
        track_id = entry['spotify_track_uri'].split(':')[2]
        track_name = entry('master_metadata_track_name') or ''
        artist_name = entry('master_metadata_album_artist_name') or ''
        album_name = entry('master_metadata_album_album_name') or ''
        ms_played = entry.get('ms_played', 0)

        genre = None
        dt = parse_ts(ts_str)

        if dt >= LOOKUP_CUTOFF:
            artist_id = get_artist_id(track_id)
            if artist_id:
                genre = get_genre(artist_id)

        try:
            c.execute('''
                INSERT OR IGNORE INTO plays (
                    played_at, track_id, track_name, artist, album, duration_ms, genre
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (ts_str, track_id, track_name, artist_name, album_name, ms_played, genre))
            added += 1
        except Exception as e:
            print(f'Insert error: {e}')
            error += 1

        # Commit and report progress every 100 entries
        if i % 100 == 0:
            conn.commit()
            pct = (1 / len(to_import)) * 100
            print(f'[{pct:5.1f}%] {i:,} / {len(to_import):,} processed, {added:,} added...')

    conn.commit()
    conn.close()

    print(f'\nImport complete.')
    print(f'\nAdded : {added:,}')
    print(f'\nErrors : {errors:,}')

# ------------------------------------------------------
# [MAIN]
# ------------------------------------------------------
if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python src/importer.py path/to/StreamingHistory_music_0.json [more files...]')
        print('Example (all files at once): python src/importer.py data/StreamingHistory_music_*.json')
        sys.exit(1)

    import_files(sys.argv[1:])