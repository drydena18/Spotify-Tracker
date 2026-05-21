"""
Spotify Tracker — Local Web UI
Run with: python src/app.py
Then open: http://localhost:5000
"""

import os
import sqlite3
from flask import Flask, render_template_string, request, jsonify

app = Flask(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'db', 'spotipy.db')

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def query_db(sql, args=()):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def format_duration(ms):
    """Format milliseconds into a readable string."""
    if not ms:
        return '0 min'
    total_mins = int(ms) // 60000
    if total_mins < 60:
        return f'{total_mins:,} min'
    hours = total_mins // 60
    mins  = total_mins % 60
    if hours < 24:
        return f'{hours}h {mins}m'
    days = hours // 24
    hrs  = hours % 24
    return f'{days:,}d {hrs}h'


def format_hour(h):
    """Format a 0-23 hour integer as 12-hour AM/PM."""
    if h is None:
        return 'N/A'
    if h == 0:  return '12 AM'
    if h < 12:  return f'{h} AM'
    if h == 12: return '12 PM'
    return f'{h - 12} PM'


def get_stats():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    total         = c.execute('SELECT COUNT(*) FROM plays').fetchone()[0]
    total_ms      = c.execute('SELECT SUM(duration_ms) FROM plays').fetchone()[0] or 0
    unique_songs  = c.execute('SELECT COUNT(DISTINCT track_name || artist) FROM plays').fetchone()[0]
    unique_artists= c.execute('SELECT COUNT(DISTINCT artist) FROM plays').fetchone()[0]
    unique_albums = c.execute('SELECT COUNT(DISTINCT album) FROM plays').fetchone()[0]
    top_artist    = c.execute(
        'SELECT artist, COUNT(*) as n FROM plays GROUP BY artist ORDER BY n DESC LIMIT 1'
    ).fetchone()
    top_track     = c.execute(
        'SELECT track_name, artist, COUNT(*) as n FROM plays GROUP BY track_name, artist ORDER BY n DESC LIMIT 1'
    ).fetchone()
    top_album     = c.execute(
        'SELECT album, COUNT(*) as n FROM plays GROUP BY album ORDER BY n DESC LIMIT 1'
    ).fetchone()
    active_hour   = c.execute(
        "SELECT CAST(strftime('%H', played_at) AS INTEGER) as h, COUNT(*) as n "
        "FROM plays GROUP BY h ORDER BY n DESC LIMIT 1"
    ).fetchone()

    conn.close()
    return {
        'total'          : f'{total:,}',
        'total_time'     : format_duration(total_ms),
        'unique_songs'   : f'{unique_songs:,}',
        'unique_artists' : f'{unique_artists:,}',
        'unique_albums'  : f'{unique_albums:,}',
        'top_artist'     : dict(top_artist)  if top_artist  else None,
        'top_track'      : dict(top_track)   if top_track   else None,
        'top_album'      : dict(top_album)   if top_album   else None,
        'active_hour'    : format_hour(active_hour['h']) if active_hour else 'N/A',
    }


def get_chart_data():
    # Top 10 artists by play count
    top_artists = query_db(
        'SELECT artist, COUNT(*) as n FROM plays GROUP BY artist ORDER BY n DESC LIMIT 10'
    )

    # Plays by hour of day (0-23), fill missing hours with 0
    by_hour_raw = query_db(
        "SELECT CAST(strftime('%H', played_at) AS INTEGER) as h, COUNT(*) as n "
        "FROM plays GROUP BY h ORDER BY h"
    )
    hour_map   = {row['h']: row['n'] for row in by_hour_raw}
    by_hour    = [{'h': h, 'n': hour_map.get(h, 0)} for h in range(24)]

    # Plays by day of week (0=Sunday … 6=Saturday in SQLite)
    by_dow_raw = query_db(
        "SELECT CAST(strftime('%w', played_at) AS INTEGER) as d, COUNT(*) as n "
        "FROM plays GROUP BY d ORDER BY d"
    )
    dow_map    = {row['d']: row['n'] for row in by_dow_raw}
    dow_labels = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
    by_dow     = [{'d': dow_labels[d], 'n': dow_map.get(d, 0)} for d in range(7)]

    return {
        'top_artists': top_artists,
        'by_hour'    : by_hour,
        'by_dow'     : by_dow,
    }


# ------------------------------------------------------------------
# HTML Template
# ------------------------------------------------------------------

