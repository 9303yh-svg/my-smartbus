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
st.set_page_config(page_title="SmartBus Final", page_icon="🚍", layout="wide")
DB_FILE = 'gtfs_israel.db'
ISRAEL_TZ = pytz.timezone('Asia/Jerusalem')

# --- התחברות לגוגל ---
try:
    api_key = st.secrets["GOOGLE_API_KEY"]
    gmaps = googlemaps.Client(key=api_key)
except:
    st.error("⚠️ מפתח Google API חסר.")
    st.stop()

# --- פונקציית עזר לפרמוט זמנים בעברית (תיקון הבעיה הלוגית) ---
def format_duration_hebrew(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    
    parts = []
    if hours > 0:
        parts.append(f"{hours} שעות")
    if minutes > 0:
        parts.append(f"{minutes} דקות")
    
    if not parts:
        return "דקה אחת"
        
    return " ו-".join(parts)

# --- מנוע ה-SQL ---
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
        # דילול אגרסיבי למניעת קריסה
        return list(zip(df['shape_pt_lat'].values[::20], df['shape_pt_lon'].values[::20]))
    except: return []
    finally: conn.close()

# --- עיצוב CSS (תוקן: אין RTL גלובלי) ---
st.markdown("""
    <style>
    /* כרטיס מידע */
    .info-box { 
        background-color: #f8f9fa; 
        border-right: 4px solid #007bff; 
        padding: 10px; 
        margin-bottom: 5px; 
        border-radius: 4px; 
        text-align: right; 
        direction: rtl; 
    }
    
    /* ארנק */
    .wallet-card { 
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
        color: white; 
        padding: 20px; 
        border-radius: 15px; 
        text-align: center; 
    }
    
    /* יישור כפתורי רדיו לימין */
    div[role="radiogroup"] { direction: rtl; text-align: right; }
    </style>
""", unsafe_allow_html=True)

# --- אפליקציה ---
st.title("🚍 SmartBus Final Fix")

# טאבים
tab_nav, tab_lines, tab_stations, tab_wallet = st.tabs(["🗺️ תכנון", "🔢 קווים", "📍 סביבה", "💳 ארנק"])

# ==========================================
# 1. תכנון מסלול
# ==========================================
with tab_nav:
    with st.form("nav_form"):
        c1, c2 = st.columns(2)
        with c1: origin = st.text_input("מוצא", "המיקום שלי")
        with c2: dest = st.text_input("יעד", "עזריאלי תל אביב")
        
        # תצוגת זמן תקינה
        t_col1, t_col2 = st.columns(2)
        with t_col1: 
            time_mode = st.selectbox("זמן", ["יציאה עכשיו", "יציאה ב...", "הגעה ב..."])
        
        req_time = datetime.now()
        is_arrival = False
        if time_mode != "יציאה עכשיו":
            with t_col2: 
                t_input = st.time_input("שעה")
                req_time = datetime.combine(datetime.now().date(), t_input)
                if "הגעה" in time_mode: is_arrival = True

        submit_nav = st.form_submit_button("חפש מסלול 🚀")

    if submit_nav:
        with st.spinner('מחשב מסלול...'):
            try:
                real_orig = "תחנה מרכזית תל אביב" if origin == "המיקום שלי" else origin
                params = {
                    "origin": real_orig, "destination": dest,
                    "mode": "transit", "transit_mode": "bus",
                    "alternatives": True, "language": "he"
                }
                if is_arrival: params["arrival_time"] = req_time
                else: params["departure_time"] = req_time
                
                res = gmaps.directions(**params)
                
                if res:
                    st.success(f"נמצאו {len(res)} אפשרויות:")
                    opts = []
                    for i, r in enumerate(res):
                        leg = r['legs'][0]
                        # חישוב זמן ידני ותקין
                        duration_sec = leg['duration']['value']
                        dur_text = format_duration_hebrew(duration_sec)
                        
                        # תקציר מסלול
                        steps_icons = []
                        for s in leg['steps']:
                            if s['travel_mode'] == 'TRANSIT':
                                steps_icons.append(f"🚌 {s['transit_details']['line']['short_name']}")
                            elif s['travel_mode'] == 'WALKING':
                                steps_icons.append("🚶")
                        
                        # ניקוי כפילויות
                        clean_steps = []
                        for x in steps_icons:
                            if not clean_steps or clean_steps[-1] != x: clean_steps.append(x)
                        summary = " ➔ ".join(clean_steps)
                        
                        label = f"#{i+1}: {dur_text} | {summary}"
                        opts.append({"label": label, "data": r})
                    
                    sel = st.radio("בחר מסלול:", opts, format_func=lambda x: x['label'])
                    
                    if sel:
                        r = sel['data']
                        leg = r['legs'][0]
                        
                        # כפתור לגוגל מפות (יציב)
                        url = f"https://www.google.com/maps/dir/?api=1&origin={real_orig}&destination={dest}&travelmode=transit"
                        st.markdown(f'<a href="{url}" target="_blank"><button style="background:#4285F4;color:white;width:100%;padding:10px;border-radius:8px;border:none;">🔊 נווט קולית (Google Maps)</button></a>', unsafe_allow_html=True)
                        
                        # מפה סטטית יציבה
                        m = folium.Map(location=[leg['start_location']['lat'], leg['start_location']['lng']], zoom_start=13)
                        folium.TileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}', attr='Traffic', overlay=True).add_to(m)
                        
                        all_pts = []
                        for step in leg['steps']:
                            pts = polyline.decode(step['polyline']['points'])
                            # דילול קל
                            pts = pts[::2]
                            all_pts.extend(pts)
                            color = "#800080" if step['travel_mode'] == 'TRANSIT' else "blue"
                            folium.PolyLine(pts, color=color, weight=5, opacity=0.7).add_to(m)
                        
                        if all_pts: m.fit_bounds(all_pts)
                        components.html(m._repr_html_(), height=400)
                        
                        # הוראות טקסט
                        with st.expander("הוראות מפורטות"):
                            for step in leg['steps']:
                                st.markdown(f"<div class='info-box'>{step['html_instructions']}</div>", unsafe_allow_html=True)

                else: st.error("לא נמצא מסלול.")
            except Exception as e: st.error(f"שגיאה: {e}")

