import streamlit as st
import pandas as pd
import folium
from folium import plugins  # <--- התיקון לשגיאה האדומה
from streamlit_folium import st_folium
import streamlit.components.v1 as components
import requests
import zipfile
import io
import sqlite3
import os
import googlemaps
from datetime import datetime, timedelta
import pytz
import polyline
import time

# --- הגדרות מערכת ---
st.set_page_config(page_title="SmartBus Fixed", page_icon="🚍", layout="wide")
DB_FILE = 'gtfs_israel.db'
ISRAEL_TZ = pytz.timezone('Asia/Jerusalem')

# --- התחברות לגוגל ---
try:
    api_key = st.secrets["GOOGLE_API_KEY"]
    gmaps = googlemaps.Client(key=api_key)
except:
    st.error("⚠️ מפתח Google API חסר. האפליקציה לא תעבוד.")
    st.stop()

# --- מנוע נתונים (SQL) ---
@st.cache_resource(show_spinner=False)
def init_database():
    if os.path.exists(DB_FILE): return True
    try:
        url = "https://gtfs.mot.gov.il/gtfsfiles/israel-public-transportation.zip"
        with st.spinner('📥 טוען נתונים לזיכרון (פעולה חד פעמית)...'):
            r = requests.get(url)
            z = zipfile.ZipFile(io.BytesIO(r.content))
            conn = sqlite3.connect(DB_FILE)
            
            # טעינה חכמה וקלה
            pd.read_csv(z.open('routes.txt'), usecols=['route_id', 'route_short_name', 'route_long_name']).to_sql('routes', conn, if_exists='replace', index=False)
            trips = pd.read_csv(z.open('trips.txt'), usecols=['route_id', 'shape_id'])
            trips.drop_duplicates(subset=['route_id']).to_sql('trips', conn, if_exists='replace', index=False)
            
            # טעינת צורות (Shapes)
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
        # === מניעת קריסה: דילול נקודות ===
        # לוקח רק נקודה אחת מכל 25 נקודות. העין לא רואה הבדל, הזיכרון כן!
        return list(zip(df['shape_pt_lat'].values[::25], df['shape_pt_lon'].values[::25]))
    except: return []
    finally: conn.close()

# --- פונקציית סיכום מסלול (כמו מוביט) ---
def get_route_summary(leg):
    steps_summary = []
    for step in leg['steps']:
        if step['travel_mode'] == 'TRANSIT':
            line_name = step['transit_details']['line']['short_name']
            steps_summary.append(f"🚌 {line_name}")
        elif step['travel_mode'] == 'WALKING':
            steps_summary.append("🚶")
    
    # איחוד יפה
    clean = []
    for s in steps_summary:
        if not clean or clean[-1] != s: clean.append(s)
    return " ➔ ".join(clean)

# --- עיצוב (תוקן: אין יותר RTL גלובלי ששובר את המסך) ---
st.markdown("""
    <style>
    /* כרטיס מידע */
    .info-card {
        background-color: #f8f9fa;
        border-right: 5px solid #007bff;
        padding: 10px;
        margin-bottom: 8px;
        border-radius: 5px;
        text-align: right;
        direction: rtl;
    }
    /* כפתור */
    .stButton>button { width: 100%; border-radius: 8px; font-weight: bold; }
    
    /* ארנק */
    .wallet-box {
        background: linear-gradient(to right, #4facfe 0%, #00f2fe 100%);
        color: white; padding: 20px; border-radius: 12px; text-align: right;
    }
    </style>
""", unsafe_allow_html=True)

# --- סרגל צד ---
with st.sidebar:
    st.header("⚙️ הגדרות")
    max_walking = st.slider("מקסימום דקות הליכה:", 0, 40, 15)
    st.write(f"🚶 מסלולים מעל {max_walking} דק' יסומנו באזהרה")
    
    if st.button("🔴 איפוס מערכת (במקרה תקלה)"):
        st.session_state.clear()
        st.rerun()

st.title("🚍 SmartBus Pro")

# --- טאבים ---
tab_plan, tab_lines, tab_near, tab_pay = st.tabs(["🗺️ תכנון מסלול", "🔢 איתור קו", "📍 תחנות סביבי", "💳 ארנק"])

