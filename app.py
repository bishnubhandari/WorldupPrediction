import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime, timedelta
import hashlib
import os
import textwrap
import json
import re
import threading
import time as time_module
from urllib.request import urlopen, Request
from urllib.error import URLError
from html.parser import HTMLParser
from fpdf import FPDF
from PIL import Image
try:
    from streamlit_autorefresh import st_autorefresh
    _HAS_AUTOREFRESH = True
except ImportError:
    _HAS_AUTOREFRESH = False

# --- Page Config & Logo ---
logo_path = r"D:\University\worldcup_predictor\zRSSLZa7bC9pfCYAf7-DxA_64x64.png"
logo_img = "⚽"
if os.path.exists(logo_path):
    try:
        logo_img = Image.open(logo_path)
    except Exception:
        pass

st.set_page_config(
    page_title="FIFA World Cup 2026 — Score Prediction",
    page_icon=logo_img,
    layout="wide",
    initial_sidebar_state="expanded"
)

DB_PATH = "worldcup.db"


# --- Nepal timezone helpers ---
from datetime import datetime, timedelta, timezone

def get_nepal_time():
    try:
        # returns naive datetime in Nepal time (UTC + 5:45)
        return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=5, minutes=45))).replace(tzinfo=None)
    except:
        return (datetime.utcnow() + timedelta(hours=5, minutes=45)).replace(tzinfo=None)

def parse_to_nepal_time(date_str, time_str):
    time_str = time_str.strip()
    time_match = re.match(r"(\d{2}):(\d{2})", time_str)
    if not time_match:
        return datetime.strptime(f"{date_str} 12:00:00", "%Y-%m-%d %H:%M:%S")
    
    hour = int(time_match.group(1))
    minute = int(time_match.group(2))
    dt = datetime.strptime(f"{date_str} {hour:02d}:{minute:02d}:00", "%Y-%m-%d %H:%M:%S")
    
    offset_match = re.search(r"UTC([+-]\d+)(?::(\d{2}))?", time_str)
    offset_minutes = 0
    if offset_match:
        offset_hours = int(offset_match.group(1))
        offset_mins_part = int(offset_match.group(2)) if offset_match.group(2) else 0
        if offset_hours < 0:
            offset_minutes = offset_hours * 60 - offset_mins_part
        else:
            offset_minutes = offset_hours * 60 + offset_mins_part
    elif "UTC" in time_str:
        offset_minutes = 0
    else:
        offset_minutes = 0
        
    nepal_dt = dt - timedelta(minutes=offset_minutes) + timedelta(hours=5, minutes=45)
    return nepal_dt

# ============================================================
# --- Wikipedia Live Scraper ---
# ============================================================
WIKI_URL = "https://en.wikipedia.org/wiki/2026_FIFA_World_Cup"
WIKI_CACHE = {"html": None, "fetched_at": None}

def _fetch_wiki_html():
    """Fetch Wikipedia page HTML with a browser-like UA. Caches for 9 minutes."""
    now = datetime.utcnow()
    cached_at = WIKI_CACHE.get("fetched_at")
    if cached_at and (now - cached_at).total_seconds() < 540:  # 9 min cache
        return WIKI_CACHE["html"]
    try:
        req = Request(
            WIKI_URL,
            headers={"User-Agent": "Mozilla/5.0 (compatible; WorldCupBot/1.0; +https://example.com)"}
        )
        with urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        WIKI_CACHE["html"] = html
        WIKI_CACHE["fetched_at"] = now
        return html
    except Exception:
        return WIKI_CACHE.get("html")  # return stale on error


import ssl

