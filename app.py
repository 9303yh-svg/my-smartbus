import streamlit as st
import pandas as pd
import folium
from folium import plugins
import streamlit.components.v1 as components
import requests
import zipfile
import io
import sqlite3
import os
import googlemaps
from datetime import datetime
import pytz
import polyline
import time

# --- הגדרות מערכת ---
st.set_page_config(page_title="SmartBus Pro", page_icon="🚍", layout="wide")
DB_FILE = 'gtfs_israel.db'
ISRAEL_TZ = pytz.timezone('Asia/Jerusalem')

# --- התחברות לגוגל ---
try:
    api_key = st.secrets["GOOGLE_API_KEY"]
    gmaps = googlemaps.Client(key=api_key)
except:
    st.error("⚠️ מפתח Google API חסר.")
    st.stop()

# --- מנוע ה-SQL (אופטימיזציה מקסימלית) ---
@st.cache_resource(show_spinner=False)
def init_database():
    if os.path.exists(DB_FILE): return True
    try:
        url = "https://gtfs.mot.gov.il/gtfsfiles/israel-public-transportation.zip"
        with st.spinner('📥 טוען נתונים (חד פעמי)...'):
            r = requests.get(url)
            z = zipfile.ZipFile(io.BytesIO(r.content))
            conn = sqlite3.connect(DB_FILE)
            pd.read_csv(z.open('routes.txt'), usecols=['route_id', 'route_short_name', 'route_long_name']).to_sql('routes', conn, if_exists='replace', index=False)
            trips = pd.read_csv(z.open('trips.txt'), usecols=['route_id', 'shape_id'])
            trips.drop_duplicates(subset=['route_id']).to_sql('trips', conn, if_exists='replace', index=False)
            for chunk in pd.read_csv(z.open('shapes.txt'), chunksize=100000):
                chunk.to_sql('shapes', conn, if_exists='append', index=False)
            conn.execute("CREATE INDEX idx_route_name ON routes(route_short_name)")
            conn.execute("CREATE INDEX idx_shape_id ON shapes(shape_id)")
            conn.close()
        return True
    except: return False

def get_routes_sql(line_num):
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query("SELECT * FROM routes WHERE route_short_name = ?", conn, params=(line_num,))
    conn.close()
    return df

def get_shape_sql(route_id):
    conn = sqlite3.connect(DB_FILE)
    try:
        sid = pd.read_sql_query("SELECT shape_id FROM trips WHERE route_id = ?", conn, params=(route_id,)).iloc[0]['shape_id']
        df = pd.read_sql_query("SELECT shape_pt_lat, shape_pt_lon FROM shapes WHERE shape_id = ? ORDER BY shape_pt_sequence", conn, params=(sid,))
        
        # === התיקון הקריטי למניעת קריסות ===
        # לוקח רק כל נקודה עשירית! זה מקטין את העומס ב-90%
        return list(zip(df['shape_pt_lat'].values[::10], df['shape_pt_lon'].values[::10]))
    except: return []
    finally: conn.close()

# --- פונקציית עזר: יצירת תקציר מסלול (כמו במוביט) ---
def generate_route_summary(leg):
    summary_parts = []
    for step in leg['steps']:
        if step['travel_mode'] == 'WALKING':
            summary_parts.append("🚶")
        elif step['travel_mode'] == 'TRANSIT':
            line = step['transit_details']['line']['short_name']
            summary_parts.append(f"🚌 {line}")
    
    # מאחד את החלקים עם חצים ומנקה כפילויות של הליכה
    clean_summary = []
    for x in summary_parts:
        if len(clean_summary) == 0 or clean_summary[-1] != x:
            clean_summary.append(x)
            
    return " ➔ ".join(clean_summary)

# --- עיצוב CSS ---
st.markdown("""
    <style>
    .stApp { direction: rtl; }
    .live-step { background-color: #e3f2fd; border-right: 6px solid #2196f3; padding: 15px; margin-bottom: 10px; border-radius: 8px; font-size: 18px; }
    .slider-val { font-size: 20px; font-weight: bold; color: #333; text-align: center; background: #eee; padding: 5px; border-radius: 5px; margin-bottom: 5px; }
    .wallet-card { background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%); color: white; padding: 20px; border-radius: 15px; text-align: right; }
    </style>
""", unsafe_allow_html=True)

