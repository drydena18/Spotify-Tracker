# ==================================================================
# Spotify Extended History Importer
# Imports StreamingHistory_music_*.json files from Spotify's
# "Extended streaming history" data export into the local database.
#
# Usage:
#   python src/importer.py path/to/StreamingHistory_music_0.json
#   python src/importer.py path/to/StreamingHistory_music_*.json
#
# Notes:
#   - Entries under 30 seconds played are treated as skips and ignored
#   - Podcast episodes are ignored (music tracks only)
#   - Genre lookups via the Spotify API are only attempted for plays
#     on or after January 1, 2026 to keep API usage manageable
#   - Track and artist lookups are batched (50 at a time) to stay
#     well within Spotify's rate limits
#   - Duplicate plays are detected by played_at timestamp. Because the
#     extended export uses second-precision (e.g. 2026-05-21T12:43:38Z)
#     while the live tracker uses millisecond-precision timestamps
#     (e.g. 2026-05-21T12:43:38.170Z), recent plays may appear in both
#     sources. A fuzzy duplicate check (same track within 60 seconds)
#     is used to catch these overlaps.
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

# ---------------------------------------------------------
# [CONFIG]
# ---------------------------------------------------------
DB_PATH       = os.path.join(os.path.dirname(__file__), '..', 'db', 'spotipy.db')
LOOKUP_CUTOFF = datetime(2026, 1, 1, tzinfo=timezone.utc)
MIN_MS_PLAYED = 30000   # ignore plays under 30 seconds
BATCH_SIZE    = 50      # Spotify API max IDs per batch request
API_DELAY     = 0.2     # seconds between batch calls

# ---------------------------------------------------------
# [AUTH]
# ---------------------------------------------------------
load_dotenv()

sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    client_id     = os.getenv('SPOTIPY_CLIENT_ID'),
    client_secret = os.getenv('SPOTIPY_CLIENT_SECRET'),
    redirect_uri  = os.getenv('SPOTIPY_REDIRECT_URI'),
    scope         = 'user-read-recently-played user-top-read',
    cache_path    = os.path.join(os.path.dirname(__file__), '..', '.cache'),
    open_browser  = False
))

# ---------------------------------------------------------
# [BATCH] -- Fetch artist_id for a list of track_ids (50 at a time)
# Returns: dict of track_id -> artist_id
# ---------------------------------------------------------
def batch_get_artist_ids(track_ids):
    result = {}
    unique = list(set(track_ids))
    total  = len(unique)

    for i in range(0, total, BATCH_SIZE):
        chunk = unique[i:i + BATCH_SIZE]
        try:
            resp = sp.tracks(chunk)
            for track in resp.get('tracks') or []:
                if track and track.get('artists'):
                    result[track['id']] = track['artists'][0]['id']
        except Exception as e:
            print(f'  Track batch error (chunk {i}–{i+BATCH_SIZE}): {e}')
        pct = min(100, ((i + BATCH_SIZE) / total) * 100)
        print(f'  Track lookups: {pct:.0f}%', end='\r')
        time.sleep(API_DELAY)

    print()
    return result

# ---------------------------------------------------------
# [BATCH] -- Fetch genre for a list of artist_ids (50 at a time)
# Returns: dict of artist_id -> genre string or None
# ---------------------------------------------------------
def batch_get_genres(artist_ids):
    result = {}
    unique = list(set(artist_ids))
    total  = len(unique)

    for i in range(0, total, BATCH_SIZE):
        chunk = unique[i:i + BATCH_SIZE]
        try:
            resp = sp.artists(chunk)
            for artist in resp.get('artists') or []:
                if artist:
                    genres = artist.get('genres', [])
                    result[artist['id']] = ', '.join(genres) if genres else None
        except Exception as e:
            print(f'  Artist batch error (chunk {i}–{i+BATCH_SIZE}): {e}')
        pct = min(100, ((i + BATCH_SIZE) / total) * 100)
        print(f'  Artist/genre lookups: {pct:.0f}%', end='\r')
        time.sleep(API_DELAY)

    print()
    return result

# ---------------------------------------------------------
# [PARSE] -- Convert extended history timestamp to datetime
# ---------------------------------------------------------
def parse_ts(ts_str):
    return datetime.fromisoformat(ts_str.replace('Z', '+00:00'))

