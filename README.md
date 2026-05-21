# Spotify-Tracker

A personal Spotify listening tracker that polls your recent plays every 30 minutes, stores them in a local SQLite database, and serves a dashboard UI for browsing your history.

---

## Features

- Polls Spotify every 30 minutes and stores new plays automatically
- Fetches artist genre tags for each new play
- Local web dashboard with stats, charts, and a searchable/sortable play history table
- Importer for Spotify's extended streaming history data export
- No third-party analytics, no cloud, everything stays on your machine

---

## Project Structure

```
spotipy_tracker/
├── src/
│   ├── tracker.py      # Background poller — run this to collect plays
│   ├── app.py          # Local web dashboard (Flask)
│   └── importer.py     # One-time importer for Spotify data exports
├── db/
│   └── spotipy.db      # SQLite database (auto-created on first run)
├── .env                # Spotify credentials (see Setup)
├── .cache              # Spotify OAuth token cache (auto-created)
└── README.md
```

---

## Setup

### 1. Create a Spotify Developer App

1. Go to [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard) and create a new app
2. Set the **Redirect URI** to `https://127.0.0.1/callback`
3. Copy your **Client ID** and **Client Secret**

### 2. Configure your `.env`

Create a `.env` in the project root:

```env
SPOTIPY_CLIENT_ID = your_client_id_here
SPOTIPY_CLIENT_SECRET = your_client_secret_here
SPOTIPY_REDIRECT_URI = https://127.0.0.1/callback
```

### 3. Create a virtual environment and install dependencies

```bash
python3 -m venv venv
source venv/bin/activate    # Windows: venv\Scripts\activate

pip install spotipy python-dotenv schedule flask requests
```

### 4. Authenticate with Spotify

Run the tracker once to trigger the OAuth flow:

```bash
python src/tracker.py
```

Your browser will open a Spotify login page. After authorizing, you'll be redirected to localhost. Copy the full redirect URL and paste it into the terminal when prompted. A `.cache` file will be created and authentication won't be needed again.

---

## Usage

### Running the tracker

```bash
python src/tracker.py
```

Polls Spotify immediately on startup, then every 30 minutes. Keep this running in the background (or set it up as a cron job / launch service). Press `Ctrl+C` to stop.

### Running the dashboard

```bash
python src/app.py
```

Opens a local web server at [http://localhost:5000](http://localhost:5000). The tracker and dashboard can run at the same time.

### Importing your Spotify data export

Spotify lets you download your full extended streaming history (up to several years):

1. Go to **Spotify -> Settings -> Security and Privacy -> Download your data**
2. Request the **Extended streaming history** option. Standard history only covers 90 days.
3. Spotify typically delivers the export within a few days to a few weeks.
4. Once you receive the zip file, extract it and run:

```bash
python src/importer.py path/to/StreamingHistory_music_0.json
 
# Import multiple files at once
python src/importer.py path/to/StreamingHistory_music_*.json
```

The importer will:
- Skip podcast episodes and tracks played for under 30 seconds (skips)
- Detect and skip duplicated against existing database entries
- Attempt Spotify API genre lookups only for plays on or after January 1, 2026. Older plays are imported as-is to keep runtime manageable
- Print progress every 100 entries

---

## Dashboard

The dashboard at `http://localhost:5000` includes:

**Stats bar**
- Total plays, total listening time, unique songs, unique artists, unique albumns
- Peak listening hour, top artist, most replayed track

**Charts**
- Top 10 artists by play count
- Plays by hour of day
- Plays by day of week

**Play history table**
- Seachable across track, artist, album, and genre
- Sortable by any column (click headers)
- Paginated at 50 rows per page. Handles large imported histories without slowdown

---

## Notes

**Audio features (tempo, energy, valence, etc.) are unavailable.**
Spotify restricted access to their `audio_features` API endpoint in late 2024. Apps created after that point receive a `403 Forbidden` response. This affects BPM, energy, danceability, and all related fields. There is no free workaround for new apps.
 
**Genre availability varies.**
Spotify assigns genre tags at the artist level, not the track level. Smaller or newer artists often have no genres listed, in which case the genre field is stored as `NULL`.
 
**The tracker requires an active internet connection.**
If the tracker is offline when a poll is scheduled, that poll is skipped. Spotify's recently played endpoint only returns the last 50 plays, so extended downtime may result in gaps. The extended data export importer can be used to backfill any gaps once your export arrives.

---

## Cron / Autostart (optional)
 
To run the tracker automatically in the background on macOS, you can add it to your crontab:
 
```bash
crontab -e
```
 
```
*/30 * * * * cd /Users/you/Desktop/spotipy_tracker && venv/bin/python src/tracker.py >> logs/tracker.log 2>&1
```
 
Or create a `launchd` plist in `~/Library/LaunchAgents/` for a proper background service that starts on login.