TEMPLATE = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Spotify Tracker</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Syne:wght@400;700;800&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  :root {
    --bg:        #0a0a0a;
    --surface:   #111111;
    --surface2:  #161616;
    --border:    #1e1e1e;
    --green:     #1db954;
    --green-dim: #0d3320;
    --text:      #e8e8e8;
    --muted:     #4a4a4a;
    --muted2:    #666;
  }

  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'Space Mono', monospace;
    font-size: 13px;
    min-height: 100vh;
  }

  /* ── Header ─────────────────────────────────────── */
  header {
    padding: 28px 40px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: baseline;
    gap: 16px;
  }

  header h1 {
    font-family: 'Syne', sans-serif;
    font-weight: 800;
    font-size: 26px;
    letter-spacing: -0.5px;
    color: var(--green);
  }

  header span { color: var(--muted2); font-size: 12px; }

  /* ── Stats Grid ──────────────────────────────────── */
  .stats-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    border-bottom: 1px solid var(--border);
  }

  .stat {
    padding: 20px 28px;
    border-right: 1px solid var(--border);
  }

  .stat:last-child { border-right: none; }

  .stat-label {
    font-size: 9px;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    color: var(--muted2);
    margin-bottom: 8px;
  }

  .stat-value {
    font-family: 'Syne', sans-serif;
    font-weight: 700;
    font-size: 20px;
    color: var(--text);
    line-height: 1;
  }

  .stat-value.green { color: var(--green); }

  .stat-sub {
    font-size: 10px;
    color: var(--muted2);
    margin-top: 5px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 180px;
  }

  /* ── Section Label ───────────────────────────────── */
  .section {
    padding: 32px 40px 0;
  }

  .section-title {
    font-family: 'Syne', sans-serif;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    color: var(--muted2);
    margin-bottom: 20px;
    padding-bottom: 10px;
    border-bottom: 1px solid var(--border);
  }

  /* ── Charts Row ──────────────────────────────────── */
  .charts-row {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    gap: 1px;
    background: var(--border);
    margin: 0 40px;
    border: 1px solid var(--border);
  }

  .chart-box {
    background: var(--surface);
    padding: 24px;
  }

  .chart-label {
    font-size: 10px;
    letter-spacing: 0.15em;
    text-transform: uppercase;
    color: var(--muted2);
    margin-bottom: 16px;
  }

  .chart-box canvas { width: 100% !important; }

  /* ── Tracks Table ────────────────────────────────── */
  .controls {
    padding: 20px 40px 0;
    display: flex;
    gap: 10px;
    align-items: center;
    flex-wrap: wrap;
  }

  input[type="text"] {
    background: var(--surface);
    border: 1px solid var(--border);
    color: var(--text);
    font-family: 'Space Mono', monospace;
    font-size: 12px;
    padding: 8px 14px;
    outline: none;
    width: 240px;
    transition: border-color 0.15s;
  }

  input[type="text"]:focus { border-color: var(--green); }
  input::placeholder { color: var(--muted); }

  select {
    background: var(--surface);
    border: 1px solid var(--border);
    color: var(--text);
    font-family: 'Space Mono', monospace;
    font-size: 12px;
    padding: 8px 12px;
    outline: none;
    cursor: pointer;
  }

  .count { color: var(--muted2); font-size: 11px; }

  .table-wrap {
    overflow-x: auto;
    padding: 16px 40px 16px;
  }

  table { width: 100%; border-collapse: collapse; }

  thead th {
    text-align: left;
    font-size: 9px;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--muted2);
    padding: 10px 14px;
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
    cursor: pointer;
    user-select: none;
    background: var(--bg);
    position: sticky;
    top: 0;
  }

  thead th:hover { color: var(--green); }
  thead th.sorted { color: var(--green); }
  thead th.sorted::after { content: ' ↕'; opacity: 0.6; }

  tbody tr {
    border-bottom: 1px solid var(--border);
    transition: background 0.08s;
  }

  tbody tr:hover { background: var(--surface); }

  td {
    padding: 10px 14px;
    white-space: nowrap;
    max-width: 200px;
    overflow: hidden;
    text-overflow: ellipsis;
    vertical-align: middle;
  }

  .null-cell { color: var(--muted); font-style: italic; }
  .track-col { font-weight: 700; }
  .artist-col { color: #999; }
  .dur-col { color: var(--muted2); font-variant-numeric: tabular-nums; }

  .genre-pill {
    display: inline-block;
    background: var(--green-dim);
    color: var(--green);
    padding: 2px 8px;
    font-size: 10px;
    border-radius: 2px;
    max-width: 160px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    vertical-align: middle;
  }

  /* ── Pagination ──────────────────────────────────── */
  .pagination {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 16px 40px 40px;
  }

  .page-btn {
    background: var(--surface);
    border: 1px solid var(--border);
    color: var(--text);
    font-family: 'Space Mono', monospace;
    font-size: 12px;
    padding: 6px 14px;
    cursor: pointer;
    transition: border-color 0.15s, color 0.15s;
  }

  .page-btn:hover:not(:disabled) { border-color: var(--green); color: var(--green); }
  .page-btn:disabled { opacity: 0.3; cursor: default; }

  .page-info { color: var(--muted2); font-size: 11px; flex: 1; text-align: center; }

  /* ── Loading / Empty ─────────────────────────────── */
  .table-status {
    padding: 40px;
    text-align: center;
    color: var(--muted2);
    font-size: 12px;
  }
</style>
</head>
<body>

<!-- Header -->
<header>
  <h1>⟨ spotipy ⟩</h1>
  <span>personal listening tracker</span>
</header>

<!-- Stats Grid -->
<div class="stats-grid">
  <div class="stat">
    <div class="stat-label">Total Plays</div>
    <div class="stat-value green">{{ stats.total }}</div>
  </div>
  <div class="stat">
    <div class="stat-label">Listening Time</div>
    <div class="stat-value">{{ stats.total_time }}</div>
  </div>
  <div class="stat">
    <div class="stat-label">Unique Songs</div>
    <div class="stat-value">{{ stats.unique_songs }}</div>
  </div>
  <div class="stat">
    <div class="stat-label">Unique Artists</div>
    <div class="stat-value">{{ stats.unique_artists }}</div>
  </div>
  <div class="stat">
    <div class="stat-label">Unique Albums</div>
    <div class="stat-value">{{ stats.unique_albums }}</div>
  </div>
  <div class="stat">
    <div class="stat-label">Peak Hour</div>
    <div class="stat-value">{{ stats.active_hour }}</div>
  </div>
  {% if stats.top_artist %}
  <div class="stat">
    <div class="stat-label">Top Artist</div>
    <div class="stat-value">{{ stats.top_artist.n }}</div>
    <div class="stat-sub">{{ stats.top_artist.artist }}</div>
  </div>
  {% endif %}
  {% if stats.top_track %}
  <div class="stat">
    <div class="stat-label">Most Replayed</div>
    <div class="stat-value">{{ stats.top_track.n }}×</div>
    <div class="stat-sub">{{ stats.top_track.track_name }}</div>
  </div>
  {% endif %}
</div>

<!-- Charts -->
<div class="section">
  <div class="section-title">Listening Patterns</div>
</div>
<div class="charts-row">
  <div class="chart-box">
    <div class="chart-label">Top Artists</div>
    <canvas id="chartArtists" height="220"></canvas>
  </div>
  <div class="chart-box">
    <div class="chart-label">By Hour of Day</div>
    <canvas id="chartHour" height="220"></canvas>
  </div>
  <div class="chart-box">
    <div class="chart-label">By Day of Week</div>
    <canvas id="chartDow" height="220"></canvas>
  </div>
</div>

<!-- Tracks Table -->
<div class="section" style="padding-top:32px;">
  <div class="section-title">Play History</div>
</div>

<div class="controls">
  <input type="text" id="search" placeholder="Search track, artist, album, genre…">
  <select id="sortCol">
    <option value="played_at">Sort: Date</option>
    <option value="track_name">Sort: Track</option>
    <option value="artist">Sort: Artist</option>
    <option value="album">Sort: Album</option>
    <option value="duration_ms">Sort: Duration</option>
    <option value="genre">Sort: Genre</option>
  </select>
  <select id="sortDir">
    <option value="desc">↓ Desc</option>
    <option value="asc">↑ Asc</option>
  </select>
  <span class="count" id="countLabel"></span>
</div>

<div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th data-col="played_at">Date</th>
        <th data-col="track_name">Track</th>
        <th data-col="artist">Artist</th>
        <th data-col="album">Album</th>
        <th data-col="duration_ms">Length</th>
        <th data-col="genre">Genre</th>
      </tr>
    </thead>
    <tbody id="tableBody">
      <tr><td colspan="6" class="table-status">Loading…</td></tr>
    </tbody>
  </table>
</div>

<div class="pagination">
  <button class="page-btn" id="btnPrev" onclick="changePage(-1)" disabled>← Prev</button>
  <span class="page-info" id="pageInfo"></span>
  <button class="page-btn" id="btnNext" onclick="changePage(1)">Next →</button>
</div>

<script>
// ── Chart.js global defaults ────────────────────────────────
Chart.defaults.color          = '#666';
Chart.defaults.borderColor    = '#1e1e1e';
Chart.defaults.font.family    = "'Space Mono', monospace";
Chart.defaults.font.size      = 11;

const GREEN     = '#1db954';
const GREEN_DIM = 'rgba(29,185,84,0.15)';
const chartData = {{ chart_data | tojson }};

// ── Top Artists (horizontal bar) ────────────────────────────
new Chart(document.getElementById('chartArtists'), {
  type: 'bar',
  data: {
    labels  : chartData.top_artists.map(r => r.artist).reverse(),
    datasets: [{
      data           : chartData.top_artists.map(r => r.n).reverse(),
      backgroundColor: GREEN_DIM,
      borderColor    : GREEN,
      borderWidth    : 1,
    }]
  },
  options: {
    indexAxis : 'y',
    plugins   : { legend: { display: false } },
    scales    : {
      x: { grid: { color: '#1a1a1a' }, ticks: { color: '#555' } },
      y: { grid: { display: false },   ticks: { color: '#aaa' } }
    }
  }
});

// ── By Hour of Day ──────────────────────────────────────────
const hourLabels = chartData.by_hour.map(r => {
  const h = r.h;
  if (h === 0)  return '12a';
  if (h < 12)   return h + 'a';
  if (h === 12) return '12p';
  return (h - 12) + 'p';
});

new Chart(document.getElementById('chartHour'), {
  type: 'bar',
  data: {
    labels  : hourLabels,
    datasets: [{
      data           : chartData.by_hour.map(r => r.n),
      backgroundColor: GREEN_DIM,
      borderColor    : GREEN,
      borderWidth    : 1,
    }]
  },
  options: {
    plugins: { legend: { display: false } },
    scales : {
      x: { grid: { display: false }, ticks: { color: '#555', maxRotation: 0 } },
      y: { grid: { color: '#1a1a1a' }, ticks: { color: '#555' } }
    }
  }
});

// ── By Day of Week ──────────────────────────────────────────
new Chart(document.getElementById('chartDow'), {
  type: 'bar',
  data: {
    labels  : chartData.by_dow.map(r => r.d),
    datasets: [{
      data           : chartData.by_dow.map(r => r.n),
      backgroundColor: GREEN_DIM,
      borderColor    : GREEN,
      borderWidth    : 1,
    }]
  },
  options: {
    plugins: { legend: { display: false } },
    scales : {
      x: { grid: { display: false }, ticks: { color: '#555' } },
      y: { grid: { color: '#1a1a1a' }, ticks: { color: '#555' } }
    }
  }
});

// ── Table (server-side pagination) ─────────────────────────
let currentPage  = 1;
let totalPages   = 1;
let totalRows    = 0;
let searchTimer  = null;

function formatDuration(ms) {
  if (ms == null) return null;
  const m = Math.floor(ms / 60000);
  const s = Math.floor((ms % 60000) / 1000).toString().padStart(2, '0');
  return `${m}:${s}`;
}

function formatDate(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  return d.toLocaleDateString('en-CA') + ' ' +
         d.toLocaleTimeString('en-CA', { hour: '2-digit', minute: '2-digit' });
}

function cell(val, cls = '') {
  if (val == null || val === '') return `<td class="null-cell ${cls}">—</td>`;
  return `<td class="${cls}">${val}</td>`;
}

function renderRows(rows) {
  if (rows.length === 0) {
    document.getElementById('tableBody').innerHTML =
      '<tr><td colspan="6" class="table-status">No tracks match your search.</td></tr>';
    return;
  }
  document.getElementById('tableBody').innerHTML = rows.map(r => `<tr>
    ${cell(formatDate(r.played_at))}
    <td class="track-col" title="${r.track_name || ''}">${r.track_name || '—'}</td>
    <td class="artist-col" title="${r.artist || ''}">${r.artist || '—'}</td>
    ${cell(r.album)}
    ${cell(formatDuration(r.duration_ms), 'dur-col')}
    ${r.genre ? `<td><span class="genre-pill" title="${r.genre}">${r.genre}</span></td>` : '<td class="null-cell">—</td>'}
  </tr>`).join('');
}

function updatePagination() {
  const start = totalRows === 0 ? 0 : (currentPage - 1) * 50 + 1;
  const end   = Math.min(currentPage * 50, totalRows);
  document.getElementById('pageInfo').textContent =
    totalRows === 0 ? 'No results' : `${start}–${end} of ${totalRows.toLocaleString()}`;
  document.getElementById('btnPrev').disabled = currentPage <= 1;
  document.getElementById('btnNext').disabled = currentPage >= totalPages;

  document.getElementById('countLabel').textContent =
    totalRows > 0 ? `${totalRows.toLocaleString()} track${totalRows !== 1 ? 's' : ''}` : '';
}

async function loadPage() {
  const q    = document.getElementById('search').value;
  const sort = document.getElementById('sortCol').value;
  const dir  = document.getElementById('sortDir').value;

  const url = `/api/plays?page=${currentPage}&q=${encodeURIComponent(q)}&sort=${sort}&dir=${dir}`;
  document.getElementById('tableBody').innerHTML =
    '<tr><td colspan="6" class="table-status">Loading…</td></tr>';

  const res  = await fetch(url);
  const data = await res.json();

  totalPages = data.pages;
  totalRows  = data.total;
  currentPage = data.page;

  // Update sorted column highlight
  document.querySelectorAll('thead th').forEach(th =>
    th.classList.toggle('sorted', th.dataset.col === sort));

  renderRows(data.rows);
  updatePagination();
}

function changePage(delta) {
  currentPage = Math.max(1, Math.min(totalPages, currentPage + delta));
  loadPage();
}

// Search with debounce
document.getElementById('search').addEventListener('input', () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { currentPage = 1; loadPage(); }, 300);
});