# --- סרגל צד ---
with st.sidebar:
    st.header("⚙️ הגדרות")
    max_walking = st.slider("מקסימום הליכה (דקות):", 0, 30, 10)
    st.markdown(f"<div class='slider-val'>🚶 {max_walking} דקות</div>", unsafe_allow_html=True)
    
    if st.button("🔄 אתחול מערכת (תקלה)"):
        st.session_state.clear()
        st.rerun()

st.title("🚍 SmartBus Lite")

# טאבים
tab_nav, tab_lines, tab_stations, tab_wallet = st.tabs(["🗺️ תכנון וניווט", "🔢 איתור קו", "🚏 תחנות סביבי", "💳 ארנק"])

# ==========================================
# 1. תכנון מסלול (משופר ויציב)
# ==========================================
with tab_nav:
    if 'live_nav_steps' not in st.session_state:
        st.session_state.live_nav_steps = None

    # --- מסך ניווט לייב (טקסט בלבד למניעת קריסה) ---
    if st.session_state.live_nav_steps:
        st.info("🟢 ניווט פעיל")
        
        if st.button("❌ יציאה"):
            st.session_state.live_nav_steps = None
            st.rerun()

        # כפתור חיצוני לגוגל (הכי בטוח)
        origin_s = st.session_state.live_origin
        dest_s = st.session_state.live_dest
        gmaps_link = f"https://www.google.com/maps/dir/?api=1&origin={origin_s}&destination={dest_s}&travelmode=transit"
        
        st.markdown(f"""
            <a href="{gmaps_link}" target="_blank">
                <button style="width:100%; background-color:#4285F4; color:white; font-size:18px; padding:12px; border-radius:8px; border:none; margin-bottom:10px;">
                    🔊 פתח ניווט קולי מלא (Google Maps)
                </button>
            </a>
        """, unsafe_allow_html=True)

        st.subheader("הוראות:")
        for step in st.session_state.live_nav_steps:
            instr = step['html_instructions']
            dist = step['distance']['text']
            icon = "🚶" if step['travel_mode'] == 'WALKING' else "🚌"
            st.markdown(f"<div class='live-step'>{icon} {instr} ({dist})</div>", unsafe_allow_html=True)

    else:
        # --- טופס חיפוש ---
        with st.form("nav_form"):
            c1, c2 = st.columns(2)
            with c1: origin = st.text_input("מוצא", "המיקום שלי")
            with c2: dest = st.text_input("יעד", "עזריאלי תל אביב")
            
            t_col1, t_col2 = st.columns(2)
            with t_col1: time_mode = st.selectbox("זמן", ["יציאה עכשיו", "יציאה ב...", "הגעה ב..."])
            
            req_time = datetime.now()
            is_arrival = False
            if time_mode != "יציאה עכשיו":
                with t_col2: 
                    t_input = st.time_input("שעה")
                    req_time = datetime.combine(datetime.now().date(), t_input)
                    if "הגעה" in time_mode: is_arrival = True

            submit_nav = st.form_submit_button("חפש מסלול 🚀")

        if submit_nav:
            with st.spinner('מחשב...'):
                try:
                    real_origin = "תחנה מרכזית נתניה" if origin == "המיקום שלי" else origin
                    params = {
                        "origin": real_origin, "destination": dest,
                        "mode": "transit", "transit_mode": "bus",
                        "alternatives": True, "language": "he"
                    }
                    if is_arrival: params["arrival_time"] = req_time
                    else: params["departure_time"] = req_time
                    
                    routes = gmaps.directions(**params)
                    
                    if routes:
                        st.success(f"נמצאו {len(routes)} אפשרויות:")
                        options = []
                        for i, r in enumerate(routes):
                            leg = r['legs'][0]
                            w_sec = sum([s['duration']['value'] for s in leg['steps'] if s['travel_mode']=='WALKING'])
                            w_min = int(w_sec/60)
                            
                            # יצירת הסיכום הקצר (כמו מוביט)
                            route_summary = generate_route_summary(leg)
                            
                            lbl = f"אופציה {i+1}: {route_summary} ({leg['duration']['text']})"
                            if w_min > max_walking: lbl += " ⚠️ הליכה מרובה"
                            
                            options.append({"label": lbl, "data": r})
                        
                        sel = st.radio("בחר מסלול:", options, format_func=lambda x: x['label'])
                        
                        if sel:
                            r = sel['data']
                            leg = r['legs'][0]
                            
                            # הפעלת ניווט לייב - שומרים רק טקסט! לא את כל המפה
                            if st.button("🚶‍♂️ התחל ניווט לייב", type="primary"):
                                st.session_state.live_nav_steps = leg['steps']
                                st.session_state.live_origin = real_origin
                                st.session_state.live_dest = dest
                                st.rerun()

                            # מפה סטטית - מונעת קריסה
                            m = folium.Map(location=[leg['start_location']['lat'], leg['start_location']['lng']], zoom_start=14)
                            plugins.LocateControl(auto_start=False).add_to(m)
                            folium.TileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}', attr='Traffic', overlay=True).add_to(m)
                            
                            pts_all = []
                            for step in leg['steps']:
                                pts = polyline.decode(step['polyline']['points'])
                                pts_all.extend(pts)
                                color = "#581845" if step['travel_mode'] == 'TRANSIT' else "blue"
                                folium.PolyLine(pts, color=color, weight=5, opacity=0.7).add_to(m)
                            
                            m.fit_bounds(pts_all)
                            components.html(m._repr_html_(), height=400)
                    else: st.error("לא נמצא מסלול.")
                except Exception as e: st.error(f"שגיאה: {e}")

