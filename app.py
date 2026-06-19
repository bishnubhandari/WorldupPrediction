import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime, timedelta
import hashlib
import os
import textwrap
from fpdf import FPDF

# --- Page Config ---
st.set_page_config(
    page_title="FIFA World Cup 2026 — Score Predictor",
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- Nepal timezone helpers ---
import re
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

# --- Password Hashing Helper ---
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def check_password(hashed, input_password):
    return hashed == hashlib.sha256(input_password.encode()).hexdigest()

# --- Database Initialization ---
DB_PATH = "worldcup.db"

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
            finished INTEGER DEFAULT 0
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
            cursor.execute("UPDATE users SET password = ?, display_name = ? WHERE username = ?", (password, display_name, username))
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
    /* Qatar 2026 World Cup Theme Styling */
    .stApp {
        background: linear-gradient(135deg, #0f172a 0%, #020617 100%);
        color: #f8fafc;
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
        color: #94a3b8;
        margin-bottom: 10px;
    }
    
    /* Responsive Match Card */
    .match-card {
        background: rgba(15, 23, 42, 0.6);
        border: 1px solid rgba(251, 191, 36, 0.15);
        border-radius: 12px;
        padding: 18px;
        margin-bottom: 18px;
        box-shadow: 0 4px 20px rgba(0,0,0,0.4);
        transition: border 0.3s ease;
    }
    .match-card:hover {
        border-color: rgba(251, 191, 36, 0.4);
    }
    
    .card-meta {
        display: flex;
        justify-content: space-between;
        font-size: 0.8rem;
        color: #94a3b8;
        border-bottom: 1px solid rgba(255,255,255,0.05);
        padding-bottom: 6px;
        margin-bottom: 12px;
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
        font-size: 1.1rem;
    }
    .team-flag {
        font-size: 2rem;
        display: block;
        margin-bottom: 4px;
    }
    
    .middle-block {
        display: flex;
        flex-direction: column;
        align-items: center;
        padding: 0 15px;
    }
    
    .vs-badge {
        padding: 3px 12px;
        background: rgba(251, 191, 36, 0.1);
        border: 1px solid #fbbf24;
        border-radius: 20px;
        color: #fbbf24;
        font-weight: 700;
        font-size: 0.8rem;
    }
    
    .score-display {
        font-size: 1.8rem;
        font-weight: 800;
        color: #f8fafc;
        letter-spacing: 5px;
    }
    
    .card-footer {
        font-size: 0.85rem;
        border-top: 1px solid rgba(255,255,255,0.05);
        padding-top: 8px;
        margin-top: 10px;
    }
    
    .badge-status {
        display: inline-block;
        padding: 2px 8px;
        border-radius: 6px;
        font-size: 0.7rem;
        font-weight: 700;
        text-transform: uppercase;
    }
    .badge-open { background: rgba(16, 185, 129, 0.15); color: #10b981; border: 1px solid #10b981; }
    .badge-locked { background: rgba(245, 158, 11, 0.15); color: #f59e0b; border: 1px solid #f59e0b; }
    .badge-finished { background: rgba(148, 163, 184, 0.15); color: #94a3b8; border: 1px solid #94a3b8; }
    
    /* Leaderboard Table */
    .table-leaderboard {
        width: 100%;
        border-collapse: collapse;
        margin-top: 15px;
        border-radius: 8px;
        overflow: hidden;
    }
    .table-leaderboard th {
        background-color: #fbbf24;
        color: #0f172a;
        padding: 12px;
        font-weight: 700;
        text-align: left;
    }
    .table-leaderboard td {
        padding: 12px;
        background-color: rgba(30, 41, 59, 0.4);
        border-bottom: 1px solid rgba(255,255,255,0.05);
    }
    .table-leaderboard tr:hover td {
        background-color: rgba(251, 191, 36, 0.05);
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
now = get_nepal_time()
st.html(f"""
<div style='text-align: center; margin-top: -15px;'>
    <h1 class='worldcup-title'>🏆 FIFA WORLD CUP 2026</h1>
    <div class='worldcup-subtitle'>Socre predictor</div>
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
            
    st.sidebar.info("""
    **💡 Predefined Accounts:**
    *   `admin` / `Wc2026!Adm#98`
    *   `knshrestha` / `KnS#7202`
    *   `ksvgautam` / `KsvG!4091`
    *   `ashishkhadka` / `Ashish#8301`
    """)
else:
    st.sidebar.html(f"""
    <div style='background:rgba(251,191,36,0.1); border:1px solid #fbbf24; border-radius:8px; padding:15px; text-align:center; margin-bottom:15px;'>
        <p style='margin:0; font-size:0.85rem; color:#94a3b8;'>Logged in as:</p>
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
            <p style='margin:0; font-size:0.9rem; color:#94a3b8;'>
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
            <p style='margin:0; font-size:0.9rem; color:#94a3b8;'>
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
                    status_lbl = "<span style='color:#94a3b8;'>Incorrect</span>"
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

# --- Main App Pages ---
tabs = ["🏆 Leaderboard", "⚽ Matches & Predictions"]
if st.session_state.username == "admin":
    tabs.append("🛠️ Admin Controls")

tab_leaderboard, tab_matches, *tab_admin = st.tabs(tabs)

# --- Tab 1: Leaderboard ---
with tab_leaderboard:
    st.subheader("Recent Predictions & Live Tracker")
    st.write("Click on any recent match to view predictions submitted by all users, including live eligibility and points won.")
    
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
                            <div style='font-size: 0.68rem; color: #94a3b8; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-top: 2px;' title='{winner_names}'>👑 {winner_names}</div>
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
                    status_badge = "<span class='badge-status badge-locked' style='background:rgba(239,68,68,0.15); color:#ef4444; border-color:#ef4444; display:block; margin:4px auto; text-align:center;'>🔴 LIVE</span>"
                else:
                    score_str = "<b style='color:#94a3b8; font-size:1.1rem;'>vs</b>"
                    status_badge = "<span class='badge-status badge-locked' style='display:block; margin:4px auto; text-align:center;'>Locked</span>"
                
                st.html(f"""
                <div style='background: rgba(15, 23, 42, 0.4); border: 1px solid rgba(251, 191, 36, 0.15); border-radius: 12px; padding: 12px; text-align: center; margin-bottom: 8px;'>
                    <div style='font-size:0.75rem; color:#fbbf24; font-weight:700; margin-bottom:4px;'>Match #{m['match_number']}</div>
                    <div style='font-size: 0.9rem; font-weight: 600; color: #f8fafc; margin-bottom: 5px;'>{emoji_a} {m['team_a']}</div>
                    <div style='margin: 5px 0;'>{score_str}</div>
                    <div style='font-size: 0.9rem; font-weight: 600; color: #f8fafc; margin-top: 5px;'>{emoji_b} {m['team_b']}</div>
                    <div style='margin-top:8px;'>{status_badge}</div>
                    <div style='font-size:0.75rem; color:#94a3b8; margin-top:6px;'>🏟 {city_lbl}</div>
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
                if st.session_state.user_id:
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
                    <div style='font-size:0.8rem; color:#94a3b8; text-align:center; margin-top:5px;'>
                        💰 Match Pool: <b>{pool_details['total_pool']:.0f} pts</b> (Carryover: {pool_details['incoming_carry']:.0f})
                    </div>
                    <div class='card-footer' style='text-align:center;'>
                        <p style='margin:0; font-size:0.8rem; color:#94a3b8;'>🏟 {m['stadium']} ({city_lbl})</p>
                        <p style='margin:2px 0 0 0; font-size:0.8rem; color:#94a3b8;'>Kickoff: {k_time.strftime('%Y-%m-%d %H:%M')} NPT</p>
                    </div>
                </div>
                """))
                
                if st.session_state.user_id:
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
                else:
                    st.html("<p style='text-align:center; color:#94a3b8; font-size:0.85rem; margin-top:-10px; margin-bottom:20px;'>🔒 Login to submit prediction</p>")
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
                    st.html(textwrap.dedent(f"""
                    <div class='match-card' style='border-color:#ef4444;'>
                        <div class='card-meta'>
                            <span>⚽ Match #{m['match_number']} | {group_lbl}</span>
                            <span class='badge-status badge-locked' style='background:rgba(239,68,68,0.15); color:#ef4444; border-color:#ef4444;'>🔴 LIVE</span>
                        </div>
                        <div class='teams-grid'>
                            <div class='team-block'><span class='team-flag'>{emoji_a}</span>{m['team_a']}</div>
                            <div class='middle-block'>
                                <span class='score-display' style='color:#ef4444;'>{live_a} - {live_b}</span>
                                <span style='font-size:0.75rem; color:#f59e0b; font-weight:700; margin-top:5px;'>Live Score</span>
                            </div>
                            <div class='team-block'><span class='team-flag'>{emoji_b}</span>{m['team_b']}</div>
                        </div>
                        <div style='font-size:0.8rem; color:#94a3b8; text-align:center; margin-top:5px;'>
                            💰 Match Pool: <b>{pool_details['total_pool']:.0f} pts</b> (Carryover: {pool_details['incoming_carry']:.0f})
                        </div>
                        <div class='card-footer' style='text-align:center;'>
                            <p style='margin:0; font-size:0.8rem; color:#94a3b8;'>🏟 {m['stadium']} ({city_lbl})</p>
                            <p style='margin:2px 0 0 0; font-size:0.8rem; color:#94a3b8;'>Started: {k_time.strftime('%Y-%m-%d %H:%M')} NPT</p>
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
                        <div style='font-size:0.8rem; color:#94a3b8; text-align:center; margin-top:5px;'>
                            💰 Match Pool: <b>{pool_details['total_pool']:.0f} pts</b> (Carryover: {pool_details['incoming_carry']:.0f})
                        </div>
                        <div class='card-footer' style='text-align:center;'>
                            <p style='margin:0; font-size:0.8rem; color:#94a3b8;'>🏟 {m['stadium']} ({city_lbl})</p>
                            <p style='margin:2px 0 0 0; font-size:0.8rem; color:#94a3b8;'>Kickoff: {k_time.strftime('%Y-%m-%d %H:%M')} NPT</p>
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
        st.html("<h3 style='color:#94a3b8; border-bottom:2px solid #94a3b8; padding-bottom:5px;'>✅ Finished</h3>")
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
                if st.session_state.user_id:
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
                    <div style='font-size:0.8rem; color:#94a3b8; text-align:center; margin-top:5px;'>
                        💰 Pool: <b>{pool_details['total_pool']:.0f} pts</b> (Carryover: {pool_details['incoming_carry']:.0f})
                    </div>
                    <div class='card-footer' style='text-align:center;'>
                        <p style='margin:0; font-size:0.8rem; color:#94a3b8;'>🏟 {m['stadium']} ({city_lbl})</p>
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

# --- Tab 3: Admin Controls (Only if Admin logged in) ---
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
                    
                    with st.form(key=f"form_admin_finish_{mid}"):
                        col1, col2 = st.columns(2)
                        with col1:
                            sc_a = st.number_input(f"{m['team_a']} Goals", min_value=0, max_value=20, value=m['score_a'] if m['score_a'] is not None else 0, step=1, key=f"adm_sa_{mid}")
                        with col2:
                            sc_b = st.number_input(f"{m['team_b']} Goals", min_value=0, max_value=20, value=m['score_b'] if m['score_b'] is not None else 0, step=1, key=f"adm_sb_{mid}")
                        
                        status_opt = ["Ongoing / Live", "Completed / Finished"]
                        m_status = st.selectbox("Update Match Status", options=status_opt, index=0, key=f"adm_status_{mid}")
                        
                        adm_submit = st.form_submit_button(f"Update Match #{mid}", use_container_width=True)
                        if adm_submit:
                            is_finished = 1 if m_status == "Completed / Finished" else 0
                            conn = sqlite3.connect(DB_PATH)
                            conn.execute("UPDATE matches SET score_a = ?, score_b = ?, finished = ? WHERE id = ?", (sc_a, sc_b, is_finished, mid))
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
