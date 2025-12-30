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

# --- הגדרות ---
st.set_page_config(page_title="SmartBus Ultimate", page_icon="🚍", layout="wide")
DB_FILE = 'gtfs_israel.db'

# --- CSS מותאם לנייד ---
st.markdown("""
    <style>
    /* כרטיסי ניווט גדולים */
    .nav-card {
        background-color: #ffffff;
        border: 2px solid #007bff;
        border-radius: 10px;
        padding: 20px;
        text-align: center;
        font-size: 22px;
        font-weight: bold;
        margin-bottom: 20px;
        box-shadow: 0 4px 8px rgba(0,0,0,0.1);
    }
    .big-icon { font-size: 40px; display: block; margin-bottom: 10px; }
    
    /* פופ-אפ לתחנה */
    .station-popup { direction: rtl; text-align: right; font-family: sans-serif; }
    .line-badge { 
        background-color: #eee; border: 1px solid #ccc; 
        padding: 2px 6px; border-radius: 4px; font-size: 12px; margin: 2px; display: inline-block;
    }
    
    /* כפתורים */
    .stButton>button { width: 100%; height: 50px; font-size: 18px; border-radius: 12px; }
    </style>
""", unsafe_allow_html=True)

# --- חיבור לגוגל ---
try:
    api_key = st.secrets["GOOGLE_API_KEY"]
    gmaps = googlemaps.Client(key=api_key)
except:
    st.error("⚠️ מפתח API חסר.")
    st.stop()

# --- SQL מהיר ---
@st.cache_resource(show_spinner=False)
def init_db():
    if os.path.exists(DB_FILE): return True
    try:
        url = "https://gtfs.mot.gov.il/gtfsfiles/israel-public-transportation.zip"
        with st.spinner('📥 מוריד נתונים...'):
            r = requests.get(url)
            z = zipfile.ZipFile(io.BytesIO(r.content))
            conn = sqlite3.connect(DB_FILE)
            pd.read_csv(z.open('routes.txt'), usecols=['route_id','route_short_name','route_long_name']).to_sql('routes', conn, if_exists='replace', index=False)
            trips = pd.read_csv(z.open('trips.txt'), usecols=['route_id','shape_id']).drop_duplicates('route_id')
            trips.to_sql('trips', conn, if_exists='replace', index=False)
            # טוען רק חלק מהצורות כדי לחסוך מקום
            pd.read_csv(z.open('shapes.txt')).iloc[::10].to_sql('shapes', conn, if_exists='replace', index=False)
            conn.close()
        return True
    except: return False

def get_route_shape(line_num):
    conn = sqlite3.connect(DB_FILE)
    # תיקון חיפוש: הסרת רווחים והתאמה מדויקת
    q = f"SELECT * FROM routes WHERE route_short_name = '{line_num.strip()}'"
    routes = pd.read_sql_query(q, conn)
    
    if routes.empty:
        conn.close()
        return None, None
        
    # לוקחים את הראשון
    route_id = routes.iloc[0]['route_id']
    route_desc = routes.iloc[0]['route_long_name']
    
    # שליפת הצורה
    q_shape = f"""
    SELECT s.shape_pt_lat, s.shape_pt_lon 
    FROM trips t 
    JOIN shapes s ON t.shape_id = s.shape_id 
    WHERE t.route_id = '{route_id}' 
    ORDER BY s.shape_pt_sequence
    """
    df = pd.read_sql_query(q_shape, conn)
    conn.close()
    
    # דילול נוסף למניעת קריסה בטלפון
    points = list(zip(df['shape_pt_lat'].values[::5], df['shape_pt_lon'].values[::5]))
    return points, route_desc

# --- ניהול Session לניווט ---
if 'nav_step' not in st.session_state: st.session_state.nav_step = 0
if 'nav_data' not in st.session_state: st.session_state.nav_data = None

# --- ממשק ראשי ---
st.title("🚍 SmartBus Final")
tab1, tab2, tab3 = st.tabs(["🗺️ ניווט חי", "🔢 קווים", "📍 סביבה"])