# ==========================================
# 2. איתור קו (השורת חיפוש חזרה!)
# ==========================================
with tab_lines:
    if init_database():
        st.info("🔎 חפש מסלול של קו ספציפי")
        ln = st.text_input("מספר קו (למשל 480):", "")
        
        if ln:
            res = get_routes_sql(ln)
            if not res.empty:
                opts = {f"{r['route_long_name']}": r['route_id'] for i, r in res.iterrows()}
                s = st.selectbox("בחר כיוון:", list(opts.keys()))
                
                if st.button("הצג מסלול"):
                    pts = get_shape_sql(opts[s])
                    if pts:
                        mid = pts[len(pts)//2]
                        m2 = folium.Map(location=mid, zoom_start=12)
                        folium.TileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}', attr='Traffic', overlay=True).add_to(m2)
                        folium.PolyLine(pts, color="purple", weight=5).add_to(m2)
                        components.html(m2._repr_html_(), height=400)
            else: st.warning("לא נמצא קו כזה")

# ==========================================
# 3. תחנות סביבי (התיקון: לחיצה לפרטים)
# ==========================================
with tab_stations:
    st.caption("חפש כתובת כדי לראות תחנות ברדיוס 300 מטר")
    
    col_s, col_btn = st.columns([3, 1])
    with col_s: q_loc = st.text_input("כתובת לחיפוש:", "דיזנגוף סנטר תל אביב")
    with col_btn: 
        st.write("")
        st.write("")
        do_search = st.button("חפש 🔎")
    
    if do_search:
        g = gmaps.geocode(q_loc)
        if g:
            loc = g[0]['geometry']['location']
            center = [loc['lat'], loc['lng']]
            
            # מפה
            m3 = folium.Map(location=center, zoom_start=17)
            plugins.LocateControl(auto_start=True).add_to(m3)
            folium.TileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}', attr='Traffic', overlay=True).add_to(m3)
            
            # סימון המיקום
            folium.Marker(center, popup="המיקום שנבחר", icon=folium.Icon(color='red', icon='star')).add_to(m3)
            folium.Circle(center, radius=300, color='blue', fill=True, fill_opacity=0.1).add_to(m3)
            
            # מציאת תחנות
            try:
                nearby = gmaps.places_nearby(location=(center[0], center[1]), radius=300, type='transit_station')
                count = 0
                for p in nearby.get('results', []):
                    count += 1
                    pl = p['geometry']['location']
                    name = p['name']
                    vicinity = p.get('vicinity', '')
                    
                    # הנה התיקון: פופ-אפ שמכיל את כל המידע!
                    html_popup = f"""
                    <div style="font-family:Arial; direction:rtl; text-align:right;">
                        <b>🚏 {name}</b><br>
                        <span style="font-size:12px;">{vicinity}</span>
                    </div>
                    """
                    folium.Marker(
                        [pl['lat'], pl['lng']],
                        popup=folium.Popup(html_popup, max_width=200),
                        tooltip=name,
                        icon=folium.Icon(color='blue', icon='bus', prefix='fa')
                    ).add_to(m3)
                
                st.success(f"נמצאו {count} תחנות ברדיוס 300 מטר.")
            except: pass
            
            components.html(m3._repr_html_(), height=500)
        else:
            st.error("לא נמצאה כתובת")

# ==========================================
# 4. ארנק
# ==========================================
with tab_wallet:
    st.markdown("""<div class="wallet-card"><h1>₪ 45.90</h1><p>יתרה</p></div>""", unsafe_allow_html=True)
    if st.button("📷 תשלום", use_container_width=True):
        st.balloons()