# ==========================================
# 2. איתור קו (SQL) - "דיאטה" מוגברת
# ==========================================
with tab_lines:
    if init_database():
        l_num = st.text_input("מספר קו:", "")
        if l_num:
            res = get_routes_sql(l_num)
            if not res.empty:
                opts = {f"{r['route_long_name']}": r['route_id'] for i, r in res.iterrows()}
                s_opt = st.selectbox("כיוון:", list(opts.keys()))
                if st.button("הצג"):
                    # שימוש בפונקציה שדוגמת רק כל נקודה עשירית
                    pts = get_shape_sql(opts[s_opt])
                    if pts:
                        mid = pts[len(pts)//2]
                        m2 = folium.Map(location=mid, zoom_start=12)
                        folium.TileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}', attr='Traffic', overlay=True).add_to(m2)
                        folium.PolyLine(pts, color="purple", weight=5).add_to(m2)
                        components.html(m2._repr_html_(), height=500)
            else: st.warning("לא נמצא")

# ==========================================
# 3. תחנות סביבי (יציב)
# ==========================================
with tab_stations:
    col_me, col_search = st.columns([1, 2])
    with col_search: q_stat = st.text_input("חיפוש כתובת:", "")
    with col_me:
        st.write("") 
        st.write("") 
        use_gps = st.button("📍 מצא סביבי")

    if use_gps or (q_stat and st.button("חפש")):
        loc_center = None
        if use_gps:
            st.info("👈 לחץ על הכפתור השחור במפה למיקום")
            loc_center = [32.08, 34.78]
        elif q_stat:
            geo = gmaps.geocode(q_stat)
            if geo:
                l = geo[0]['geometry']['location']
                loc_center = [l['lat'], l['lng']]
            else: st.error("כתובת לא נמצאה")

        if loc_center:
            m3 = folium.Map(location=loc_center, zoom_start=16)
            plugins.LocateControl(auto_start=(True if use_gps else False)).add_to(m3)
            folium.TileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}', attr='Traffic', overlay=True).add_to(m3)
            
            if q_stat and not use_gps:
                folium.Marker(loc_center, icon=folium.Icon(color='red', icon='star')).add_to(m3)
                try:
                    nearby = gmaps.places_nearby(location=(loc_center[0], loc_center[1]), radius=500, type='transit_station')
                    for p in nearby.get('results', []):
                        pl = p['geometry']['location']
                        folium.Marker([pl['lat'], pl['lng']], tooltip=p['name'], icon=folium.Icon(color='blue', icon='bus', prefix='fa')).add_to(m3)
                except: pass

            components.html(m3._repr_html_(), height=500)

# ==========================================
# 4. ארנק
# ==========================================
with tab_wallet:
    st.markdown("""<div class="wallet-card"><div>יתרה</div><div style="font-size:32px; font-weight:bold;">₪ 45.90</div></div>""", unsafe_allow_html=True)
    if st.button("📷 שלם", type="primary", use_container_width=True):
        with st.spinner("..."): time.sleep(1)
        st.balloons()
        st.success("שולם!")
