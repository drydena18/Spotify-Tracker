# ==================================================================
# Spotify Personal Tracker
# Polls Spotify every 30 minutes, stores new plays in SQLite
# ==================================================================

import os
import time
import sqlite3
import schedule
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv
from datetime import datetime

# ---------------------------------------------------------
# [CONFIG] - Edit if needed
# ---------------------------------------------------------
DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'db', 'spotipy.db')
POLL_MINUTES = 30
SCOPE = 'user-read-recently-played user-top-read'

# ---------------------------------------------------------
# [AUTH] -- Spotify Connection (do not edit)
# ---------------------------------------------------------
load_dotenv()

sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    client_id     = os.getenv('SPOTIPY_CLIENT_ID'),
    client_secret = os.getenv('SPOTIPY_CLIENT_SECRET'),
    redirect_uri  = os.getenv('SPOTIPY_REDIRECT_URI'),
    scope         = SCOPE,
    cache_path    = os.path.join(os.path.dirname(__file__), '..', '.cache'),
    open_browser  = False
))

# ---------------------------------------------------------
# [DATABASE] -- Create table / migrate if needed (do not edit)
# ---------------------------------------------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS plays (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            played_at   TEXT UNIQUE,
            track_id    TEXT,
            track_name  TEXT,
            artist      TEXT,
            album       TEXT,
            duration_ms INTEGER,
            genre       TEXT
        )
    ''')

    # Migration: add genre column if upgrading from an older schema
    existing = [row[1] for row in c.execute('PRAGMA table_info(plays)').fetchall()]
    if 'genre' not in existing:
        c.execute('ALTER TABLE plays ADD COLUMN genre TEXT')
        print('Migration: added genre column.')

    conn.commit()
    conn.close()
    print('Database initialized.')

# ---------------------------------------------------------
# [FETCH] -- Get artist genre (do not edit)
#
# Spotify attaches genre tags to artists, not individual
# tracks. This fetches the artist object and returns their
# genres as a comma-separated string, or None if unlisted.
# ---------------------------------------------------------
def get_genre(artist_id):
    try:
        artist = sp.artist(artist_id)
        genres = artist.get('genres', [])
        return ', '.join(genres) if genres else None
    except Exception as e:
        print(f'Genre fetch error for artist {artist_id}: {e}')
    return None

# ---------------------------------------------------------
# [POLL] -- Fetch recent plays and insert (do not edit)
# ---------------------------------------------------------
def poll():
    print(f'[{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}] Polling Spotify...')

    try:
        results = sp.current_user_recently_played(limit=50)
        items   = results['items']
        conn    = sqlite3.connect(DB_PATH)
        c       = conn.cursor()
        new     = 0

        for item in items:
            track     = item['track']
            played_at = item['played_at']
            track_id  = track['id']

            # Skip if already in database
            c.execute('SELECT id FROM plays WHERE played_at = ?', (played_at,))
            if c.fetchone():
                continue

            artist_id = track['artists'][0]['id']
            genre     = get_genre(artist_id)

            c.execute('''
                INSERT INTO plays (
                    played_at, track_id, track_name, artist, album, duration_ms, genre
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                played_at,
                track_id,
                track['name'],
                track['artists'][0]['name'],
                track['album']['name'],
                track['duration_ms'],
                genre,
            ))
            new += 1

        conn.commit()
        conn.close()
        print(f' Done. {new} track(s) added.')

    except Exception as e:
        print(f' Poll error: {e}')

# ---------------------------------------------------------
# [MAIN] -- Initialize and start scheduler (do not edit)
# ---------------------------------------------------------
if __name__ == '__main__':
    init_db()
    poll()

    schedule.every(POLL_MINUTES).minutes.do(poll)
    print(f'Scheduler running. Polling every {POLL_MINUTES} minutes. Ctrl + C to stop.')

    while True:
        schedule.run_pending()
        time.sleep(1)