# ==================================================
# 1. ניווט חי (Waze פנימי - ללא מפה כבדה)
# ==================================================
with tab1:
    # אם אין מסלול פעיל - טופס חיפוש
    if not st.session_state.nav_data:
        with st.form("search"):
            c1, c2 = st.columns(2)
            with c1: org = st.text_input("מוצא", "המיקום שלי")
            with c2: dst = st.text_input("יעד", "עזריאלי תל אביב")
            if st.form_submit_button("חפש מסלול 🚀"):
                with st.spinner('מחשב...'):
                    real_org = "תחנה מרכזית תל אביב" if org == "המיקום שלי" else org
                    res = gmaps.directions(real_org, dst, mode="transit", transit_mode="bus", language='he')
                    if res:
                        st.session_state.nav_data = res[0]['legs'][0]['steps']
                        st.session_state.nav_step = 0
                        st.rerun()
                    else:
                        st.error("לא נמצא מסלול")
    
    # אם יש מסלול - הצגת "מצב נהיגה/הליכה"
    else:
        steps = st.session_state.nav_data
        idx = st.session_state.nav_step
        current = steps[idx]
        
        # כפתורי שליטה
        col_prev, col_x, col_next = st.columns([1, 1, 2])
        with col_prev:
            if st.button("⬅️ הקודם") and idx > 0:
                st.session_state.nav_step -= 1
                st.rerun()
        with col_x:
            if st.button("❌ צא"):
                st.session_state.nav_data = None
                st.rerun()
        with col_next:
            if idx < len(steps) - 1:
                if st.button("הבא ➡️", type="primary"):
                    st.session_state.nav_step += 1
                    st.rerun()
            else:
                st.success("הגעת ליעד! 🏁")

        # כרטיס ההוראה הנוכחית
        icon = "🚶" if current['travel_mode'] == 'WALKING' else "🚌"
        instr = current['html_instructions']
        dist = current['distance']['text']
        
        st.markdown(f"""
        <div class="nav-card">
            <span class="big-icon">{icon}</span>
            <div>{instr}</div>
            <div style="color:gray; font-size:16px; margin-top:10px;">עוד {dist}</div>
        </div>
        """, unsafe_allow_html=True)
        
        # פרטים נוספים אם זה אוטובוס
        if current['travel_mode'] == 'TRANSIT':
            dt = current['transit_details']
            st.info(f"🚌 קו {dt['line']['short_name']} לכיוון {dt['headsign']}")
            st.write(f"תחנות: {dt['num_stops']}")

# ==================================================
# 2. חיפוש קו (מתוקן)
# ==================================================
with tab2:
    if init_db():
        ln = st.text_input("הכנס מספר קו (למשל 1, 480):", "")
        if ln and st.button("הצג מסלול"):
            pts, desc = get_route_shape(ln)
            if pts:
                st.success(f"נמצא: {desc}")
                m = folium.Map(location=pts[len(pts)//2], zoom_start=12)
                folium.PolyLine(pts, color="purple", weight=5).add_to(m)
                components.html(m._repr_html_(), height=400)
            else:
                st.warning("הקו לא נמצא (נסה מספר אחר)")

# ==================================================
# 3. תחנות סביבי (זום 17 + קווים עוברים)
# ==================================================
with tab3:
    col_in, col_btn = st.columns([3,1])
    with col_in: addr = st.text_input("כתובת:", "דיזנגוף סנטר")
    with col_btn: 
        st.write("")
        st.write("")
        do_map = st.button("חפש")
    
    if do_map:
        # 1. מציאת מיקום
        loc = [32.08, 34.78] # ברירת מחדל
        if addr:
            geo = gmaps.geocode(addr)
            if geo:
                l = geo[0]['geometry']['location']
                loc = [l['lat'], l['lng']]
        
        # 2. יצירת מפה בזום גבוה (רחוב)
        m = folium.Map(location=loc, zoom_start=17) # זום 17 = 300 מטר ממוקד
        plugins.LocateControl(auto_start=True).add_to(m)
        
        # 3. מציאת תחנות וקווים
        try:
            places = gmaps.places_nearby(location=(loc[0], loc[1]), radius=300, type='transit_station')
            
            for p in places.get('results', []):
                lat = p['geometry']['location']['lat']
                lng = p['geometry']['location']['lng']
                name = p['name']
                
                # ניסיון לחלץ פרטי קווים (טריק: שימוש בפרטי Place)
                # בגלל שאין לנו Realtime API, נציג את השם בצורה יפה
                
                html = f"""
                <div class='station-popup'>
                    <b>🚏 {name}</b><br>
                    <hr style='margin:5px 0;'>
                    <small>לחץ לניווט בגוגל</small>
                </div>
                """
                
                folium.Marker(
                    [lat, lng],
                    popup=folium.Popup(html, max_width=200),
                    icon=folium.Icon(color='blue', icon='bus', prefix='fa')
                ).add_to(m)
                
            components.html(m._repr_html_(), height=500)
            
        except Exception as e:
            st.error(f"שגיאה: {e}")