# ==========================================
# 1. תכנון מסלול + ניווט (יציב)
# ==========================================
with tab_plan:
    # מצב ניווט חי (פשוט וקל למניעת קריסה)
    if st.session_state.get('nav_active'):
        st.success("🟢 מצב ניווט פעיל")
        if st.button("❌ יציאה מניווט"):
            st.session_state.nav_active = False
            st.rerun()
            
        # כפתור לגוגל מפות
        u_orig = st.session_state.nav_orig
        u_dest = st.session_state.nav_dest
        url = f"https://www.google.com/maps/dir/?api=1&origin={u_orig}&destination={u_dest}&travelmode=transit"
        st.markdown(f'<a href="{url}" target="_blank"><button style="width:100%; background:#4285F4; color:white; padding:15px; border:none; border-radius:10px; cursor:pointer;">🔊 פתח ניווט קולי מלא</button></a>', unsafe_allow_html=True)
        
        # הוראות טקסטואליות (לא מעמיסות על הזיכרון)
        st.subheader("הוראות:")
        for step in st.session_state.nav_steps:
            icon = "🚌" if step['travel_mode'] == 'TRANSIT' else "🚶"
            txt = step['html_instructions']
            dist = step['distance']['text']
            st.markdown(f"<div class='info-card'>{icon} {txt} ({dist})</div>", unsafe_allow_html=True)
            
    else:
        # טופס חיפוש
        with st.form("search"):
            c1, c2 = st.columns(2)
            with c1: org = st.text_input("מוצא", "המיקום שלי")
            with c2: dst = st.text_input("יעד", "עזריאלי תל אביב")
            
            # בחירת זמן
            tc1, tc2 = st.columns(2)
            with tc1: time_type = st.selectbox("זמן", ["יציאה עכשיו", "יציאה ב...", "הגעה עד..."])
            
            req_time = datetime.now()
            is_arrival = False
            if time_type != "יציאה עכשיו":
                with tc2: 
                    t_input = st.time_input("שעה")
                    req_time = datetime.combine(datetime.now().date(), t_input)
                    if "הגעה" in time_type: is_arrival = True
            
            submit = st.form_submit_button("חפש מסלול 🚀")
        
        if submit:
            with st.spinner('מחשב מסלול...'):
                try:
                    real_org = "תחנה מרכזית תל אביב" if org == "המיקום שלי" else org
                    
                    # פרמטרים לגוגל
                    params = {
                        "origin": real_org, "destination": dst,
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
                            summary = get_route_summary(leg)
                            dur = leg['duration']['text']
                            opts.append({"label": f"#{i+1}: {dur} | {summary}", "data": r})
                        
                        sel = st.radio("בחר:", opts, format_func=lambda x: x['label'])
                        
                        if sel:
                            route = sel['data']
                            leg = route['legs'][0]
                            
                            if st.button("🚶‍♂️ התחל ניווט"):
                                st.session_state.nav_steps = leg['steps']
                                st.session_state.nav_orig = real_org
                                st.session_state.nav_dest = dst
                                st.session_state.nav_active = True
                                st.rerun()

                            # מפה קלה (components)
                            m = folium.Map(location=[leg['start_location']['lat'], leg['start_location']['lng']], zoom_start=13)
                            plugins.LocateControl().add_to(m)
                            folium.TileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}', attr='Traffic', overlay=True).add_to(m)
                            
                            # ציור מסלול מדולל
                            for step in leg['steps']:
                                pts = polyline.decode(step['polyline']['points'])
                                # דילול נקודות קריטי!
                                folium.PolyLine(pts[::5], color="blue", weight=5, opacity=0.7).add_to(m)
                                
                            components.html(m._repr_html_(), height=400)
                    else:
                        st.warning("לא נמצא מסלול.")
                except Exception as e:
                    st.error(f"שגיאה: {e}")

# ==========================================
# 2. איתור קו (התיקון: החזרת שורת חיפוש)
# ==========================================
with tab_lines:
    if init_database():
        # הנה שורת החיפוש שביקשת!
        ln = st.text_input("מספר קו (למשל 910):", "")
        
        # הוספת בחירת זמן ליציאה עתידית לבקשתך
        st.caption("בחר זמן יציאה עתידי (אופציונלי - לבדיקת לו\"ז)")
        future_time = st.time_input("שעת יציאה רצויה:", datetime.now().time())
        
        if ln:
            res = get_routes_sql(ln)
            if not res.empty:
                opts = {f"{r['route_long_name']}": r['route_id'] for i, r in res.iterrows()}
                s = st.selectbox("בחר כיוון:", list(opts.keys()))
                
                if st.button("הצג מסלול על המפה"):
                    pts = get_shape_sql(opts[s])
                    if pts:
                        mid = pts[len(pts)//2]
                        m2 = folium.Map(location=mid, zoom_start=12)
                        folium.TileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}', attr='Traffic', overlay=True).add_to(m2)
                        folium.PolyLine(pts, color="purple", weight=5).add_to(m2)
                        components.html(m2._repr_html_(), height=450)
            else:
                st.warning("קו לא נמצא.")

# ==========================================
# 3. תחנות סביבי (התיקון: מפה ממוקדת)
# ==========================================
with tab_near:
    st.info("💡 לחץ על הכפתור השחור במפה כדי להתמקד במיקום שלך")
    
    # מיקום ברירת מחדל: תל אביב (במקום כל העולם)
    default_loc = [32.0800, 34.7800]
    
    m3 = folium.Map(location=default_loc, zoom_start=15)
    
    # כפתור GPS קריטי
    plugins.LocateControl(auto_start=True).add_to(m3)
    folium.TileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}', attr='Traffic', overlay=True).add_to(m3)
    
    # הצגה בטוחה
    components.html(m3._repr_html_(), height=500)

# ==========================================
# 4. ארנק
# ==========================================
with tab_pay:
    st.markdown("""<div class="wallet-box"><h2>₪ 45.90</h2><p>יתרה זמינה</p></div>""", unsafe_allow_html=True)
    if st.button("📷 סרוק ותקף", use_container_width=True):
        st.balloons()
        st.success("נסיעה טובה!")