document.getElementById('sortCol').addEventListener('change', () => { currentPage = 1; loadPage(); });
document.getElementById('sortDir').addEventListener('change', () => { currentPage = 1; loadPage(); });

// Column header click to sort
document.querySelectorAll('thead th[data-col]').forEach(th => {
  th.addEventListener('click', () => {
    const col    = th.dataset.col;
    const colSel = document.getElementById('sortCol');
    const dirSel = document.getElementById('sortDir');
    dirSel.value = colSel.value === col && dirSel.value === 'desc' ? 'asc' : 'desc';
    colSel.value = col;
    currentPage  = 1;
    loadPage();
  });
});

// Initial load
loadPage();
</script>
</body>
</html>'''


# ------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------

@app.route('/')
def index():
    stats      = get_stats()
    chart_data = get_chart_data()
    return render_template_string(TEMPLATE, stats=stats, chart_data=chart_data)


@app.route('/api/plays')
def api_plays():
    page     = max(1, int(request.args.get('page', 1)))
    q        = request.args.get('q', '').strip()
    sort     = request.args.get('sort', 'played_at')
    dir_     = request.args.get('dir', 'desc')
    per_page = 50

    allowed = {'played_at', 'track_name', 'artist', 'album', 'duration_ms', 'genre'}
    if sort not in allowed:
        sort = 'played_at'
    order = 'DESC' if dir_ == 'desc' else 'ASC'

    if q:
        where = ("WHERE track_name LIKE ? OR artist LIKE ? "
                 "OR album LIKE ? OR genre LIKE ?")
        args  = [f'%{q}%'] * 4
    else:
        where = ''
        args  = []

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    total  = conn.execute(f'SELECT COUNT(*) FROM plays {where}', args).fetchone()[0]
    offset = (page - 1) * per_page
    rows   = conn.execute(
        f'SELECT * FROM plays {where} ORDER BY {sort} {order} LIMIT ? OFFSET ?',
        args + [per_page, offset]
    ).fetchall()
    conn.close()

    return jsonify({
        'rows'    : [dict(r) for r in rows],
        'total'   : total,
        'page'    : page,
        'pages'   : max(1, (total + per_page - 1) // per_page),
        'per_page': per_page,
    })


# ------------------------------------------------------------------
# Run
# ------------------------------------------------------------------

if __name__ == '__main__':
    print('\n  Spotify Tracker UI')
    print('  → http://localhost:5000\n')
    app.run(debug=False, port=5000)