def scrape_score_from_fifa_url(url):
    """
    Parses a FIFA match centre URL and fetches the score directly from the FIFA live API.
    Example URL: https://www.fifa.com/en/match-centre/match/17/285023/289273/400021457
    Returns: (dict_details, err_message)
    """
    try:
        pattern = r"match-centre/match/(\d+)/(\d+)/(\d+)/(\d+)"
        m = re.search(pattern, url)
        if not m:
            return None, "Invalid FIFA match URL structure. Make sure it contains competition/season/stage/match IDs."
        
        comp_id, season_id, stage_id, match_id = m.groups()
        api_url = f"https://api.fifa.com/api/v3/live/football/{comp_id}/{season_id}/{stage_id}/{match_id}"
        
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        
        req = Request(
            api_url,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urlopen(req, timeout=10, context=ctx) as resp:
            data = resp.read()
            parsed = json.loads(data.decode("utf-8"))
            
            home_names = parsed.get("HomeTeam", {}).get("TeamName", [])
            away_names = parsed.get("AwayTeam", {}).get("TeamName", [])
            
            home_team = home_names[0]["Description"] if home_names else "Home Team"
            away_team = away_names[0]["Description"] if away_names else "Away Team"
            
            home_score = parsed.get("HomeTeam", {}).get("Score")
            away_score = parsed.get("AwayTeam", {}).get("Score")
            
            period = parsed.get("Period")
            is_finished = (period in [5, 10]) if period is not None else False
            
            match_time = parsed.get("MatchTime", "")
            
            return {
                "home_team": home_team,
                "home_score": home_score,
                "away_team": away_team,
                "away_score": away_score,
                "is_finished": is_finished,
                "match_time": match_time
            }, None
    except Exception as e:
        return None, str(e)


def _clean_text(s):
    """Strip HTML tags and normalize whitespace/HTML entities."""
    s = re.sub(r'<[^>]+>', '', s)
    s = re.sub(r'\[\d+\]', '', s)   # remove citation refs like [1]
    s = s.replace('&nbsp;', ' ').replace('&#160;', ' ').replace('\u00a0', ' ')
    s = s.replace('&amp;', '&').replace('&#38;', '&')
    s = s.replace('&lt;', '<').replace('&gt;', '>')
    s = s.replace('&quot;', '"').replace('&#34;', '"')
    s = s.replace('&#39;', "'").replace('&apos;', "'")
    s = re.sub(r'\s+', ' ', s)
    return s.strip()


def parse_wiki_scores():
    """
    Parse football box scores from Wikipedia.
    Returns list of dicts: {home, away, score_a, score_b, status}
    status: 'FINISHED' | 'LIVE' | 'UPCOMING'
    """
    html = _fetch_wiki_html()
    if not html:
        return []
    
    matches = []
    # Wikipedia uses class="fevent" for match tables (contains fhome/faway/fscore cells)
    box_pattern = re.compile(
        r'<table[^>]*class="[^"]*fevent[^"]*"[^>]*>(.*?)</table>',
        re.DOTALL | re.IGNORECASE
    )
    score_pattern = re.compile(
        r'<th[^>]*class="[^"]*fscore[^"]*"[^>]*>(.*?)</th>',
        re.DOTALL | re.IGNORECASE
    )
    team_pattern = re.compile(
        r'<th[^>]*class="[^"]*fhome[^"]*"[^>]*>(.*?)</th>|<th[^>]*class="[^"]*faway[^"]*"[^>]*>(.*?)</th>',
        re.DOTALL | re.IGNORECASE
    )
    
    for box_m in box_pattern.finditer(html):
        box_html = box_m.group(1)
        
        # Extract teams
        team_matches = team_pattern.findall(box_html)
        teams = []
        for grp in team_matches:
            for g in grp:
                if g.strip():
                    t = _clean_text(g)
                    # Clean HTML entities
                    t = t.replace('&#39;', "'").replace('&quot;', '"')
                    t = t.replace('\u00a0', ' ').strip()
                    teams.append(t)
        if len(teams) < 2:
            continue
        home = teams[0]
        away = teams[1]
        
        # Skip placeholder entries (e.g. "Winner Group A")
        if 'winner' in home.lower() or 'runner' in home.lower() or 'group' in home.lower():
            continue
        if 'winner' in away.lower() or 'runner' in away.lower() or 'group' in away.lower():
            continue
        
        # Extract score
        score_m = score_pattern.search(box_html)
        score_text = _clean_text(score_m.group(1)) if score_m else ""
        
        # Parse score
        score_a, score_b, status = None, None, "UPCOMING"
        score_clean = re.sub(r'\(.*?\)', '', score_text).strip()  # remove pen. notation
        dash_match = re.match(r'^(\d+)[\u2013\u2014\-](\d+)$', score_clean)
        if dash_match:
            score_a = int(dash_match.group(1))
            score_b = int(dash_match.group(2))
            status = "FINISHED"
        elif re.search(r'live|in progress|\d+\'', score_text, re.IGNORECASE):
            status = "LIVE"
        
        matches.append({
            "home": home, "away": away,
            "score_a": score_a, "score_b": score_b,
            "status": status
        })
    
    return matches




def parse_wiki_group_tables():
    """
    Parse all group standing tables from Wikipedia.
    Returns dict: {group_name: [{team, mp, w, d, l, gf, ga, gd, pts}, ...]}
    """
    html = _fetch_wiki_html()
    if not html:
        return {}
    
    # Find group sections: <h3>Group A</h3> ... <table class="wikitable">...</table>
    group_section_pat = re.compile(
        r'<h[23][^>]*>[^<]*Group\s+([A-L])[^<]*</h[23]>(.*?)(?=<h[23]|$)',
        re.DOTALL | re.IGNORECASE
    )
    table_pat = re.compile(
        r'<table[^>]*class="[^"]*wikitable[^"]*"[^>]*>(.*?)</table>',
        re.DOTALL | re.IGNORECASE
    )
    row_pat = re.compile(r'<tr[^>]*>(.*?)</tr>', re.DOTALL | re.IGNORECASE)
    cell_pat = re.compile(r'<t[dh][^>]*>(.*?)</t[dh]>', re.DOTALL | re.IGNORECASE)
    
    groups = {}
    for grp_m in group_section_pat.finditer(html):
        grp_letter = grp_m.group(1).upper()
        section_html = grp_m.group(2)
        
        table_m = table_pat.search(section_html)
        if not table_m:
            continue
        table_html = table_m.group(1)
        
        rows = row_pat.findall(table_html)
        teams = []
        for row in rows[1:]:  # skip header
            cells_raw = cell_pat.findall(row)
            cells = [_clean_text(c) for c in cells_raw]
            if len(cells) < 9:
                continue
            # Wikipedia group tables: Pos | Team | Pld | W | D | L | GF | GA | GD | Pts
            pos_cell = cells[0].strip()
            # Pos cell is a digit (rank) or empty; skip header-like rows
            if not pos_cell.isdigit():
                continue
            # Team name is cells[1], clean non-breaking spaces and suffix notes like (H) (A)
            raw_team = cells[1]
            raw_team = raw_team.replace('\u00a0', ' ').replace('&#160;', '')
            raw_team = re.sub(r'\s*\(H(?:, A)?\)\s*', '', raw_team)  # remove (H) or (H, A)
            raw_team = raw_team.strip()
            if not raw_team:
                continue
            try:
                mp_val = cells[2] if len(cells) > 2 else "0"
                w_val  = cells[3] if len(cells) > 3 else "0"
                d_val  = cells[4] if len(cells) > 4 else "0"
                l_val  = cells[5] if len(cells) > 5 else "0"
                gf_val = cells[6] if len(cells) > 6 else "0"
                ga_val = cells[7] if len(cells) > 7 else "0"
                gd_val = cells[8] if len(cells) > 8 else "0"
                pts_val = cells[9] if len(cells) > 9 else "0"
                entry = {
                    "pos": int(pos_cell),
                    "team": raw_team,
                    "mp":  int(mp_val)  if mp_val.isdigit()  else 0,
                    "w":   int(w_val)   if w_val.isdigit()   else 0,
                    "d":   int(d_val)   if d_val.isdigit()   else 0,
                    "l":   int(l_val)   if l_val.isdigit()   else 0,
                    "gf":  int(gf_val)  if gf_val.isdigit()  else 0,
                    "ga":  int(ga_val)  if ga_val.isdigit()  else 0,
                    "gd":  gd_val.replace('\u2212', '-'),  # normalize minus sign
                    "pts": int(pts_val) if pts_val.isdigit() else 0,
                }
                teams.append(entry)
            except (IndexError, ValueError):
                pass
        if teams:
            groups[grp_letter] = teams
    
    return groups

def parse_wiki_goalscorers():
    """
    Parse the top goalscorers from Wikipedia's Goalscorers section.
    Wikipedia uses a div-col bulleted list grouped by goals count.
    Returns list of dicts: {player, team, goals} sorted by goals desc.
    """
    html = _fetch_wiki_html()
    if not html:
        return []
    
    # Find the Goalscorers section (everything between it and the next h2/h3)
    gs_idx = html.find('id="Goalscorers"')
    if gs_idx < 0:
        return []
    
    # Find the section end (next h2 or h3)
    section_end = re.search(r'<h[23][\s>]', html[gs_idx + 50:])
    gs_section = html[gs_idx: gs_idx + 50 + (section_end.start() if section_end else 20000)]
    
    scorers = []
    # Find goal-count groups: <b>N goals</b> or <b>N goal</b>
    goal_group_pat = re.compile(
        r'<[bp][^>]*>\s*(\d+)\s+goals?\s*</[bp]>(.*?)(?=<[bp][^>]*>\s*\d+\s+goals?|<h[23][\s>]|$)',
        re.DOTALL | re.IGNORECASE
    )
    # Extract player names from anchor tags within list items
    player_pat = re.compile(r'<li[^>]*>(.*?)</li>', re.DOTALL | re.IGNORECASE)
    link_pat = re.compile(r'<a[^>]*title="([^"]+)"[^>]*>', re.IGNORECASE)
    flag_pat = re.compile(r'<img[^>]*alt="([^"]+national football team[^"]*)"[^>]*/>', re.IGNORECASE)
    
    for goal_m in goal_group_pat.finditer(gs_section):
        goals = int(goal_m.group(1))
        block = goal_m.group(2)
        
        for li_m in player_pat.finditer(block):
            li_html = li_m.group(1)
            
            # Get team from flag image alt text
            flag_m = flag_pat.search(li_html)
            team = ""
            if flag_m:
                team_raw = flag_m.group(1)
                team = re.sub(r'\s*national football team.*$', '', team_raw, flags=re.IGNORECASE).strip()
                team = re.sub(r"\s*men's.*$", '', team, flags=re.IGNORECASE).strip()
            
            # Get player name from link (second link after flag, or any link)
            links = link_pat.findall(li_html)
            player = ""
            for lnk in links:
                # Skip flag/country links (they match team names)
                if 'national' in lnk.lower() or 'football team' in lnk.lower():
                    continue
                player = lnk.strip()
                break
            
            if not player:
                # Fallback: clean all HTML
                player = _clean_text(li_html)
                # Remove team name if present
                if team and player.startswith(team):
                    player = player[len(team):].strip()
            
            if player and len(player) < 60:
                scorers.append({"player": player, "team": team, "goals": goals})
    
    # Sort by goals descending, then player name
    scorers.sort(key=lambda x: (-x["goals"], x["player"]))
    return scorers


# --- Team name normalization for wiki->db matching ---
_WIKI_TO_DB = {
    "Czech Republic": "Czechia",
    "Czechia": "Czechia",
    "Ivory Coast": "Cote d'Ivoire",
    "Cote d'Ivoire": "Cote d'Ivoire",
    "Cura\u00e7ao": "Curacao",
    "DR Congo": "Congo DR",
    "United States": "United States",
    "USA": "United States",
    "South Korea": "Korea Republic",
    "Korea Republic": "Korea Republic",
    "Iran": "IR Iran",
    "IR Iran": "IR Iran",
    "Turkey": "Turkiye",
    "Turkiye": "Turkiye",
    "Bosnia and Herzegovina": "Bosnia and Herzegovina",
}

def _norm_wiki_name(name):
    return _WIKI_TO_DB.get(name, name)


def sync_scores_from_wiki():
    """
    Fetches matches from Wikipedia and updates scores in DB.
    - FINISHED matches get score + finished=1
    - LIVE matches get score only (finished=0)
    Returns (updated_count, errors)
    """
    wiki_matches = parse_wiki_scores()
    if not wiki_matches:
        return 0, "Could not fetch Wikipedia data"
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    updated = 0
    try:
        db_matches = conn.execute(
            "SELECT id, team_a, team_b, kickoff_time, finished, score_a, score_b FROM matches"
        ).fetchall()
        
        # Build lookup: (normalized_a, normalized_b) -> db row
        db_lookup = {}
        for row in db_matches:
            key = (row["team_a"].strip().lower(), row["team_b"].strip().lower())
            db_lookup[key] = row
        
        for wm in wiki_matches:
            norm_home = _norm_wiki_name(wm["home"])
            norm_away = _norm_wiki_name(wm["away"])
            key = (norm_home.lower(), norm_away.lower())
            
            db_row = db_lookup.get(key)
            if not db_row:
                continue  # no matching DB match
            
            if db_row["finished"] == 1:
                continue  # already finalized, skip
            
            if wm["status"] == "FINISHED" and wm["score_a"] is not None:
                conn.execute(
                    "UPDATE matches SET score_a=?, score_b=?, finished=1 WHERE id=?",
                    (wm["score_a"], wm["score_b"], db_row["id"])
                )
                updated += 1
            elif wm["status"] == "LIVE" and wm["score_a"] is not None:
                conn.execute(
                    "UPDATE matches SET score_a=?, score_b=?, finished=0 WHERE id=?",
                    (wm["score_a"], wm["score_b"], db_row["id"])
                )
                updated += 1
        
        conn.commit()
    finally:
        conn.close()
    
    return updated, None
def sync_live_fifa_scores():
    """
    Finds currently playing matches (kickoff is between now - 3 hrs and now)
    and updates their scores from the official FIFA Match Centre URL if set.
    Returns (updated_count, errors)
    """
    now_utc = datetime.utcnow()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    active_matches = []
    try:
        rows = conn.execute("SELECT * FROM matches WHERE finished = 0 AND fifa_url IS NOT NULL").fetchall()
        for r in rows:
            try:
                ko_dt = datetime.strptime(r['kickoff_time'], '%Y-%m-%d %H:%M:%S')
                ko_utc = ko_dt - timedelta(hours=5, minutes=45)
                elapsed = (now_utc - ko_utc).total_seconds()
                # Match is considered active from kickoff to 3 hours later
                if 0 <= elapsed <= 10800:
                    active_matches.append(r)
            except Exception:
                pass
    except Exception as e:
        conn.close()
        return 0, str(e)
    
    updated_count = 0
    errors = []
    for m in active_matches:
        try:
            res, err = scrape_score_from_fifa_url(m['fifa_url'])
            if err:
                errors.append(f"Match #{m['match_number']}: {err}")
            elif res:
                is_finished = 1 if res["is_finished"] else 0
                conn.execute(
                    "UPDATE matches SET score_a = ?, score_b = ?, finished = ?, match_time = ? WHERE id = ?",
                    (res["home_score"], res["away_score"], is_finished, res["match_time"], m["id"])
                )
                updated_count += 1
        except Exception as ex:
            errors.append(f"Match #{m['match_number']}: {ex}")
            
    conn.commit()
    conn.close()
    return updated_count, "; ".join(errors) if errors else None


# --- Smart Background Sync: FIFA Live during matches, Wikipedia post-match ---
_SYNC_LOCK = threading.Lock()
_LAST_SYNC = {"time": None, "updated": 0, "error": None, "reason": ""}


def _background_sync_loop():
    """
    Runs every 2 minutes:
    - If any match is currently playing, updates scores from FIFA live API.
    - Otherwise (outside match windows), syncs group tables and goalscorers from Wikipedia.
    """
    while True:
        try:
            time_module.sleep(120)
            
            # Check if any matches are active now
            now_utc = datetime.utcnow()
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            active_rows = conn.execute("SELECT id, team_a, team_b, kickoff_time, fifa_url FROM matches WHERE finished = 0").fetchall()
            conn.close()
            
            currently_live = []
            for r in active_rows:
                try:
                    ko_dt = datetime.strptime(r['kickoff_time'], '%Y-%m-%d %H:%M:%S')
                    ko_utc = ko_dt - timedelta(hours=5, minutes=45)
                    elapsed = (now_utc - ko_utc).total_seconds()
                    if 0 <= elapsed <= 10800:
                        currently_live.append(r)
                except Exception:
                    pass
            
            if currently_live:
                # During match: sync live score from FIFA API
                updated, err = sync_live_fifa_scores()
                reason = "Live FIFA scores: " + ", ".join(f"{m['team_a']} vs {m['team_b']}" for m in currently_live)
                with _SYNC_LOCK:
                    _LAST_SYNC["time"] = datetime.utcnow()
                    _LAST_SYNC["updated"] = updated
                    _LAST_SYNC["error"] = err
                    _LAST_SYNC["reason"] = reason
            else:
                # Post-match: sync standings and goalscorers from Wikipedia
                WIKI_CACHE["fetched_at"] = None  # force fresh fetch
                updated, err = sync_scores_from_wiki()
                with _SYNC_LOCK:
                    _LAST_SYNC["time"] = datetime.utcnow()
                    _LAST_SYNC["updated"] = updated
                    _LAST_SYNC["error"] = err
                    _LAST_SYNC["reason"] = "Wikipedia post-match sync"
        except Exception as e:
            with _SYNC_LOCK:
                _LAST_SYNC["error"] = str(e)


# Start background thread once (Streamlit reruns share the same process)
if "_wiki_sync_started" not in st.session_state:
    t = threading.Thread(target=_background_sync_loop, daemon=True)
    t.start()
    st.session_state["_wiki_sync_started"] = True

# On every page load: check if we just missed a sync cycle and have active live matches
with _SYNC_LOCK:
    _last_sync_time = _LAST_SYNC.get("time")

_now_utc = datetime.utcnow()
_conn_active = sqlite3.connect(DB_PATH)
_conn_active.row_factory = sqlite3.Row
_active_matches = _conn_active.execute("SELECT id, kickoff_time FROM matches WHERE finished = 0").fetchall()
_conn_active.close()

_live_now = False
for _m in _active_matches:
    try:
        _ko_dt = datetime.strptime(_m['kickoff_time'], '%Y-%m-%d %H:%M:%S')
        _ko_utc = _ko_dt - timedelta(hours=5, minutes=45)
        _elapsed = (_now_utc - _ko_utc).total_seconds()
        if 0 <= _elapsed <= 10800:
            _live_now = True
            break
    except Exception:
        pass

if _live_now:
    _stale = _last_sync_time is None or (datetime.utcnow() - _last_sync_time).total_seconds() > 120
    if _stale:
        _upd, _err = sync_live_fifa_scores()
        with _SYNC_LOCK:
            _LAST_SYNC["time"] = datetime.utcnow()
            _LAST_SYNC["updated"] = _upd
            _LAST_SYNC["error"] = _err
            _LAST_SYNC["reason"] = "Live FIFA load-sync"





# --- Password Hashing Helper ---
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def check_password(hashed, input_password):
    return hashed == hashlib.sha256(input_password.encode()).hexdigest()

# --- Database Initialization ---

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. Users Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT,
            display_name TEXT,
            is_active INTEGER DEFAULT 1
        )
    """)
    
    cursor.execute("PRAGMA table_info(users)")
    columns = [c[1] for c in cursor.fetchall()]
    if columns and "is_active" not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN is_active INTEGER DEFAULT 1")
        conn.commit()
    
    # 2. Matches Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_number INTEGER UNIQUE,
            team_a TEXT,
            team_b TEXT,
            group_name TEXT,
            stage TEXT,
            stadium TEXT,
            city TEXT,
            kickoff_time DATETIME,
            score_a INTEGER DEFAULT NULL,
            score_b INTEGER DEFAULT NULL,
            finished INTEGER DEFAULT 0,
            fifa_url TEXT DEFAULT NULL,
            match_time TEXT DEFAULT NULL
        )
    """)
    
    cursor.execute("PRAGMA table_info(matches)")
    m_columns = [c[1] for c in cursor.fetchall()]
    if m_columns:
        if "match_number" not in m_columns:
            cursor.execute("ALTER TABLE matches ADD COLUMN match_number INTEGER DEFAULT NULL")
        if "stage" not in m_columns:
            cursor.execute("ALTER TABLE matches ADD COLUMN stage TEXT DEFAULT NULL")
        if "stadium" not in m_columns:
            cursor.execute("ALTER TABLE matches ADD COLUMN stadium TEXT DEFAULT NULL")
        if "city" not in m_columns:
            cursor.execute("ALTER TABLE matches ADD COLUMN city TEXT DEFAULT NULL")
        if "fifa_url" not in m_columns:
            cursor.execute("ALTER TABLE matches ADD COLUMN fifa_url TEXT DEFAULT NULL")
        if "match_time" not in m_columns:
            cursor.execute("ALTER TABLE matches ADD COLUMN match_time TEXT DEFAULT NULL")
        conn.commit()
    
    # 3. Predictions Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS predictions (
            user_id INTEGER,
            match_id INTEGER,
            pred_score_a INTEGER,
            pred_score_b INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, match_id),
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (match_id) REFERENCES matches(id)
        )
    """)
    conn.commit()
    
    # Populate Default Users (only real ones + admin)
    default_users = [
        ("admin", hash_password("Wc2026!Adm#98"), "Tournament Admin"),
        ("aashish", hash_password("Predict!2026"), "Aashish Khadka"),
        ("aashish1", hash_password("Predict!2026"), "Aashish Khatri"),
        ("abhishek", hash_password("Predict!2026"), "Abhishek Karki"),
        ("amrit", hash_password("Predict!2026"), "Amrit Khatri"),
        ("anil", hash_password("Predict!2026"), "Anil Nepal"),
        ("bishnu", hash_password("Predict!2026"), "Bishnu Bhandari"),
        ("dinesh", hash_password("Predict!2026"), "Dinesh Karki"),
        ("drona", hash_password("Predict!2026"), "Drona Khatri"),
        ("hemant", hash_password("Predict!2026"), "Hemant Karki"),
        ("janak", hash_password("Predict!2026"), "Janak Ale"),
        ("kn", hash_password("Predict!2026"), "KN Shrestha"),
        ("kamal", hash_password("Predict!2026"), "Kamal Karki"),
        ("keshav", hash_password("Predict!2026"), "Keshav Gadtaula"),
        ("kumar", hash_password("Predict!2026"), "Kumar Ghimire"),
        ("lotus", hash_password("Predict!2026"), "Lotus Parajuli"),
        ("lushan", hash_password("Predict!2026"), "Lushan Shrestha"),
        ("madan", hash_password("Predict!2026"), "Madan Thapa"),
        ("mahesh", hash_password("Predict!2026"), "Mahesh Satyal"),
        ("nischal", hash_password("Predict!2026"), "Nischal Khanal"),
        ("nissan", hash_password("Predict!2026"), "Nissan Dhungana"),
        ("pawan", hash_password("Predict!2026"), "Pawan Bhatta"),
        ("prakash", hash_password("Predict!2026"), "Prakash Karki"),
        ("pushkar", hash_password("Predict!2026"), "Pushkar Bikram Moktan"),
        ("raaz", hash_password("Predict!2026"), "Raaz Kumar Bhujel"),
        ("rzes", hash_password("Predict!2026"), "Rzes Maharjan"),
        ("subaaz", hash_password("Predict!2026"), "Subaaz Bhattarai"),
        ("umesh", hash_password("Predict!2026"), "Umesh Bhattarai"),
        ("yukesh", hash_password("Predict!2026"), "Yukesh Thakuri")
    ]
    for username, password, display_name in default_users:
        cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
        if cursor.fetchone() is None:
            cursor.execute("INSERT INTO users (username, password, display_name, is_active) VALUES (?, ?, ?, 1)", (username, password, display_name))
        else:
            cursor.execute("UPDATE users SET display_name = ? WHERE username = ?", (display_name, username))
    conn.commit()
    
    # Populate matches from JSON if matches table is empty
    cursor.execute("SELECT COUNT(*) FROM matches")
    if cursor.fetchone()[0] == 0:
        api_json = "fixtures.json"
        local_json = "worldcup_group_stage.json"
        if os.path.exists(api_json):
            import json
            
            def clean_name(name):
                n = name.strip()
                if "Cura" in n: return "Curacao"
                if "Bosnia" in n: return "Bosnia and Herzegovina"
                if "Congo" in n or "DR" in n: return "Congo DR"
                if "Ivory" in n or "Ivoire" in n: return "Cote d'Ivoire"
                if "Czech" in n: return "Czechia"
                if "Iran" in n: return "IR Iran"
                if "Korea" in n: return "Korea Republic"
                if "Turkey" in n or "Turk" in n or "t\u00fcrk" in n.lower(): return "Turkiye"
                if "USA" in n or "United States" in n: return "United States"
                if "Cape Verde" in n or "Cabo Verde" in n: return "Cabo Verde"
                return n

            local_map = {}
            if os.path.exists(local_json):
                with open(local_json, "r", encoding="utf-8") as lf:
                    local_data = json.load(lf)
                    for lm in local_data:
                        t1 = clean_name(lm['team1'])
                        t2 = clean_name(lm['team2'])
                        score_data = lm.get('score')
                        score_a = None
                        score_b = None
                        finished = 0
                        if score_data and 'ft' in score_data:
                            score_a = score_data['ft'][0]
                            score_b = score_data['ft'][1]
                            finished = 1
                        local_map[(t1, t2)] = (score_a, score_b, finished)
                        local_map[(t2, t1)] = (score_a, score_b, finished)

            with open(api_json, "r", encoding="utf-8") as f:
                api_data = json.load(f)
                api_fixtures = api_data.get("fixtures", [])
            
            for f_item in api_fixtures:
                match_num = f_item["matchNumber"]
                team_a = f_item["homeTeam"]
                team_b = f_item["awayTeam"]
                group_name = f_item.get("group", "")
                stage = f_item["stage"]
                stadium = f_item["stadium"]
                city = f_item["hostCity"]
                
                # kickoffUtc to Nepal time
                utc_dt = datetime.strptime(f_item["kickoffUtc"], "%Y-%m-%dT%H:%M:%SZ")
                nep_dt = utc_dt + timedelta(hours=5, minutes=45)
                kickoff_time_str = nep_dt.strftime('%Y-%m-%d %H:%M:%S')
                
                score_a, score_b, finished = local_map.get((clean_name(team_a), clean_name(team_b)), (None, None, 0))
                
                cursor.execute(
                    """
                    INSERT INTO matches (match_number, team_a, team_b, group_name, stage, stadium, city, kickoff_time, score_a, score_b, finished)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (match_num, team_a, team_b, group_name, stage, stadium, city, kickoff_time_str, score_a, score_b, finished)
                )
            conn.commit()
            
    # Seed specific FIFA URLs
    cursor.execute("""
        UPDATE matches 
        SET fifa_url = 'https://www.fifa.com/en/match-centre/match/17/285023/289273/400021457' 
        WHERE match_number = 29 AND fifa_url IS NULL
    """)
    cursor.execute("""
        UPDATE matches 
        SET fifa_url = 'https://www.fifa.com/en/match-centre/match/17/285023/289273/400021460' 
        WHERE match_number = 31 AND fifa_url IS NULL
    """)
    conn.commit()
    conn.close()