# ---------------------------------------------------------
# [IMPORT] -- Main import logic
# ---------------------------------------------------------
def import_files(json_paths):
    conn = sqlite3.connect(DB_PATH)
    c    = conn.cursor()

    # Load existing played_at values for exact duplicate check
    existing_ts = set(
        row[0] for row in c.execute('SELECT played_at FROM plays').fetchall()
    )

    # Load existing plays for fuzzy duplicate check
    existing_plays = c.execute(
        "SELECT track_name, artist, strftime('%s', played_at) FROM plays"
    ).fetchall()
    existing_fuzzy = set(
        (name, artist, int(ts))
        for name, artist, ts in existing_plays if ts
    )

    # Load all JSON files
    all_entries = []
    for path in json_paths:
        print(f'Reading {os.path.basename(path)}...')
        with open(path, 'r', encoding='utf-8') as f:
            all_entries.extend(json.load(f))

    print(f'\nLoaded {len(all_entries):,} total entries.\n')

    # Filter to valid, new music plays
    to_import       = []
    skipped_episode = 0
    skipped_short   = 0
    skipped_dupe    = 0

    for entry in all_entries:
        uri = entry.get('spotify_track_uri') or ''

        if not uri.startswith('spotify:track:'):
            skipped_episode += 1
            continue

        if entry.get('ms_played', 0) < MIN_MS_PLAYED:
            skipped_short += 1
            continue

        ts_str      = entry.get('ts', '')
        track_name  = entry.get('master_metadata_track_name') or ''
        artist_name = entry.get('master_metadata_album_artist_name') or ''

        if ts_str in existing_ts:
            skipped_dupe += 1
            continue

        # Fuzzy duplicate check (same track within 60 seconds)
        try:
            ts_epoch = int(parse_ts(ts_str).timestamp())
            if any(
                name == track_name and artist == artist_name and abs(ts_epoch - t) < 60
                for name, artist, t in existing_fuzzy
            ):
                skipped_dupe += 1
                continue
        except Exception:
            pass

        to_import.append(entry)

    print('Filtering summary:')
    print(f'  Episodes / non-music skipped : {skipped_episode:,}')
    print(f'  Short plays (skips) ignored  : {skipped_short:,}')
    print(f'  Duplicates skipped           : {skipped_dupe:,}')
    print(f'  Tracks to import             : {len(to_import):,}')

    if not to_import:
        print('\nNothing new to import.')
        conn.close()
        return

    # Split into entries needing lookup vs not
    lookup_entries = [e for e in to_import if parse_ts(e['ts']) >= LOOKUP_CUTOFF]
    no_lookup      = [e for e in to_import if parse_ts(e['ts']) <  LOOKUP_CUTOFF]

    print(f'  Genre lookups required       : {len(lookup_entries):,} (plays since Jan 2026)')
    print(f'  Imported without lookup      : {len(no_lookup):,}\n')

    # ── Batch API lookups ────────────────────────────────────
    track_id_to_artist = {}
    artist_id_to_genre = {}

    if lookup_entries:
        track_ids = [e['spotify_track_uri'].split(':')[2] for e in lookup_entries]

        print('Step 1/2 — Fetching artist IDs...')
        track_id_to_artist = batch_get_artist_ids(track_ids)

        artist_ids = list(set(track_id_to_artist.values()))
        print(f'Step 2/2 — Fetching genres for {len(artist_ids):,} unique artists...')
        artist_id_to_genre = batch_get_genres(artist_ids)

        print()

    # ── Insert all entries ───────────────────────────────────
    added  = 0
    errors = 0

    for entry in to_import:
        ts_str      = entry['ts']
        track_id    = entry['spotify_track_uri'].split(':')[2]
        track_name  = entry.get('master_metadata_track_name') or ''
        artist_name = entry.get('master_metadata_album_artist_name') or ''
        album_name  = entry.get('master_metadata_album_album_name') or ''
        ms_played   = entry.get('ms_played', 0)

        artist_id = track_id_to_artist.get(track_id)
        genre     = artist_id_to_genre.get(artist_id) if artist_id else None

        try:
            c.execute('''
                INSERT OR IGNORE INTO plays (
                    played_at, track_id, track_name, artist, album, duration_ms, genre
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (ts_str, track_id, track_name, artist_name, album_name, ms_played, genre))
            added += 1
        except Exception as e:
            print(f'  Insert error: {e}')
            errors += 1

    conn.commit()
    conn.close()

    print('Import complete.')
    print(f'  Added  : {added:,}')
    print(f'  Errors : {errors:,}')


# ---------------------------------------------------------
# [MAIN]
# ---------------------------------------------------------
if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python src/importer.py path/to/StreamingHistory_music_0.json')
        print('       python src/importer.py path/to/StreamingHistory_music_*.json')
        sys.exit(1)

    import_files(sys.argv[1:])