init_db()

# --- Database Query Helpers ---
def get_user_by_username(username):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    return user

def get_user_prediction(user_id, match_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    pred = conn.execute("SELECT * FROM predictions WHERE user_id = ? AND match_id = ?", (user_id, match_id)).fetchone()
    conn.close()
    return pred

def get_predictions_summary(match_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    preds = conn.execute("""
        SELECT u.display_name, p.pred_score_a, p.pred_score_b 
        FROM predictions p
        JOIN users u ON p.user_id = u.id
        WHERE p.match_id = ? AND u.username != 'admin'
    """, (match_id,)).fetchall()
    conn.close()
    return preds

def submit_prediction(user_id, match_id, score_a, score_b):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            INSERT OR REPLACE INTO predictions (user_id, match_id, pred_score_a, pred_score_b) 
            VALUES (?, ?, ?, ?)
        """, (user_id, match_id, score_a, score_b))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        st.error(f"Error submitting prediction: {e}")
        return False

# --- Points & Pool Helpers ---
def calculate_points(pred_a, pred_b, act_a, act_b):
    if act_a is None or act_b is None:
        return 0
    if pred_a == act_a and pred_b == act_b:
        return 3 # Exact score indicator
    return 0

def get_match_pool_and_payout(target_match_id):
    conn = sqlite3.connect(DB_PATH)
    matches = conn.execute("SELECT id, score_a, score_b, finished FROM matches ORDER BY kickoff_time ASC, id ASC").fetchall()
    preds = conn.execute("SELECT user_id, match_id, pred_score_a, pred_score_b FROM predictions").fetchall()
    conn.close()
    
    preds_by_match = {}
    for p in preds:
        uid, mid, sa, sb = p
        if mid not in preds_by_match:
            preds_by_match[mid] = []
        preds_by_match[mid].append({"user_id": uid, "pred_score_a": sa, "pred_score_b": sb})
        
    carry_forward = 0.0
    
    for m in matches:
        mid, act_a, act_b, finished = m
        match_preds = preds_by_match.get(mid, [])
        num_predictors = len(match_preds)
        base_pool = num_predictors * 100
        total_pool = base_pool + carry_forward
        
        if mid == target_match_id:
            winners = []
            if finished == 1:
                for p in match_preds:
                    if p["pred_score_a"] == act_a and p["pred_score_b"] == act_b:
                        winners.append(p["user_id"])
            payout = total_pool / len(winners) if len(winners) > 0 else 0.0
            outgoing = 0.0 if len(winners) > 0 or finished == 0 else total_pool
            return {
                "incoming_carry": carry_forward,
                "base_pool": base_pool,
                "total_pool": total_pool,
                "winners_count": len(winners),
                "winners": winners,
                "payout": payout,
                "outgoing_carry": outgoing
            }
            
        if finished == 1:
            winners = []
            for p in match_preds:
                if p["pred_score_a"] == act_a and p["pred_score_b"] == act_b:
                    winners.append(p["user_id"])
            if len(winners) > 0:
                carry_forward = 0.0
            else:
                carry_forward = total_pool
                
    return {
        "incoming_carry": 0.0,
        "base_pool": 0.0,
        "total_pool": 0.0,
        "winners_count": 0,
        "winners": [],
        "payout": 0.0,
        "outgoing_carry": 0.0
    }

def get_leaderboard():
    conn = sqlite3.connect(DB_PATH)
    users = conn.execute("SELECT id, display_name, username FROM users WHERE username != 'admin'").fetchall()
    matches = conn.execute("SELECT id, score_a, score_b, finished FROM matches ORDER BY kickoff_time ASC, id ASC").fetchall()
    preds = conn.execute("SELECT user_id, match_id, pred_score_a, pred_score_b FROM predictions").fetchall()
    conn.close()
    
    preds_by_match = {}
    for p in preds:
        uid, mid, sa, sb = p
        if mid not in preds_by_match:
            preds_by_match[mid] = []
        preds_by_match[mid].append({"user_id": uid, "pred_score_a": sa, "pred_score_b": sb})
        
    user_stats = {}
    for row in users:
        uid, name, uname = row
        user_stats[uid] = {
            "display_name": name,
            "username": uname,
            "pred_count": 0,
            "exact_wins": 0,
            "points_won": 0.0,
            "points_cost": 0,
            "points": 0.0
        }
        
    carry_forward = 0.0
    
    for m in matches:
        mid, act_a, act_b, finished = m
        match_preds = preds_by_match.get(mid, [])
        
        for p in match_preds:
            uid = p["user_id"]
            if uid in user_stats:
                user_stats[uid]["pred_count"] += 1
                user_stats[uid]["points_cost"] += 100
                
        num_predictors = len(match_preds)
        base_pool = num_predictors * 100
        total_pool = base_pool + carry_forward
        
        if finished == 1:
            winners = []
            for p in match_preds:
                if p["pred_score_a"] == act_a and p["pred_score_b"] == act_b:
                    winners.append(p["user_id"])
                    
            if len(winners) > 0:
                pts_per_winner = total_pool / len(winners)
                for wid in winners:
                    if wid in user_stats:
                        user_stats[wid]["points_won"] += pts_per_winner
                        user_stats[wid]["exact_wins"] += 1
                carry_forward = 0.0
            else:
                carry_forward = total_pool
                
    for uid in user_stats:
        user_stats[uid]["points"] = user_stats[uid]["points_won"] - user_stats[uid]["points_cost"]
        
    lb_list = list(user_stats.values())
    lb_list.sort(key=lambda x: (x["points_won"], x["exact_wins"]), reverse=True)
    return lb_list

def get_team_emoji(team_name):
    emojis = {
        "Canada": "🇨🇦", "Qatar": "🇶🇦", "Mexico": "🇲🇽", "South Korea": "🇰🇷",
        "Czechia": "🇨🇿", "South Africa": "🇿🇦", "Switzerland": "🇨🇭", "Bosnia": "🇧🇦",
        "Uzbekistan": "🇺🇿", "Colombia": "🇨🇴", "Scotland": "🏴󠁧󠁢󠁳󠁣󠁴󠁿", "Morocco": "🇲🇦",
        "Brazil": "🇧🇷", "Haiti": "🇭🇹", "USA": "🇺🇸", "Australia": "🇦🇺",
        "Türkiye": "🇹🇷", "Paraguay": "🇵🇾", "Netherlands": "🇳🇱", "Sweden": "🇸🇪",
        "Germany": "🇩🇪", "Côte d'Ivoire": "🇨🇮", "Ecuador": "🇪🇨", "Curaçao": "🇨🇼",
        "Tunisia": "🇹🇳", "Japan": "🇯🇵", "Czech Republic": "🇨🇿", "Turkey": "🇹🇷",
        "Argentina": "🇦🇷", "France": "🇫🇷", "England": "🏴󠁧󠁢󠁥󠁮󠁧󠁿", "Spain": "🇪🇸",
        "Portugal": "🇵🇹", "Italy": "🇮🇹", "Uruguay": "🇺🇾", "Belgium": "🇧🇪",
        "Croatia": "🇭🇷", "Senegal": "🇸🇳", "Saudi Arabia": "🇸🇦", "Denmark": "🇩🇰",
        "Serbia": "🇷🇸", "Poland": "🇵🇱", "Ghana": "🇬🇭", "Cameroon": "🇨🇲",
        "Costa Rica": "🇨🇷", "Iran": "🇮🇷", "Wales": "🏴󠁧󠁢󠁷󠁬󠁳󠁿", "Ukraine": "🇺🇦"
    }
    return emojis.get(team_name, "🏳️")

def generate_match_pdf(match, predictions):
    # Convert sqlite3.Row to dict to allow dictionary method access like .get()
    try:
        match = dict(match)
    except Exception:
        pass
        
    pdf = FPDF()
    pdf.add_page()
    
    # Navy Background Header
    pdf.set_fill_color(15, 23, 42)
    pdf.rect(0, 0, 210, 40, 'F')
    
    # Title
    pdf.set_text_color(251, 191, 36)
    pdf.set_font("helvetica", style="B", size=22)
    pdf.cell(190, 15, text="FIFA WORLD CUP 2026", ln=True, align="C")
    
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("helvetica", style="B", size=12)
    pdf.cell(190, 8, text="OFFICIAL MATCH REPORT & PREDICTIONS SUMMARY", ln=True, align="C")
    
    pdf.ln(15)
    
    # Match Details Info Block
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("helvetica", style="B", size=14)
    team_a = match['team_a']
    team_b = match['team_b']
    score_a = match['score_a']
    score_b = match['score_b']
    group = match['group_name']
    kickoff = match['kickoff_time']
    mid = match['id']
    match_num = match.get('match_number', mid)
    stage = match.get('stage', 'group-stage')
    stadium = match.get('stadium', 'N/A')
    city = match.get('city', 'N/A')
    
    pool_details = get_match_pool_and_payout(mid)
    
    pdf.cell(190, 8, text=f"{team_a} vs {team_b}", ln=True)
    pdf.set_font("helvetica", style="", size=11)
    stage_lbl = f"Group {group}" if group else stage.replace("-", " ").title()
    pdf.cell(190, 6, text=f"Match #{match_num} | Stage: {stage_lbl} | Venue: {stadium} ({city.replace('-', ' ').title()})", ln=True)
    pdf.cell(190, 6, text=f"Kickoff: {kickoff} NPT", ln=True)
    
    if score_a is not None and score_b is not None:
        winner = team_a if score_a > score_b else team_b if score_a < score_b else "Draw"
        winner_text = f"Winner: {winner}" if winner != "Draw" else "Result: Draw Match"
        pdf.set_font("helvetica", style="B", size=12)
        pdf.cell(190, 8, text=f"Final Score: {team_a} {score_a} - {score_b} {team_b} ({winner_text})", ln=True)
        
        pdf.set_font("helvetica", style="", size=10)
        pdf.cell(190, 6, text=f"Total Match Pool: {pool_details['total_pool']:.0f} pts (Rollover: {pool_details['incoming_carry']:.0f} pts | Base: {pool_details['base_pool']:.0f} pts)", ln=True)
        pdf.cell(190, 6, text=f"Winners Count: {pool_details['winners_count']} | Payout: {pool_details['payout']:.1f} pts per winner", ln=True)
        if pool_details['winners_count'] == 0:
            pdf.cell(190, 6, text=f"Carryover Rollover: {pool_details['outgoing_carry']:.0f} pts (Doubled & carried forward)", ln=True)
    else:
        pdf.set_font("helvetica", style="I", size=11)
        pdf.cell(190, 8, text="Status: Live / Ongoing (Score not finalized)", ln=True)
        
    pdf.ln(8)
    
    # Table Header
    pdf.set_fill_color(251, 191, 36)
    pdf.set_text_color(15, 23, 42)
    pdf.set_font("helvetica", style="B", size=10)
    
    col_name = 65
    col_pred = 45
    col_pts = 35
    col_status = 45
    
    pdf.cell(col_name, 8, text="Predictor Name", border=1, fill=True, align="C")
    pdf.cell(col_pred, 8, text="Predicted Score", border=1, fill=True, align="C")
    pdf.cell(col_pts, 8, text="Points Awarded", border=1, fill=True, align="C")
    pdf.cell(col_status, 8, text="Prediction Status", border=1, fill=True, align="C")
    pdf.ln()
    
    # Table Rows
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("helvetica", style="", size=10)
    
    row_count = 0
    for p in predictions:
        fill = row_count % 2 == 1
        pdf.set_fill_color(241, 245, 249)
        
        pdf.cell(col_name, 7, text=p['display_name'], border=1, fill=fill, align="L")
        pdf.cell(col_pred, 7, text=f"{p['pred_score_a']} - {p['pred_score_b']}", border=1, fill=fill, align="C")
        
        pts = 0.0
        status_str = "Match Pending"
        if score_a is not None and score_b is not None:
            is_winner = (p['pred_score_a'] == score_a and p['pred_score_b'] == score_b)
            if is_winner:
                pts = pool_details['payout']
                status_str = f"Exact Win (+{pts:.1f})"
            else:
                pts = 0.0
                status_str = "Incorrect (0 pts)"
                
        pdf.cell(col_pts, 7, text=f"{pts:.1f}", border=1, fill=fill, align="C")
        pdf.cell(col_status, 7, text=status_str, border=1, fill=fill, align="C")
        pdf.ln()
        row_count += 1
        
    if not predictions:
        pdf.cell(190, 8, text="No predictions submitted for this match.", border=1, align="C")
        pdf.ln()
        
    pdf.ln(10)
    pdf.set_font("helvetica", style="I", size=8)
    pdf.set_text_color(128, 128, 128)
    pdf.cell(190, 5, text="Report generated automatically by FIFA World Cup 2026 Score Predictor app.", align="C")
    
    return bytes(pdf.output())

# --- FIFA Theme Custom CSS ---
st.html("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800;900&display=swap');

    /* Qatar 2026 World Cup Theme Overhaul */
    .stApp {
        background: radial-gradient(circle at 50% 50%, #0d1b3e 0%, #050b18 100%) !important;
        color: #f1f5f9 !important;
        font-family: 'Outfit', sans-serif !important;
    }
    
    /* Remove default Streamlit top padding to reduce top gaps */
    .block-container {
        padding-top: 1.0rem !important;
        padding-bottom: 1.0rem !important;
    }
    
    .worldcup-title {
        text-align: center;
        background: linear-gradient(90deg, #fbbf24 0%, #d97706 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        font-size: 2.2rem;
        font-weight: 800;
        margin-top: 0px !important;
        margin-bottom: 2px;
        letter-spacing: 1px;
    }
    
    .worldcup-subtitle {
        text-align: center;
        color: #fbbf24;
        font-size: 0.95rem;
        font-weight: 500;
        margin-bottom: 6px;
        text-transform: uppercase;
        letter-spacing: 3px;
    }
    
    .server-time {
        text-align: center;
        font-size: 0.8rem;
        color: #cbd5e1;
        margin-bottom: 10px;
    }
    
    /* Glassmorphism Sports Match Cards */
    .match-card {
        background: rgba(13, 27, 62, 0.45) !important;
        backdrop-filter: blur(12px) !important;
        -webkit-backdrop-filter: blur(12px) !important;
        border: 1px solid rgba(251, 191, 36, 0.2) !important;
        border-radius: 16px !important;
        padding: 20px !important;
        margin-bottom: 18px;
        box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37) !important;
        transition: all 0.3s cubic-bezier(0.25, 0.8, 0.25, 1) !important;
    }
    .match-card:hover {
        border-color: rgba(251, 191, 36, 0.6) !important;
        box-shadow: 0 12px 40px 0 rgba(251, 191, 36, 0.2) !important;
        transform: translateY(-2px) !important;
    }
    
    .card-meta {
        display: flex;
        justify-content: space-between;
        font-size: 0.8rem;
        color: #cbd5e1;
        border-bottom: 1px solid rgba(255,255,255,0.05);
        padding-bottom: 8px;
        margin-bottom: 14px;
    }
    
    .teams-grid {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 15px;
    }
    
    .team-block {
        flex: 1;
        text-align: center;
        font-weight: 600;
        font-size: 1.15rem;
        color: #f8fafc;
    }
    .team-flag {
        font-size: 2.2rem;
        display: block;
        margin-bottom: 6px;
    }
    
    .middle-block {
        display: flex;
        flex-direction: column;
        align-items: center;
        padding: 0 15px;
    }
    
    .vs-badge {
        padding: 4px 14px;
        background: rgba(251, 191, 36, 0.1);
        border: 1px solid #fbbf24;
        border-radius: 20px;
        color: #fbbf24;
        font-weight: 800;
        font-size: 0.8rem;
    }
    
    .score-display {
        font-size: 1.9rem;
        font-weight: 900;
        color: #f8fafc;
        letter-spacing: 5px;
    }
    
    .card-footer {
        font-size: 0.85rem;
        border-top: 1px solid rgba(255,255,255,0.05);
        padding-top: 10px;
        margin-top: 12px;
    }
    
    .badge-status {
        display: inline-block;
        padding: 3px 10px;
        border-radius: 6px;
        font-size: 0.72rem;
        font-weight: 800;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    .badge-open { background: rgba(16, 185, 129, 0.15) !important; color: #10b981 !important; border: 1px solid #10b981 !important; }
    .badge-locked { background: rgba(245, 158, 11, 0.15) !important; color: #f59e0b !important; border: 1px solid #f59e0b !important; }
    .badge-finished { background: rgba(203, 213, 225, 0.15) !important; color: #cbd5e1 !important; border: 1px solid #cbd5e1 !important; }
    
    /* Premium Standings & Leaderboard Table */
    .table-leaderboard {
        width: 100%;
        border-collapse: separate !important;
        border-spacing: 0 6px !important;
        margin-top: 15px;
    }
    .table-leaderboard th {
        background: linear-gradient(90deg, #fbbf24 0%, #d97706 100%) !important;
        color: #050b18 !important;
        padding: 14px !important;
        font-weight: 800 !important;
        text-transform: uppercase;
        font-size: 0.85rem;
        letter-spacing: 1px;
        border: none !important;
    }
    .table-leaderboard td {
        padding: 12px 14px !important;
        background-color: rgba(13, 27, 62, 0.5) !important;
        color: #cbd5e1;
        border-top: 1px solid rgba(255,255,255,0.03) !important;
        border-bottom: 1px solid rgba(255,255,255,0.03) !important;
        border-left: none !important;
        border-right: none !important;
        font-size: 0.95rem;
    }
    .table-leaderboard td:first-child,
    .table-leaderboard td:first-child b {
        color: #f8fafc !important; /* Force predictor name to be off-white */
    }
    .table-leaderboard tr:hover td {
        background-color: rgba(251, 191, 36, 0.1) !important;
        color: #f8fafc !important;
    }
    
    /* Primary & Secondary Buttons overrides */
    button[data-testid^="stBaseButton-primary"],
    button[data-testid^="stBaseButton-formSubmit"] {
        background: linear-gradient(135deg, #fbbf24 0%, #d97706 100%) !important;
        color: #050b18 !important;
        border: none !important;
        border-radius: 10px !important;
        padding: 0.6rem 1.2rem !important;
        font-weight: 700 !important;
        letter-spacing: 0.5px !important;
        transition: all 0.3s cubic-bezier(0.25, 0.8, 0.25, 1) !important;
        box-shadow: 0 4px 14px rgba(217, 119, 6, 0.3) !important;
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
    }
    button[data-testid^="stBaseButton-primary"]:hover,
    button[data-testid^="stBaseButton-formSubmit"]:hover {
        background: linear-gradient(135deg, #fcd34d 0%, #fbbf24 100%) !important;
        color: #050b18 !important;
        box-shadow: 0 6px 20px rgba(251, 191, 36, 0.5) !important;
        transform: translateY(-2px) !important;
    }
    
    button[data-testid="stBaseButton-secondary"] {
        background-color: rgba(13, 27, 62, 0.6) !important;
        color: #fbbf24 !important;
        border: 1px solid rgba(251, 191, 36, 0.4) !important;
        border-radius: 10px !important;
        padding: 0.6rem 1.2rem !important;
        font-weight: 700 !important;
        transition: all 0.25s ease !important;
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        box-shadow: none !important;
    }
    button[data-testid="stBaseButton-secondary"]:hover {
        background-color: #fbbf24 !important;
        color: #050b18 !important;
        border-color: #fbbf24 !important;
        box-shadow: 0 0 14px rgba(251, 191, 36, 0.45) !important;
        transform: translateY(-2px) !important;
    }
    button[data-testid^="stBaseButton"]:active {
        transform: translateY(0px) !important;
    }

    /* Widget labels */
    label, div[data-testid="stWidgetLabel"] p {
        color: #e2e8f0 !important;
        font-weight: 600 !important;
        font-size: 0.95rem !important;
        letter-spacing: 0.5px !important;
        margin-bottom: 8px !important;
    }
    
    /* Text input, Password input, Number input, Date input, Time input */
    div[data-testid="stTextInput"] input, 
    div[data-testid="stNumberInput"] input, 
    div[data-testid="stDateInput"] input, 
    div[data-testid="stTimeInput"] input,
    input[type="text"], 
    input[type="number"], 
    input[type="password"], 
    input[type="date"], 
    input[type="time"] {
        background-color: #0b132b !important;
        color: #f8fafc !important;
        border: 1px solid rgba(251, 191, 36, 0.25) !important;
        border-radius: 10px !important;
        padding: 10px 14px !important;
        font-size: 1rem !important;
        height: auto !important;
        transition: all 0.2s ease !important;
    }
    
    div[data-testid="stTextInput"] input:focus, 
    div[data-testid="stNumberInput"] input:focus, 
    div[data-testid="stDateInput"] input:focus, 
    div[data-testid="stTimeInput"] input:focus,
    input:focus {
        border-color: #fbbf24 !important;
        box-shadow: 0 0 0 2px rgba(251, 191, 36, 0.25) !important;
        background-color: #0e1a3d !important;
        outline: none !important;
    }
    
    /* Selectbox overrides */
    div[data-baseweb="select"] > div {
        background-color: #0b132b !important;
        border: 1px solid rgba(251, 191, 36, 0.25) !important;
        border-radius: 10px !important;
        color: #f8fafc !important;
        height: auto !important;
    }
    div[data-baseweb="select"] div[data-testid="stSelectboxVirtualFocus"] {
        color: #f8fafc !important;
    }
    div[data-baseweb="select"] div {
        color: #f8fafc !important;
    }
    div[data-baseweb="select"] svg {
        fill: #fbbf24 !important;
    }
    
    /* Dropdown selection lists */
    ul[role="listbox"] {
        background-color: #0b132b !important;
        border: 1px solid rgba(251, 191, 36, 0.5) !important;
        border-radius: 10px !important;
        box-shadow: 0 8px 30px rgba(0,0,0,0.6) !important;
    }
    ul[role="listbox"] li {
        color: #f8fafc !important;
        padding: 10px 15px !important;
    }
    ul[role="listbox"] li[aria-selected="true"] {
        background-color: rgba(251, 191, 36, 0.15) !important;
        color: #fbbf24 !important;
    }
    ul[role="listbox"] li:hover {
        background-color: #fbbf24 !important;
        color: #050b18 !important;
    }
    
    /* Number input stepping buttons */
    div[data-testid="stNumberInput"] button {
        background-color: #1a2542 !important;
        color: #fbbf24 !important;
        border: 1px solid rgba(251, 191, 36, 0.2) !important;
    }
    div[data-testid="stNumberInput"] button:hover {
        background-color: #fbbf24 !important;
        color: #050b18 !important;
    }

    /* Tabs styling overrides */
    div[data-baseweb="tab-list"] {
        background-color: rgba(13, 27, 62, 0.5) !important;
        border-bottom: 2px solid rgba(251, 191, 36, 0.2) !important;
        border-radius: 12px 12px 0 0 !important;
        padding: 6px 12px 0 12px !important;
    }
    button[data-baseweb="tab"] {
        color: #94a3b8 !important;
        font-size: 1.05rem !important;
        font-weight: 600 !important;
        background-color: transparent !important;
        border: none !important;
    }
    button[data-baseweb="tab"][aria-selected="true"] {
        color: #fbbf24 !important;
        font-weight: 700 !important;
    }
    button[data-baseweb="tab"]:hover {
        color: #f8fafc !important;
    }
    div[data-baseweb="tab-highlight"] {
        background-color: #fbbf24 !important;
        height: 3px !important;
    }
    
    /* Expander override */
    div[data-testid="stExpander"] {
        background-color: rgba(13, 27, 62, 0.45) !important;
        border: 1px solid rgba(251, 191, 36, 0.2) !important;
        border-radius: 12px !important;
        margin-bottom: 12px !important;
    }
    div[data-testid="stExpander"] details summary {
        color: #fbbf24 !important;
        font-weight: 700 !important;
    }
    div[data-testid="stExpander"] details summary:hover {
        color: #fcd34d !important;
    }

    /* Dialog Modals complete readability overrides */
    div[role="dialog"] {
        background-color: #0b132b !important;
        border: 1px solid rgba(251, 191, 36, 0.4) !important;
        border-radius: 16px !important;
        box-shadow: 0 16px 45px rgba(0,0,0,0.75) !important;
    }
    /* Enforce light/bright off-white text inside dialog modals for readability */
    div[role="dialog"] h1,
    div[role="dialog"] h2,
    div[role="dialog"] h3,
    div[role="dialog"] h4,
    div[role="dialog"] h5,
    div[role="dialog"] h6,
    div[role="dialog"] p,
    div[role="dialog"] li {
        color: #f1f5f9 !important;
    }
    div[role="dialog"] table.table-leaderboard th {
        color: #050b18 !important; /* Gold headers must have dark text */
    }
    
    /* Alert cards overrides */
    div[data-testid="stAlert"] {
        background-color: rgba(13, 27, 62, 0.7) !important;
        border: 1px solid rgba(251, 191, 36, 0.2) !important;
        border-radius: 10px !important;
    }
    div[data-testid="stAlert"] p {
        color: #e2e8f0 !important;
    }
    
    /* Sidebar overrides */
    section[data-testid="stSidebar"] {
        background-color: #050b18 !important;
        border-right: 1px solid rgba(251, 191, 36, 0.25) !important;
    }
    div[data-testid="stSidebarUserContent"] {
        background-color: #050b18 !important;
    }
    div[data-testid="stSidebarHeader"] {
        background-color: #050b18 !important;
        border-bottom: 1px solid rgba(251, 191, 36, 0.15) !important;
    }
    section[data-testid="stSidebar"] h1,
    section[data-testid="stSidebar"] h2,
    section[data-testid="stSidebar"] h3,
    section[data-testid="stSidebar"] h4,
    section[data-testid="stSidebar"] h5,
    section[data-testid="stSidebar"] h6 {
        color: #fbbf24 !important;
        font-weight: 700 !important;
    }
    section[data-testid="stSidebar"] p,
    section[data-testid="stSidebar"] span,
    section[data-testid="stSidebar"] label,
    section[data-testid="stSidebar"] div {
        color: #e2e8f0 !important;
    }
    section[data-testid="stSidebar"] input[type="text"],
    section[data-testid="stSidebar"] input[type="password"] {
        background-color: #0d1b3e !important;
        color: #f8fafc !important;
        border: 1px solid rgba(251, 191, 36, 0.4) !important;
        border-radius: 8px !important;
    }
    section[data-testid="stSidebar"] input[type="text"]:focus,
    section[data-testid="stSidebar"] input[type="password"]:focus {
        border-color: #fbbf24 !important;
        box-shadow: 0 0 0 2px rgba(251, 191, 36, 0.25) !important;
        outline: none !important;
    }

    /* Sidebar collapse button styles (visible in both states) */
    [data-testid="stSidebarCollapseButton"] button,
    button[data-testid="collapsedSidebarMenu"],
    [data-testid="stHeader"] button {
        background-color: rgba(13, 27, 62, 0.9) !important;
        color: #fbbf24 !important;
        border: 1px solid #fbbf24 !important;
        border-radius: 50% !important;
        width: 38px !important;
        height: 38px !important;
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        transition: all 0.25s ease !important;
        box-shadow: 0 4px 10px rgba(0,0,0,0.5) !important;
        z-index: 999999 !important;
    }
    [data-testid="stSidebarCollapseButton"] button:hover,
    button[data-testid="collapsedSidebarMenu"]:hover,
    [data-testid="stHeader"] button:hover {
        background-color: #fbbf24 !important;
        color: #050b18 !important;
        box-shadow: 0 0 14px rgba(251, 191, 36, 0.6) !important;
        transform: scale(1.05) !important;
    }
    [data-testid="stSidebarCollapseButton"] button svg,
    button[data-testid="collapsedSidebarMenu"] svg,
    [data-testid="stHeader"] button svg {
        fill: currentColor !important;
        stroke: currentColor !important;
        color: inherit !important;
    }
    
    @media (max-width: 600px) {
        .teams-grid {
            flex-direction: column;
            gap: 12px;
        }
        .score-display {
            margin: 5px 0;
        }
    }
</style>
""")

# --- Header ---
import base64
logo_b64 = ""
if os.path.exists(logo_path):
    try:
        with open(logo_path, "rb") as f:
            logo_b64 = base64.b64encode(f.read()).decode("utf-8")
    except Exception:
        pass

logo_html = f"<img src='data:image/png;base64,{logo_b64}' style='width:55px; height:55px; margin-bottom:8px;'><br>" if logo_b64 else "🏆 "

now = get_nepal_time()
st.html(f"""
<div style='text-align: center; margin-top: -15px;'>
    <h1 class='worldcup-title'>{logo_html}FIFA WORLD CUP 2026</h1>
    <div class='worldcup-subtitle'>Score Prediction</div>
    <div style='font-size: 0.95rem; color: #fbbf24; margin-top: 4px; margin-bottom: 8px; font-weight: 500; font-style: italic;'>
        फुटबलको महाकुम्भको रोमाञ्चक यात्रा र उत्साहको आनन्द लिनुहोस्! ⚽🏆
    </div>
    <div class='server-time'>🕒 Current Time: <b>{now.strftime('%Y-%m-%d %H:%M:%S')} (Kathmandu Time)</b></div>
</div>
""")

# --- User Session Setup ---
if "user_id" not in st.session_state:
    st.session_state.user_id = None
    st.session_state.username = None
    st.session_state.display_name = None

# --- Sidebar Authentication ---
st.sidebar.html("<h2 style='text-align:center; color:#fbbf24; margin-bottom:15px;'>👤 User Center</h2>")

if st.session_state.user_id is None:
    st.sidebar.subheader("Login Form")
    username_input = st.sidebar.text_input("Username", value="", key="login_username").strip().lower()
    password_input = st.sidebar.text_input("Password", type="password", key="login_password")
    
    if st.sidebar.button("Log In", use_container_width=True):
        user = get_user_by_username(username_input)
        if user:
            if user["is_active"] == 0:
                st.sidebar.error("🚫 This account has been disabled by the administrator.")
            elif check_password(user["password"], password_input):
                st.session_state.user_id = user["id"]
                st.session_state.username = user["username"]
                st.session_state.display_name = user["display_name"]
                st.sidebar.success(f"Welcome back, {user['display_name']}!")
                st.rerun()
            else:
                st.sidebar.error("Invalid Username or Password.")
        else:
            st.sidebar.error("Invalid Username or Password.")
            
    st.sidebar.info("💡 Please contact Admin for your username and password.")
else:
    st.sidebar.html(f"""
    <div style='background:rgba(251,191,36,0.1); border:1px solid #fbbf24; border-radius:8px; padding:15px; text-align:center; margin-bottom:15px;'>
        <p style='margin:0; font-size:0.85rem; color:#cbd5e1;'>Logged in as:</p>
        <h4 style='margin:5px 0; color:#fbbf24;'>{st.session_state.display_name}</h4>
        <p style='margin:0; font-size:0.8rem; color:#10b981; font-weight:700;'>✔ Connected</p>
    </div>
    """)
    
    # Self-Service Password Change
    with st.sidebar.expander("🔑 Change Password"):
        old_pwd = st.text_input("Current Password", type="password", key="side_old_pwd")
        new_pwd = st.text_input("New Password", type="password", key="side_new_pwd")
        confirm_pwd = st.text_input("Confirm New Password", type="password", key="side_confirm_pwd")
        if st.button("Update Password", use_container_width=True):
            if not old_pwd or not new_pwd or not confirm_pwd:
                st.error("All fields are required.")
            elif new_pwd != confirm_pwd:
                st.error("New passwords do not match.")
            else:
                # Check current password
                conn = sqlite3.connect(DB_PATH)
                conn.row_factory = sqlite3.Row
                user = conn.execute("SELECT password FROM users WHERE id = ?", (st.session_state.user_id,)).fetchone()
                if user and check_password(user["password"], old_pwd):
                    conn.execute("UPDATE users SET password = ? WHERE id = ?", (hash_password(new_pwd), st.session_state.user_id))
                    conn.commit()
                    st.success("Password updated successfully!")
                else:
                    st.error("Incorrect current password.")
                conn.close()
                
    if st.sidebar.button("Log Out", use_container_width=True):
        st.session_state.user_id = None
        st.session_state.username = None
        st.session_state.display_name = None
        st.sidebar.success("Logged out successfully!")
        st.rerun()

# --- Prediction Summary Dialog Modal ---
@st.dialog("📊 Prediction Summary", width="large")
def show_prediction_summary_dialog(match_id, team_a, team_b, score_a, score_b, finished):
    pool_details = get_match_pool_and_payout(match_id)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    preds = conn.execute("""
        SELECT u.display_name, p.pred_score_a, p.pred_score_b 
        FROM predictions p
        JOIN users u ON p.user_id = u.id
        WHERE p.match_id = ? AND u.username != 'admin'
        ORDER BY u.display_name ASC
    """, (match_id,)).fetchall()
    conn.close()
    
    emoji_a = get_team_emoji(team_a)
    emoji_b = get_team_emoji(team_b)
    
    st.markdown(f"### {emoji_a} {team_a} vs {emoji_b} {team_b}")
    
    try:
        is_finished = int(finished) == 1
    except Exception:
        is_finished = False

    if is_finished:
        st.html(f"""
        <div style='background:rgba(251,191,36,0.1); border:1px solid #fbbf24; border-radius:8px; padding:15px; margin-bottom:15px;'>
            <h4 style='margin:0 0 5px 0; color:#fbbf24;'>Final Score: {score_a} - {score_b}</h4>
            <p style='margin:0; font-size:0.9rem; color:#cbd5e1;'>
                💰 Match Pool: <b>{pool_details['total_pool']:.0f} pts</b> 
                (Carryover: {pool_details['incoming_carry']:.0f} | Base: {pool_details['base_pool']:.0f})
            </p>
            <p style='margin:5px 0 0 0; font-size:0.9rem; color:#10b981; font-weight:700;'>
                🏆 Winners: {pool_details['winners_count']} | Payout: {pool_details['payout']:.1f} pts each
            </p>
        </div>
        """)
    else:
        live_a = score_a if score_a is not None else 0
        live_b = score_b if score_b is not None else 0
        st.html(f"""
        <div style='background:rgba(59,130,246,0.1); border:1px solid #3b82f6; border-radius:8px; padding:15px; margin-bottom:15px;'>
            <h4 style='margin:0 0 5px 0; color:#3b82f6;'>Live Score: {live_a} - {live_b}</h4>
            <p style='margin:0; font-size:0.9rem; color:#cbd5e1;'>
                💰 Current Match Pool: <b>{pool_details['total_pool']:.0f} pts</b> 
                (Carryover: {pool_details['incoming_carry']:.0f} | Base: {pool_details['base_pool']:.0f})
            </p>
        </div>
        """)

    if preds:
        # Convert to list and calculate sort categories (1: Winner/Active, 2: Correct Outcome, 3: Incorrect/Out)
        preds_list = []
        for r in preds:
            pa = r["pred_score_a"]
            pb = r["pred_score_b"]
            
            is_winner = False
            outcome_match = False
            
            if is_finished:
                try:
                    is_winner = (int(pa) == int(score_a) and int(pb) == int(score_b))
                except Exception:
                    is_winner = False
                if not is_winner:
                    try:
                        pred_diff = int(pa) - int(pb)
                        act_diff = int(score_a) - int(score_b)
                        outcome_match = (pred_diff > 0 and act_diff > 0) or (pred_diff < 0 and act_diff < 0) or (pred_diff == 0 and act_diff == 0)
                    except Exception:
                        outcome_match = False
                sort_cat = 1 if is_winner else (2 if outcome_match else 3)
            else:
                live_a_val = int(score_a) if score_a is not None else 0
                live_b_val = int(score_b) if score_b is not None else 0
                try:
                    is_active = (int(pa) >= live_a_val) and (int(pb) >= live_b_val)
                except Exception:
                    is_active = False
                sort_cat = 1 if is_active else 3
                
            preds_list.append({
                "display_name": r["display_name"],
                "pred_score_a": pa,
                "pred_score_b": pb,
                "sort_cat": sort_cat
            })
            
        # Sort by sort_cat ASC, then display_name ASC
        preds_list.sort(key=lambda x: (x["sort_cat"], x["display_name"]))

        html_content = "<table class='table-leaderboard' style='font-size:0.9rem; text-align:center;'><thead><tr><th>Predictor</th><th>Prediction</th><th>Status</th><th>Points Won</th></tr></thead><tbody>"
        for p in preds_list:
            pa = p["pred_score_a"]
            pb = p["pred_score_b"]
            if is_finished:
                if p["sort_cat"] == 1:
                    status_lbl = "<span style='color:#fbbf24; font-weight:700;'>🏆 Winner</span>"
                    pts_lbl = f"<b style='color:#10b981;'>+{pool_details['payout']:.1f}</b>"
                    bg_color = "background-color:rgba(251,191,36,0.15);"
                elif p["sort_cat"] == 2:
                    status_lbl = "<span style='color:#38bdf8; font-weight:600;'>Correct Outcome</span>"
                    pts_lbl = "0"
                    bg_color = "background-color:rgba(56,189,248,0.05);"
                else:
                    status_lbl = "<span style='color:#cbd5e1;'>Incorrect</span>"
                    pts_lbl = "0"
                    bg_color = ""
            else:
                if p["sort_cat"] == 1:
                    status_lbl = "<span style='color:#10b981; font-weight:600;'>🟢 Active</span>"
                    bg_color = "background-color:rgba(16,185,129,0.05);"
                else:
                    status_lbl = "<span style='color:#ef4444; text-decoration:line-through;'>❌ Out</span>"
                    bg_color = "background-color:rgba(239,68,68,0.02);"
                pts_lbl = "-"
                
            html_content += f"<tr style='{bg_color}'><td><b>{p['display_name']}</b></td><td>{pa} - {pb}</td><td>{status_lbl}</td><td>{pts_lbl}</td></tr>"
            
        html_content += "</tbody></table>"
        st.html(html_content)
    else:
        st.info("No predictions submitted for this match.")

# --- Auto-refresh & Live Match Banner ---
# Detect any currently live match to decide refresh rate
_live_matches_now = []
try:
    _now_utc = datetime.utcnow()
    _conn_live = sqlite3.connect(DB_PATH)
    _conn_live.row_factory = sqlite3.Row
    _unfinished = _conn_live.execute("SELECT * FROM matches WHERE finished = 0").fetchall()
    _conn_live.close()
    for _m in _unfinished:
        try:
            _ko_dt = datetime.strptime(_m['kickoff_time'], '%Y-%m-%d %H:%M:%S')
            _ko_utc = _ko_dt - timedelta(hours=5, minutes=45)
            _elapsed = (_now_utc - _ko_utc).total_seconds()
            if 0 <= _elapsed <= 9000:  # within 2.5 hours of kickoff
                _live_matches_now.append(_m)
        except Exception:
            pass
except Exception:
    pass

# Set auto-refresh interval: 60s if live, 5min otherwise
if _HAS_AUTOREFRESH:
    _refresh_interval = 60 * 1000 if _live_matches_now else 5 * 60 * 1000
    st_autorefresh(interval=_refresh_interval, key="live_autorefresh")

# Show LIVE banner for each ongoing match
if _live_matches_now:
    for _lm in _live_matches_now:
        _sa = f"{_lm['score_a']}" if _lm['score_a'] is not None else "?"
        _sb = f"{_lm['score_b']}" if _lm['score_b'] is not None else "?"
        _score_disp = f"{_sa} – {_sb}" if _lm['score_a'] is not None else "Ongoing"
        _time_str = f" ({_lm['match_time']})" if _lm['match_time'] else ""
        st.markdown(
            f"<div style='background:linear-gradient(90deg,rgba(220,38,38,0.9),rgba(239,68,68,0.7)); "
            f"border-radius:10px; padding:10px 20px; margin-bottom:10px; "
            f"display:flex; align-items:center; gap:12px; animation:pulse 1.5s infinite;'>"
            f"<span style='font-size:1.3rem;'>🔴</span>"
            f"<span style='color:white; font-weight:700; font-size:1.05rem; letter-spacing:0.05em;'>LIVE{_time_str}</span>"
            f"<span style='color:#fef2f2; font-size:1.05rem;'>{_lm['team_a']} <b style=\"color:white\">{_score_disp}</b> {_lm['team_b']}</span>"
            f"<span style='margin-left:auto; color:rgba(255,255,255,0.7); font-size:0.8rem;'></span>"
            f"</div>",
            unsafe_allow_html=True
        )

# --- Main App Pages ---
tabs = ["🏆 Leaderboard", "⚽ Matches & Predictions", "🌍 Group Tables", "⚽ Top Goalscorers", "📋 Teams & Lineups"]
if st.session_state.username == "admin":
    tabs.append("🛠️ Admin Controls")

tab_leaderboard, tab_matches, tab_groups, tab_scorers, tab_lineups, *tab_admin = st.tabs(tabs)

# --- Tab 1: Leaderboard ---
with tab_leaderboard:
    st.subheader("Recent Predictions & Live Tracker")
    st.write("Click on any recent match to view predictions submitted by all users, including live eligibility and points won.")
    
    # Quick live score updater (admin only, visible when match is ongoing)
    if _live_matches_now and st.session_state.get("username") == "admin":
        for _lm in _live_matches_now:
            with st.expander(f"⚡ Quick Live Score Update: {_lm['team_a']} vs {_lm['team_b']}", expanded=True):
                st.caption("Wikipedia may have a delay for in-progress scores. Enter the current score manually here.")
                _col1, _col2, _col3 = st.columns([2, 2, 1])
                with _col1:
                    _live_sa = st.number_input(f"{_lm['team_a']} goals", min_value=0, max_value=20,
                                               value=_lm['score_a'] if _lm['score_a'] is not None else 0,
                                               key=f"live_sa_{_lm['id']}")
                with _col2:
                    _live_sb = st.number_input(f"{_lm['team_b']} goals", min_value=0, max_value=20,
                                               value=_lm['score_b'] if _lm['score_b'] is not None else 0,
                                               key=f"live_sb_{_lm['id']}")
                with _col3:
                    _is_final = st.checkbox("Final?", value=False, key=f"live_fin_{_lm['id']}")
                if st.button("💾 Update Score", key=f"live_upd_{_lm['id']}", use_container_width=True):
                    _fin_val = 1 if _is_final else 0
                    _conn_upd = sqlite3.connect(DB_PATH)
                    _conn_upd.execute("UPDATE matches SET score_a=?, score_b=?, finished=? WHERE id=?",
                                      (_live_sa, _live_sb, _fin_val, _lm['id']))
                    _conn_upd.commit()
                    _conn_upd.close()
                    st.success(f"Score updated: {_lm['team_a']} {_live_sa}–{_live_sb} {_lm['team_b']}" + (" (Final)" if _is_final else " (Live)"))
                    st.rerun()
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Get the 5 most recent matches that are locked, live, or finished
    recent_matches = conn.execute("""
        SELECT * FROM matches 
        WHERE finished = 1 OR datetime(kickoff_time) <= datetime(?)
        ORDER BY kickoff_time DESC LIMIT 5
    """, (get_nepal_time().strftime('%Y-%m-%d %H:%M:%S'),)).fetchall()
    conn.close()
    
    # Show recent match first (recent to past order)
    if recent_matches:
        cols = st.columns(len(recent_matches))
        for idx, m in enumerate(recent_matches):
            with cols[idx]:
                mid = m["id"]
                emoji_a = get_team_emoji(m["team_a"])
                emoji_b = get_team_emoji(m["team_b"])
                
                group_lbl = f"Group {m['group_name']}" if m['group_name'] else m['stage'].replace("-", " ").title()
                city_lbl = m['city'].replace("-", " ").title()
                
                pool_details = get_match_pool_and_payout(mid)
                
                winners_summary_html = ""
                if m["finished"] == 1:
                    score_str = f"<b style='color:#fbbf24; font-size:1.2rem;'>{m['score_a']} - {m['score_b']}</b>"
                    status_badge = "<span class='badge-status badge-finished' style='display:block; margin:4px auto; text-align:center;'>Finished</span>"
                    
                    if pool_details['winners_count'] > 0:
                        conn = sqlite3.connect(DB_PATH)
                        conn.row_factory = sqlite3.Row
                        w_list = conn.execute(
                            "SELECT display_name FROM users WHERE id IN (" + ",".join(str(w) for w in pool_details['winners']) + ")"
                        ).fetchall()
                        conn.close()
                        winner_names = ", ".join([r['display_name'] for r in w_list])
                        winners_summary_html = f"""
                        <div style='border-top: 1px solid rgba(255, 255, 255, 0.05); margin-top: 8px; padding-top: 6px; text-align: left;'>
                            <div style='font-size: 0.72rem; color: #10b981; font-weight: 600; display: flex; justify-content: space-between;'>
                                <span>🏆 {pool_details['winners_count']} Winner(s)</span>
                                <span>+{pool_details['payout']:.1f} pts</span>
                            </div>
                            <div style='font-size: 0.68rem; color: #cbd5e1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-top: 2px;' title='{winner_names}'>👑 {winner_names}</div>
                        </div>
                        """
                    else:
                        winners_summary_html = f"""
                        <div style='border-top: 1px solid rgba(255, 255, 255, 0.05); margin-top: 8px; padding-top: 6px; text-align: left;'>
                            <div style='font-size: 0.72rem; color: #f59e0b; font-weight: 600; display: flex; justify-content: space-between;'>
                                <span>🏆 0 Winners</span>
                                <span>Carryover</span>
                            </div>
                            <div style='font-size: 0.68rem; color: #ef4444; font-weight: 600; margin-top: 2px;'>💰 {pool_details['outgoing_carry']:.0f} pts</div>
                        </div>
                        """
                elif m["score_a"] is not None and m["score_b"] is not None:
                    score_str = f"<b style='color:#ef4444; font-size:1.2rem;'>{m['score_a']} - {m['score_b']}</b>"
                    _t_badge = f" ({m['match_time']})" if m['match_time'] else ""
                    status_badge = f"<span class='badge-status badge-locked' style='background:rgba(239,68,68,0.15); color:#ef4444; border-color:#ef4444; display:block; margin:4px auto; text-align:center;'>🔴 LIVE{_t_badge}</span>"
                else:
                    score_str = "<b style='color:#cbd5e1; font-size:1.1rem;'>vs</b>"
                    status_badge = "<span class='badge-status badge-locked' style='display:block; margin:4px auto; text-align:center;'>Locked</span>"
                
                st.html(f"""
                <div style='background: rgba(15, 23, 42, 0.4); border: 1px solid rgba(251, 191, 36, 0.15); border-radius: 12px; padding: 12px; text-align: center; margin-bottom: 8px;'>
                    <div style='font-size:0.75rem; color:#fbbf24; font-weight:700; margin-bottom:4px;'>Match #{m['match_number']}</div>
                    <div style='font-size: 0.9rem; font-weight: 600; color: #f8fafc; margin-bottom: 5px;'>{emoji_a} {m['team_a']}</div>
                    <div style='margin: 5px 0;'>{score_str}</div>
                    <div style='font-size: 0.9rem; font-weight: 600; color: #f8fafc; margin-top: 5px;'>{emoji_b} {m['team_b']}</div>
                    <div style='margin-top:8px;'>{status_badge}</div>
                    <div style='font-size:0.75rem; color:#cbd5e1; margin-top:6px;'>🏟 {city_lbl}</div>
                    {winners_summary_html}
                </div>
                """)
                
                if st.button("📊 View Predictions", key=f"rec_sum_{mid}", use_container_width=True):
                    show_prediction_summary_dialog(mid, m['team_a'], m['team_b'], m['score_a'], m['score_b'], m['finished'])
    else:
        st.info("No completed or locked matches yet to display predictions.")
        
    st.html("<hr style='border-color:rgba(255,255,255,0.05);'>")
    
    st.subheader("Leaderboard Standings")
    st.write("Calculated points rules: Each prediction costs **100 pts**. Whichever user gets the exact score right splits the total match points pool equally. If no one is right, the pool carries forward to the next match!")
    
    leaderboard_data = get_leaderboard()
    
    if leaderboard_data:
        # Build styled HTML table
        rows_html = ""
        for rank, row in enumerate(leaderboard_data, 1):
            highlight = "style='color:#fbbf24; font-weight:700;'" if rank == 1 else ""
            net_color = "#10b981" if row['points'] >= 0 else "#ef4444"
            rows_html += f"<tr><td>{rank}</td><td {highlight}>{row['display_name']} (@{row['username']})</td><td>{row['pred_count']}</td><td>🥇 {row['exact_wins']}</td><td style='color:#10b981; font-weight:600;'>{row['points_won']:.1f}</td><td style='color:#ef4444;'>{row['points_cost']}</td><td style='font-weight:700; color:{net_color};'>{row['points']:.1f}</td></tr>"
            
        st.html(f"<div style='overflow-x: auto;'><table class='table-leaderboard'><thead><tr><th>Rank</th><th>Predictor</th><th>Predictions Made</th><th>Winning Predictions (Exact)</th><th>Total Points Won</th><th>Points Cost</th><th>Net Balance</th></tr></thead><tbody>{rows_html}</tbody></table></div>")
    else:
        st.info("No data available.")

# --- Tab 2: Matches & Predictions ---
with tab_matches:
    st.subheader("World Cup Schedule & Predictions")
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    all_matches = conn.execute("SELECT * FROM matches ORDER BY kickoff_time ASC").fetchall()
    conn.close()
    
    open_matches = []
    locked_matches = []
    finished_matches = []
    
    for m in all_matches:
        k_time = datetime.strptime(m["kickoff_time"], "%Y-%m-%d %H:%M:%S")
        time_diff = k_time - now
        
        if m["finished"]:
            finished_matches.append(m)
        elif time_diff.total_seconds() > 3600:
            open_matches.append(m)
        else:
            locked_matches.append(m)
            
    finished_matches = list(reversed(finished_matches))
    col_open, col_locked, col_finished = st.columns(3)
    
    # Column A: Open for Predictions
    with col_open:
        st.html("<h3 style='color:#10b981; border-bottom:2px solid #10b981; padding-bottom:5px;'>🟢 Open for Prediction</h3>")
        st.write("Kickoff is > 1 hour away. Submit predictions. Note: scope cannot be modified after submit.")
        
        if open_matches:
            for m in open_matches:
                mid = m["id"]
                k_time = datetime.strptime(m["kickoff_time"], "%Y-%m-%d %H:%M:%S")
                time_diff = k_time - now
                hours_left = int(time_diff.total_seconds() // 3600)
                mins_left = int((time_diff.total_seconds() % 3600) // 60)
                
                emoji_a = get_team_emoji(m["team_a"])
                emoji_b = get_team_emoji(m["team_b"])
                
                user_pred = None
                if st.session_state.user_id and st.session_state.username != "admin":
                    user_pred = get_user_prediction(st.session_state.user_id, mid)
                    
                pool_details = get_match_pool_and_payout(mid)
                
                group_lbl = f"Group {m['group_name']}" if m['group_name'] else m['stage'].replace("-", " ").title()
                city_lbl = m['city'].replace("-", " ").title()
                st.html(textwrap.dedent(f"""
                <div class='match-card'>
                    <div class='card-meta'>
                        <span>⚽ Match #{m['match_number']} | {group_lbl}</span>
                        <span class='badge-status badge-open'>Open ({hours_left}h {mins_left}m left)</span>
                    </div>
                    <div class='teams-grid'>
                        <div class='team-block'><span class='team-flag'>{emoji_a}</span>{m['team_a']}</div>
                        <div class='middle-block'><span class='vs-badge'>VS</span></div>
                        <div class='team-block'><span class='team-flag'>{emoji_b}</span>{m['team_b']}</div>
                    </div>
                    <div style='font-size:0.8rem; color:#cbd5e1; text-align:center; margin-top:5px;'>
                        💰 Match Pool: <b>{pool_details['total_pool']:.0f} pts</b> (Carryover: {pool_details['incoming_carry']:.0f})
                    </div>
                    <div class='card-footer' style='text-align:center;'>
                        <p style='margin:0; font-size:0.8rem; color:#cbd5e1;'>🏟 {m['stadium']} ({city_lbl})</p>
                        <p style='margin:2px 0 0 0; font-size:0.8rem; color:#cbd5e1;'>Kickoff: {k_time.strftime('%Y-%m-%d %H:%M')} NPT</p>
                    </div>
                </div>
                """))
                
                if st.session_state.user_id and st.session_state.username != "admin":
                    if user_pred:
                        st.html(textwrap.dedent(f"""
                        <div style='background:rgba(16,185,129,0.1); border:1px solid #10b981; border-radius:8px; padding:10px; text-align:center; font-size:0.9rem; margin-top:-10px; margin-bottom:20px; color:#10b981;'>
                            ⚽ Your Prediction: <b>{user_pred['pred_score_a']} - {user_pred['pred_score_b']}</b> (Submitted)
                        </div>
                        """))
                    else:
                        with st.form(key=f"form_pred_{mid}"):
                            c1, c2 = st.columns(2)
                            with c1:
                                s_a = st.number_input(f"{m['team_a']} goals", min_value=0, max_value=20, value=0, step=1, key=f"inp_sa_{mid}")
                            with c2:
                                s_b = st.number_input(f"{m['team_b']} goals", min_value=0, max_value=20, value=0, step=1, key=f"inp_sb_{mid}")
                            
                            sub_btn = st.form_submit_button("Lock in Prediction", use_container_width=True)
                            if sub_btn:
                                if submit_prediction(st.session_state.user_id, mid, s_a, s_b):
                                    st.success("Prediction saved! It cannot be modified.")
                                    st.rerun()
                elif not st.session_state.user_id:
                    st.html("<p style='text-align:center; color:#cbd5e1; font-size:0.85rem; margin-top:-10px; margin-bottom:20px;'>🔒 Login to submit prediction</p>")
        else:
            st.info("No matches currently open for prediction.")
            
    # Column B: Locked / Live
    with col_locked:
        st.html("<h3 style='color:#f59e0b; border-bottom:2px solid #f59e0b; padding-bottom:5px;'>🔒 Locked / Live</h3>")
        st.write("Kickoff is <= 1 hour away. Predictions are closed. Live tracker displays active predictions.")
        
        if locked_matches:
            for m in locked_matches:
                mid = m["id"]
                k_time = datetime.strptime(m["kickoff_time"], "%Y-%m-%d %H:%M:%S")
                time_diff = k_time - now
                
                emoji_a = get_team_emoji(m["team_a"])
                emoji_b = get_team_emoji(m["team_b"])
                
                is_live = time_diff.total_seconds() <= 0
                live_a = m["score_a"] if m["score_a"] is not None else 0
                live_b = m["score_b"] if m["score_b"] is not None else 0
                
                pool_details = get_match_pool_and_payout(mid)
                
                group_lbl = f"Group {m['group_name']}" if m['group_name'] else m['stage'].replace("-", " ").title()
                city_lbl = m['city'].replace("-", " ").title()
                # Render Card
                if is_live:
                    _t_badge = f" ({m['match_time']})" if m['match_time'] else ""
                    st.html(textwrap.dedent(f"""
                    <div class='match-card' style='border-color:#ef4444;'>
                        <div class='card-meta'>
                            <span>⚽ Match #{m['match_number']} | {group_lbl}</span>
                            <span class='badge-status badge-locked' style='background:rgba(239,68,68,0.15); color:#ef4444; border-color:#ef4444;'>🔴 LIVE{_t_badge}</span>
                        </div>
                        <div class='teams-grid'>
                            <div class='team-block'><span class='team-flag'>{emoji_a}</span>{m['team_a']}</div>
                            <div class='middle-block'>
                                <span class='score-display' style='color:#ef4444;'>{live_a} - {live_b}</span>
                                <span style='font-size:0.75rem; color:#f59e0b; font-weight:700; margin-top:5px;'>Live Score</span>
                            </div>
                            <div class='team-block'><span class='team-flag'>{emoji_b}</span>{m['team_b']}</div>
                        </div>
                        <div style='font-size:0.8rem; color:#cbd5e1; text-align:center; margin-top:5px;'>
                            💰 Match Pool: <b>{pool_details['total_pool']:.0f} pts</b> (Carryover: {pool_details['incoming_carry']:.0f})
                        </div>
                        <div class='card-footer' style='text-align:center;'>
                            <p style='margin:0; font-size:0.8rem; color:#cbd5e1;'>🏟 {m['stadium']} ({city_lbl})</p>
                            <p style='margin:2px 0 0 0; font-size:0.8rem; color:#cbd5e1;'>Started: {k_time.strftime('%Y-%m-%d %H:%M')} NPT</p>
                        </div>
                    </div>
                    """))
                else:
                    status_str = f"Locked (Starts in {int(time_diff.total_seconds() // 60)}m)"
                    st.html(textwrap.dedent(f"""
                    <div class='match-card'>
                        <div class='card-meta'>
                            <span>⚽ Match #{m['match_number']} | {group_lbl}</span>
                            <span class='badge-status badge-locked'>{status_str}</span>
                        </div>
                        <div class='teams-grid'>
                            <div class='team-block'><span class='team-flag'>{emoji_a}</span>{m['team_a']}</div>
                            <div class='middle-block'><span class='vs-badge'>VS</span></div>
                            <div class='team-block'><span class='team-flag'>{emoji_b}</span>{m['team_b']}</div>
                        </div>
                        <div style='font-size:0.8rem; color:#cbd5e1; text-align:center; margin-top:5px;'>
                            💰 Match Pool: <b>{pool_details['total_pool']:.0f} pts</b> (Carryover: {pool_details['incoming_carry']:.0f})
                        </div>
                        <div class='card-footer' style='text-align:center;'>
                            <p style='margin:0; font-size:0.8rem; color:#cbd5e1;'>🏟 {m['stadium']} ({city_lbl})</p>
                            <p style='margin:2px 0 0 0; font-size:0.8rem; color:#cbd5e1;'>Kickoff: {k_time.strftime('%Y-%m-%d %H:%M')} NPT</p>
                        </div>
                    </div>
                    """))
                
                # Show predictions summary
                preds = get_predictions_summary(mid)
                if preds:
                    # Count active predictors
                    active_count = sum(1 for p in preds if p["pred_score_a"] >= live_a and p["pred_score_b"] >= live_b)
                    st.write(f"**Predictions & Active Tracker ({active_count} active):**")
                    if is_live:
                        if active_count > 0:
                            potential_payout = pool_details['total_pool'] / active_count
                            st.info(f"✨ Current Potential Payout: **{potential_payout:.1f} pts** per active predictor!")
                        else:
                            st.warning(f"⚠️ Everyone eliminated! Pool of **{pool_details['total_pool']:.0f} pts** will double and carry forward!")
                    
                    if st.button("📊 View Predictions Summary", key=f"sum_l_{mid}", use_container_width=True):
                        show_prediction_summary_dialog(mid, m['team_a'], m['team_b'], m['score_a'], m['score_b'], 0)
                else:
                    st.caption("No predictions were submitted for this match.")
                st.html("<div style='margin-bottom:20px;'></div>")
        else:
            st.info("No matches currently locked or live.")
            
    # Column C: Finished
    with col_finished:
        st.html("<h3 style='color:#cbd5e1; border-bottom:2px solid #cbd5e1; padding-bottom:5px;'>✅ Finished</h3>")
        st.write("Match completed. Displays final score, winner, predictions summary, and points calculated.")
        
        if finished_matches:
            for m in finished_matches:
                mid = m["id"]
                emoji_a = get_team_emoji(m["team_a"])
                emoji_b = get_team_emoji(m["team_b"])
                
                score_a = m["score_a"]
                score_b = m["score_b"]
                
                if score_a > score_b:
                    winner_str = f"🏆 {m['team_a']} Wins"
                elif score_a < score_b:
                    winner_str = f"🏆 {m['team_b']} Wins"
                else:
                    winner_str = "🤝 Draw Match"
                
                pool_details = get_match_pool_and_payout(mid)
                
                points_awarded = None
                got_exact = False
                if st.session_state.user_id and st.session_state.username != "admin":
                    user_pred = get_user_prediction(st.session_state.user_id, mid)
                    if user_pred:
                        got_exact = (user_pred["pred_score_a"] == score_a and user_pred["pred_score_b"] == score_b)
                        points_awarded = pool_details["payout"] if got_exact else 0.0
                
                group_lbl = f"Group {m['group_name']}" if m['group_name'] else m['stage'].replace("-", " ").title()
                city_lbl = m['city'].replace("-", " ").title()
                # Render Card
                st.html(textwrap.dedent(f"""
                <div class='match-card' style='background:rgba(30,41,59,0.3); border-color:rgba(255,255,255,0.05);'>
                    <div class='card-meta'>
                        <span>⚽ Match #{m['match_number']} | {group_lbl}</span>
                        <span class='badge-status badge-finished'>Finished</span>
                    </div>
                    <div class='teams-grid'>
                        <div class='team-block'><span class='team-flag'>{emoji_a}</span>{m['team_a']}</div>
                        <div class='middle-block'>
                            <span class='score-display'>{score_a} - {score_b}</span>
                            <span style='font-size:0.75rem; color:#fbbf24; font-weight:700; margin-top:5px;'>{winner_str}</span>
                        </div>
                        <div class='team-block'><span class='team-flag'>{emoji_b}</span>{m['team_b']}</div>
                    </div>
                    <div style='font-size:0.8rem; color:#cbd5e1; text-align:center; margin-top:5px;'>
                        💰 Pool: <b>{pool_details['total_pool']:.0f} pts</b> (Carryover: {pool_details['incoming_carry']:.0f})
                    </div>
                    <div class='card-footer' style='text-align:center;'>
                        <p style='margin:0; font-size:0.8rem; color:#cbd5e1;'>🏟 {m['stadium']} ({city_lbl})</p>
                    </div>
                </div>
                """))
                
                # Admin PDF download
                if st.session_state.username == "admin":
                    # Get predictions for this match
                    conn = sqlite3.connect(DB_PATH)
                    conn.row_factory = sqlite3.Row
                    preds_for_pdf = conn.execute("""
                        SELECT u.display_name, p.pred_score_a, p.pred_score_b 
                        FROM predictions p
                        JOIN users u ON p.user_id = u.id
                        WHERE p.match_id = ?
                    """, (mid,)).fetchall()
                    conn.close()
                    
                    pdf_bytes = generate_match_pdf(m, preds_for_pdf)
                    st.download_button(
                        label=f"📄 Download PDF Report",
                        data=pdf_bytes,
                        file_name=f"match_{mid}_{m['team_a']}_vs_{m['team_b']}.pdf",
                        mime="application/pdf",
                        key=f"dl_pdf_{mid}"
                    )
                
                if points_awarded is not None and user_pred is not None:
                    badge_color = "#10b981" if got_exact else "#ef4444"
                    st.html(textwrap.dedent(f"""
                    <div style='background:rgba(255,255,255,0.03); border-left:4px solid {badge_color}; border-radius:4px; padding:8px 12px; margin-top:-10px; margin-bottom:15px; font-size:0.85rem;'>
                        👤 Your Prediction: <b>{user_pred['pred_score_a']} - {user_pred['pred_score_b']}</b> 
                        | Awarded: <b style='color:{badge_color};'>{points_awarded:.1f} Points</b>
                    </div>
                    """))
                
                preds = get_predictions_summary(mid)
                if preds:
                    if st.button("📊 View Predictions Summary", key=f"sum_c_{mid}", use_container_width=True):
                        show_prediction_summary_dialog(mid, m['team_a'], m['team_b'], score_a, score_b, 1)
                    if pool_details['winners_count'] == 0:
                        st.warning(f"⚠️ Rollover carried forward: **{pool_details['outgoing_carry']:.0f} pts** (doubled!)")
                else:
                    st.caption("No predictions were submitted for this match.")
                st.html("<div style='margin-bottom:25px;'></div>")
        else:
            st.info("No matches finished yet.")

# --- Tab 3: Group Tables ---
with tab_groups:
    st.subheader("🌍 FIFA World Cup 2026 — Group Tables")
    st.write("Group standings from Wikipedia.")
    
    # Show last sync time and status (Admin only)
    if st.session_state.get("username") == "admin":
        with _SYNC_LOCK:
            last_sync_time = _LAST_SYNC.get("time")
            last_sync_err  = _LAST_SYNC.get("error")
            last_sync_reason = _LAST_SYNC.get("reason", "")
        
        col_sync_info, col_sync_btn = st.columns([3, 1])
        with col_sync_info:
            if last_sync_time:
                time_ago = int((datetime.utcnow() - last_sync_time).total_seconds() / 60)
                reason_txt = f" ({last_sync_reason})" if last_sync_reason else ""
                st.caption(f"Last sync: {time_ago} min ago{reason_txt}")
            st.caption("Wikipedia sync runs automatically after matches end.")
            if last_sync_err:
                st.caption(f"Last sync error: {last_sync_err}")
        with col_sync_btn:
            if st.button("🔄 Refresh Now", key="grp_manual_sync", use_container_width=True):
                with st.spinner("Fetching from Wikipedia..."):
                    WIKI_CACHE["fetched_at"] = None  # force re-fetch
                    _upd, _err = sync_scores_from_wiki()
                    with _SYNC_LOCK:
                        _LAST_SYNC["time"] = datetime.utcnow()
                        _LAST_SYNC["updated"] = _upd
                        _LAST_SYNC["error"] = _err
                        _LAST_SYNC["reason"] = "manual"
                if _err:
                    st.warning(f"Sync warning: {_err}")
                else:
                    st.success(f"Refreshed! {_upd} match(es) updated.")
        st.html("<hr style='border-color:rgba(251,191,36,0.2); margin: 10px 0 20px 0;'>")
    
    # Fetch group tables
    with st.spinner("Loading group standings..."):
        wiki_groups = parse_wiki_group_tables()
    
    if wiki_groups:
        # Display groups in 2-column grid
        group_letters = sorted(wiki_groups.keys())
        for i in range(0, len(group_letters), 2):
            col_left, col_right = st.columns(2, gap="large")
            for j, col in enumerate([col_left, col_right]):
                if i + j >= len(group_letters):
                    break
                grp_letter = group_letters[i + j]
                grp_teams = wiki_groups[grp_letter]
                
                with col:
                    # Group header
                    st.markdown(
                        f"<div style='background:linear-gradient(135deg,rgba(13,27,62,0.8),rgba(30,58,138,0.6)); "
                        f"border:1px solid rgba(251,191,36,0.3); border-radius:12px; padding:12px 16px; margin-bottom:4px;'>"
                        f"<h4 style='color:#fbbf24; margin:0; font-size:1.1rem;'>Group {grp_letter}</h4>"
                        f"</div>",
                        unsafe_allow_html=True
                    )
                    
                    # Table header
                    hdr_style = "background:rgba(251,191,36,0.15); color:#fbbf24; font-weight:600; font-size:0.78rem; padding:6px 8px; text-align:center;"
                    team_style = "color:#e2e8f0; font-size:0.82rem; padding:7px 8px; border-bottom:1px solid rgba(255,255,255,0.05);"
                    num_style  = "color:#cbd5e1; font-size:0.82rem; padding:7px 8px; text-align:center; border-bottom:1px solid rgba(255,255,255,0.05);"
                    
                    table_html = (
                        "<table style='width:100%; border-collapse:collapse; background:rgba(13,27,62,0.4); "
                        "border:1px solid rgba(255,255,255,0.08); border-radius:8px; overflow:hidden; margin-bottom:20px;'>"
                        "<thead><tr>"
                        f"<th style='{hdr_style} text-align:left; border-radius:0;'>Team</th>"
                        f"<th style='{hdr_style}'>MP</th>"
                        f"<th style='{hdr_style}'>W</th>"
                        f"<th style='{hdr_style}'>D</th>"
                        f"<th style='{hdr_style}'>L</th>"
                        f"<th style='{hdr_style}'>GF</th>"
                        f"<th style='{hdr_style}'>GA</th>"
                        f"<th style='{hdr_style}'>GD</th>"
                        f"<th style='{hdr_style}'>Pts</th>"
                        "</tr></thead><tbody>"
                    )
                    
                    for rank_idx, entry in enumerate(grp_teams):
                        # Highlight top 2 teams (qualify)
                        if rank_idx < 2:
                            row_bg = "background:rgba(34,197,94,0.08);"
                            pts_color = "#4ade80"
                        else:
                            row_bg = ""
                            pts_color = "#e2e8f0"
                        
                        table_html += (
                            f"<tr style='{row_bg}'>"
                            f"<td style='{team_style}'>{entry['team']}</td>"
                            f"<td style='{num_style}'>{entry['mp']}</td>"
                            f"<td style='{num_style}'>{entry['w']}</td>"
                            f"<td style='{num_style}'>{entry['d']}</td>"
                            f"<td style='{num_style}'>{entry['l']}</td>"
                            f"<td style='{num_style}'>{entry['gf']}</td>"
                            f"<td style='{num_style}'>{entry['ga']}</td>"
                            f"<td style='{num_style}'>{entry['gd']}</td>"
                            f"<td style='color:{pts_color}; font-weight:700; font-size:0.82rem; padding:7px 8px; text-align:center; border-bottom:1px solid rgba(255,255,255,0.05);'>{entry['pts']}</td>"
                            "</tr>"
                        )
                    
                    table_html += "</tbody></table>"
                    st.html(table_html)
    else:
        st.info("Group standings are not yet available. They will appear once the tournament begins and Wikipedia is updated.")

# --- Tab 4: Top Goalscorers ---
with tab_scorers:
    st.subheader("⚽ Top Goalscorers")
    st.write("Live tournament goalscorers from Wikipedia.")
    
    with st.spinner("Loading goalscorers..."):
        scorers = parse_wiki_goalscorers()
    
    if scorers:
        scorer_html = (
            "<table style='width:100%; max-width:700px; border-collapse:collapse; background:rgba(13,27,62,0.4); "
            "border:1px solid rgba(255,255,255,0.08); border-radius:12px; overflow:hidden; margin:0 auto;'>"
            "<thead><tr>"
            "<th style='background:rgba(251,191,36,0.15); color:#fbbf24; font-size:0.82rem; padding:9px 12px; text-align:center;'>#</th>"
            "<th style='background:rgba(251,191,36,0.15); color:#fbbf24; font-size:0.82rem; padding:9px 12px; text-align:left;'>Player</th>"
            "<th style='background:rgba(251,191,36,0.15); color:#fbbf24; font-size:0.82rem; padding:9px 12px; text-align:left;'>Team</th>"
            "<th style='background:rgba(251,191,36,0.15); color:#fbbf24; font-size:0.82rem; padding:9px 12px; text-align:center;'>Goals</th>"
            "</tr></thead><tbody>"
        )
        for idx, s in enumerate(scorers[:20]):  # top 20
            row_bg = "background:rgba(251,191,36,0.06);" if idx == 0 else ""
            scorer_html += (
                f"<tr style='{row_bg}'>"
                f"<td style='color:#94a3b8; font-size:0.82rem; padding:8px 12px; text-align:center; border-bottom:1px solid rgba(255,255,255,0.05);'>{idx+1}</td>"
                f"<td style='color:#e2e8f0; font-size:0.85rem; padding:8px 12px; border-bottom:1px solid rgba(255,255,255,0.05);'>{s['player']}</td>"
                f"<td style='color:#cbd5e1; font-size:0.82rem; padding:8px 12px; border-bottom:1px solid rgba(255,255,255,0.05);'>{s['team']}</td>"
                f"<td style='color:#fbbf24; font-weight:700; font-size:0.9rem; padding:8px 12px; text-align:center; border-bottom:1px solid rgba(255,255,255,0.05);'>{s['goals']}</td>"
                "</tr>"
            )
        scorer_html += "</tbody></table>"
        st.html(scorer_html)
    else:
        st.info("Goalscorer data is not yet available. It will appear once matches begin.")

# --- Tab 5: Teams & Lineups ---
with tab_lineups:
    st.subheader("📋 World Cup 2026 Teams & Lineups")
    st.write("Browse team rosters, qualified squad profiles, head coaches, and historic World Cup records.")
    
    # Load teams data
    teams_data_path = "teams_data.json"
    if os.path.exists(teams_data_path):
        import json
        with open(teams_data_path, "r", encoding="utf-8") as f:
            teams_data = json.load(f)
            
        # Get list of teams in alphabetical order of database names
        teams_list = sorted([t["db_name"] for t in teams_data])
        
        selected_team_name = st.selectbox("🔍 Select a Team Profile:", teams_list, index=0)
        
        # Find selected team object
        team = next((t for t in teams_data if t["db_name"] == selected_team_name), None)
        
        if team:
            # Let's show team card header
            st.html("<div style='margin-top:15px;'></div>")
            
            # Load local flag banner image directly as a full-width cover photo
            flag_path = team["flag_image_path"]
            if os.path.exists(flag_path):
                st.image(flag_path, use_container_width=True)
            else:
                st.warning("Banner image not found")
                
            st.markdown(f"""
            <div style='background:rgba(13, 27, 62, 0.45); border: 1px solid rgba(251, 191, 36, 0.2); border-radius: 12px; padding: 20px; margin-top: 15px;'>
                <h3 style='color:#fbbf24; margin:0 0 10px 0; font-size:1.8rem;'>{team['db_name']}</h3>
                <p style='margin:6px 0; color:#e2e8f0; font-size:1rem;'>👔 <b>Head Coach:</b> <span style='color:#fbbf24;'>{team['coach']}</span></p>
                <p style='margin:6px 0; color:#e2e8f0; font-size:1rem;'>🏆 <b>World Cup Appearances:</b> {team['appearances']}</p>
                <p style='margin:6px 0; color:#e2e8f0; font-size:1rem;'>🥇 <b>Best Result:</b> {team['best_result']}</p>
            </div>
            """, unsafe_allow_html=True)
                
            # History Section in expander or card
            st.markdown(f"""
            <div style='background:rgba(13, 27, 62, 0.3); border: 1px solid rgba(251, 191, 36, 0.15); border-radius: 12px; padding: 15px; margin-top: 15px; margin-bottom: 25px;'>
                <h4 style='color:#fbbf24; margin:0 0 8px 0; font-size:1.1rem;'>📖 World Cup History & Profile</h4>
                <p style='margin:0; color:#cbd5e1; font-size:0.92rem; line-height:1.5;'>{team['history']}</p>
            </div>
            """, unsafe_allow_html=True)
            
            # Player Lineups Grid
            st.markdown("<h4 style='color:#fbbf24; margin-bottom:15px;'>⚽ Official Squad Roster</h4>", unsafe_allow_html=True)
            
            gk, df, mf, fw = team["goalkeepers"], team["defenders"], team["midfielders"], team["forwards"]
            
            # Four columns layout
            col_gk, col_df, col_mf, col_fw = st.columns(4)
            
            with col_gk:
                gk_html = "<br>".join([f"• {p}" for p in gk]) if gk else "No players listed"
                st.markdown(f"""
                <div style='background:rgba(13, 27, 62, 0.45); border: 1px solid rgba(251, 191, 36, 0.25); border-radius: 12px; padding: 15px; min-height: 420px; box-shadow: 0 4px 15px rgba(0,0,0,0.2);'>
                    <h5 style='color:#fbbf24; text-align:center; border-bottom:1px solid rgba(251,191,36,0.25); padding-bottom:8px; margin-top:0; margin-bottom:12px;'>🧤 Goalkeepers ({len(gk)})</h5>
                    <div style='color:#cbd5e1; font-size:0.88rem; line-height:1.6;'>{gk_html}</div>
                </div>
                """, unsafe_allow_html=True)
                
            with col_df:
                df_html = "<br>".join([f"• {p}" for p in df]) if df else "No players listed"
                st.markdown(f"""
                <div style='background:rgba(13, 27, 62, 0.45); border: 1px solid rgba(251, 191, 36, 0.25); border-radius: 12px; padding: 15px; min-height: 420px; box-shadow: 0 4px 15px rgba(0,0,0,0.2);'>
                    <h5 style='color:#fbbf24; text-align:center; border-bottom:1px solid rgba(251,191,36,0.25); padding-bottom:8px; margin-top:0; margin-bottom:12px;'>🛡️ Defenders ({len(df)})</h5>
                    <div style='color:#cbd5e1; font-size:0.88rem; line-height:1.6;'>{df_html}</div>
                </div>
                """, unsafe_allow_html=True)
                
            with col_mf:
                mf_html = "<br>".join([f"• {p}" for p in mf]) if mf else "No players listed"
                st.markdown(f"""
                <div style='background:rgba(13, 27, 62, 0.45); border: 1px solid rgba(251, 191, 36, 0.25); border-radius: 12px; padding: 15px; min-height: 420px; box-shadow: 0 4px 15px rgba(0,0,0,0.2);'>
                    <h5 style='color:#fbbf24; text-align:center; border-bottom:1px solid rgba(251,191,36,0.25); padding-bottom:8px; margin-top:0; margin-bottom:12px;'>⚙️ Midfielders ({len(mf)})</h5>
                    <div style='color:#cbd5e1; font-size:0.88rem; line-height:1.6;'>{mf_html}</div>
                </div>
                """, unsafe_allow_html=True)
                
            with col_fw:
                fw_html = "<br>".join([f"• {p}" for p in fw]) if fw else "No players listed"
                st.markdown(f"""
                <div style='background:rgba(13, 27, 62, 0.45); border: 1px solid rgba(251, 191, 36, 0.25); border-radius: 12px; padding: 15px; min-height: 420px; box-shadow: 0 4px 15px rgba(0,0,0,0.2);'>
                    <h5 style='color:#fbbf24; text-align:center; border-bottom:1px solid rgba(251,191,36,0.25); padding-bottom:8px; margin-top:0; margin-bottom:12px;'>⚡ Forwards ({len(fw)})</h5>
                    <div style='color:#cbd5e1; font-size:0.88rem; line-height:1.6;'>{fw_html}</div>
                </div>
                """, unsafe_allow_html=True)
                
            st.html("<div style='margin-bottom:30px;'></div>")
    else:
        st.error("Teams and lineups data file not found. Please contact the administrator.")

# --- Tab 4: Admin Controls (Only if Admin logged in) ---
if st.session_state.username == "admin" and tab_admin:
    with tab_admin[0]:
        st.subheader("Tournament Admin Controls")
        
        admin_tabs = st.tabs(["⚡ Match Scores", "📅 Schedule Match", "👥 User Management"])
        
        # Sub-tab A: Match Score Updates
        with admin_tabs[0]:
            st.write("Update current score of ongoing matches, or set final score and finalize the match.")
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            unfinished = conn.execute("SELECT * FROM matches WHERE finished = 0 ORDER BY kickoff_time ASC").fetchall()
            conn.close()
            
            if unfinished:
                for m in unfinished:
                    mid = m["id"]
                    st.markdown(f"#### ⚽ {m['team_a']} vs {m['team_b']} ({m['group_name']})")
                    st.write(f"Kickoff: {m['kickoff_time']}")
                    
                    try:
                        exist_kickoff = datetime.strptime(m['kickoff_time'], '%Y-%m-%d %H:%M:%S')
                    except Exception:
                        exist_kickoff = get_nepal_time()
                        
                    with st.form(key=f"form_admin_finish_{mid}"):
                        col1, col2 = st.columns(2)
                        with col1:
                            sc_a = st.number_input(f"{m['team_a']} Goals", min_value=0, max_value=20, value=m['score_a'] if m['score_a'] is not None else 0, step=1, key=f"adm_sa_{mid}")
                        with col2:
                            sc_b = st.number_input(f"{m['team_b']} Goals", min_value=0, max_value=20, value=m['score_b'] if m['score_b'] is not None else 0, step=1, key=f"adm_sb_{mid}")
                        
                        status_opt = ["Ongoing / Live", "Completed / Finished"]
                        m_status = st.selectbox("Update Match Status", options=status_opt, index=0, key=f"adm_status_{mid}")
                        
                        st.markdown("<p style='margin-bottom:2px; font-weight:600;'>Auto-sync from FIFA Match URL (Optional):</p>", unsafe_allow_html=True)
                        fifa_url_in = st.text_input("FIFA Match URL", value="", key=f"fifa_url_{mid}", label_visibility="collapsed")
                        fetch_btn = st.form_submit_button(f"🔌 Fetch & Update from FIFA URL", use_container_width=True)
                        if fetch_btn:
                            if not fifa_url_in:
                                st.error("Please paste a valid FIFA Match URL first.")
                            else:
                                with st.spinner("Fetching score..."):
                                    res, err = scrape_score_from_fifa_url(fifa_url_in)
                                if err:
                                    st.error(f"Error fetching from FIFA: {err}")
                                elif res:
                                    is_finished = 1 if res["is_finished"] else 0
                                    conn = sqlite3.connect(DB_PATH)
                                    conn.execute("UPDATE matches SET score_a = ?, score_b = ?, finished = ?, match_time = ? WHERE id = ?", (res["home_score"], res["away_score"], is_finished, res["match_time"], mid))
                                    conn.commit()
                                    conn.close()
                                    st.success(f"Success! Updated Match #{mid} to: {res['home_team']} {res['home_score']} – {res['away_score']} {res['away_team']} ({res['match_time']})")
                                    st.rerun()

                        
                        st.write("**Update Kickoff Time (NPT):**")
                        c1, c2 = st.columns(2)
                        with c1:
                            new_date = st.date_input("Kickoff Date", value=exist_kickoff.date(), key=f"adm_date_{mid}")
                        with c2:
                            new_time = st.time_input("Kickoff Time", value=exist_kickoff.time(), key=f"adm_time_{mid}")
                        
                        adm_submit = st.form_submit_button(f"Update Match #{mid}", use_container_width=True)
                        if adm_submit:
                            is_finished = 1 if m_status == "Completed / Finished" else 0
                            new_kickoff = datetime.combine(new_date, new_time).strftime('%Y-%m-%d %H:%M:%S')
                            conn = sqlite3.connect(DB_PATH)
                            conn.execute("UPDATE matches SET score_a = ?, score_b = ?, finished = ?, kickoff_time = ? WHERE id = ?", (sc_a, sc_b, is_finished, new_kickoff, mid))
                            conn.commit()
                            conn.close()
                            st.success(f"Match #{mid} updated successfully! ({m_status})")
                            st.rerun()
                        st.html("<hr style='border-color:rgba(255,255,255,0.05);'>")
            else:
                st.success("🎉 All scheduled matches have been finished!")
                
        # Sub-tab B: Schedule Future Matches
        with admin_tabs[1]:
            st.subheader("Add Future Match")
            with st.form("form_add_match"):
                team_a_in = st.text_input("Team A Name").strip()
                team_b_in = st.text_input("Team B Name").strip()
                group_in = st.text_input("Group (if Group Stage, e.g. A, B or blank)").strip()
                stage_in = st.selectbox("Stage", ["group-stage", "round-of-32", "round-of-16", "quarter-finals", "semi-finals", "third-place", "final"])
                stadium_in = st.text_input("Stadium Name", "SoFi Stadium").strip()
                city_in = st.text_input("Host City Name", "los-angeles").strip()
                
                nep_now = get_nepal_time()
                c1, c2 = st.columns(2)
                with c1:
                    kickoff_date = st.date_input("Kickoff Date", nep_now.date())
                with c2:
                    kickoff_time_in = st.time_input("Kickoff Time", nep_now.time())
                
                add_btn = st.form_submit_button("Add Match to Schedule", use_container_width=True)
                if add_btn:
                    if not team_a_in or not team_b_in or not stage_in:
                        st.error("Team names and Stage are required.")
                    else:
                        k_datetime = datetime.combine(kickoff_date, kickoff_time_in).strftime('%Y-%m-%d %H:%M:%S')
                        conn = sqlite3.connect(DB_PATH)
                        cursor = conn.cursor()
                        cursor.execute("SELECT MAX(match_number) FROM matches")
                        max_num = cursor.fetchone()[0]
                        next_match_num = (max_num + 1) if max_num is not None else 105
                        
                        cursor.execute("""
                            INSERT INTO matches (match_number, team_a, team_b, group_name, stage, stadium, city, kickoff_time, score_a, score_b, finished)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, 0)
                        """, (next_match_num, team_a_in, team_b_in, group_in, stage_in, stadium_in, city_in, k_datetime))
                        conn.commit()
                        conn.close()
                        st.success(f"Added future match #{next_match_num}: {team_a_in} vs {team_b_in} on {k_datetime}")
                        st.rerun()
                        
        # Sub-tab C: User Account Management
        with admin_tabs[2]:
            st.subheader("User Account Management")
            
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            all_users = conn.execute("SELECT * FROM users ORDER BY username ASC").fetchall()
            conn.close()
            
            user_options = [f"{u['display_name']} (@{u['username']})" for u in all_users]
            selected_user_str = st.selectbox("Select User to Manage", options=user_options)
            
            if selected_user_str:
                sel_idx = user_options.index(selected_user_str)
                sel_user = all_users[sel_idx]
                su_id = sel_user["id"]
                su_uname = sel_user["username"]
                su_display = sel_user["display_name"]
                su_active = sel_user["is_active"]
                
                st.markdown(f"**Managing User:** {su_display} (`@{su_uname}`)")
                
                c1, c2 = st.columns(2)
                with c1:
                    status_label = "Enabled" if su_active == 1 else "Disabled"
                    st.write(f"Current Status: **{status_label}**")
                    toggle_btn_lbl = "Disable User" if su_active == 1 else "Enable User"
                    if st.button(toggle_btn_lbl, use_container_width=True):
                        new_active = 0 if su_active == 1 else 1
                        conn = sqlite3.connect(DB_PATH)
                        conn.execute("UPDATE users SET is_active = ? WHERE id = ?", (new_active, su_id))
                        conn.commit()
                        conn.close()
                        st.success(f"User status updated for {su_display}!")
                        st.rerun()
                        
                with c2:
                    with st.expander("Reset Password"):
                        new_pass = st.text_input("New Password", type="password", key=f"reset_pass_{su_id}")
                        if st.button("Save New Password", use_container_width=True, key=f"reset_btn_{su_id}"):
                            if not new_pass:
                                st.error("Password cannot be empty.")
                            else:
                                conn = sqlite3.connect(DB_PATH)
                                conn.execute("UPDATE users SET password = ? WHERE id = ?", (hash_password(new_pass), su_id))
                                conn.commit()
                                conn.close()
                                st.success(f"Password reset successfully for {su_display}!")
                                
            st.html("<hr style='border-color:rgba(255,255,255,0.05);'>")
            st.subheader("➕ Create New User")
            with st.form("form_create_user"):
                new_display_name = st.text_input("Full Display Name").strip()
                new_username = st.text_input("Username (alphanumeric only, e.g. john)").strip().lower()
                new_password_in = st.text_input("Password", type="password")
                
                create_user_btn = st.form_submit_button("Create User", use_container_width=True)
                if create_user_btn:
                    if not new_display_name or not new_username or not new_password_in:
                        st.error("All fields are required.")
                    elif not new_username.isalnum():
                        st.error("Username must be alphanumeric.")
                    else:
                        conn = sqlite3.connect(DB_PATH)
                        existing = conn.execute("SELECT id FROM users WHERE username = ?", (new_username,)).fetchone()
                        if existing:
                            st.error(f"Username '@{new_username}' already exists.")
                            conn.close()
                        else:
                            conn.execute(
                                "INSERT INTO users (username, password, display_name, is_active) VALUES (?, ?, ?, 1)",
                                (new_username, hash_password(new_password_in), new_display_name)
                            )
                            conn.commit()
                            conn.close()
                            st.success(f"User '{new_display_name}' (@{new_username}) created successfully!")
                            st.rerun()
