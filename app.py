import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
import requests
import zipfile
import io
import sqlite3
import os
import googlemaps
from datetime import datetime
import pytz
from folium import plugins

# --- הגדרות ---
st.set_page_config(page_title="SmartBus All-in-One", page_icon="🚍", layout="wide")
DB_FILE = 'gtfs_israel.db'

# הגדרת אזור זמן
ISRAEL_TZ = pytz.timezone('Asia/Jerusalem')

# --- עיצוב CSS מותאם ---
st.markdown("""
    <style>
    .big-font { font-size: 20px !important; font-weight: bold; }
    .time-box { 
        background-color: #f0f2f6; 
        padding: 10px; 
        border-radius: 8px; 
        text-align: center; 
        border: 1px solid #ccc;
    }
    div[data-testid="stForm"] { border: 1px solid #ddd; padding: 20px; border-radius: 10px; }
    </style>
""", unsafe_allow_html=True)

# --- התחברות לגוגל ---
try:
    api_key = st.secrets["GOOGLE_API_KEY"]
    gmaps = googlemaps.Client(key=api_key)
except:
    st.error("⚠️ חסר מפתח API.")
    st.stop()

# --- מנוע ה-SQL (כמו בגרסה הקודמת) ---
@st.cache_resource(show_spinner=False)
def init_database():
    if os.path.exists(DB_FILE): return True
    try:
        url = "https://gtfs.mot.gov.il/gtfsfiles/israel-public-transportation.zip"
        with st.spinner('📥 מוריד מאגר נתונים (חד פעמי)...'):
            r = requests.get(url)
            z = zipfile.ZipFile(io.BytesIO(r.content))
            conn = sqlite3.connect(DB_FILE)
            
            # טעינת קווים
            pd.read_csv(z.open('routes.txt'), usecols=['route_id', 'route_short_name', 'route_long_name']).to_sql('routes', conn, if_exists='replace', index=False)
            
            # טעינת נסיעות
            trips = pd.read_csv(z.open('trips.txt'), usecols=['route_id', 'shape_id'])
            trips.drop_duplicates(subset=['route_id']).to_sql('trips', conn, if_exists='replace', index=False)
            
            # טעינת צורות (בבלוקים)
            for chunk in pd.read_csv(z.open('shapes.txt'), chunksize=100000):
                chunk.to_sql('shapes', conn, if_exists='append', index=False)
            
            # אינדקסים
            conn.execute("CREATE INDEX idx_route_name ON routes(route_short_name)")
            conn.execute("CREATE INDEX idx_shape_id ON shapes(shape_id)")
            conn.close()
        return True
    except Exception as e:
        st.error(f"שגיאה: {e}")
        return False

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
        return list(zip(df['shape_pt_lat'], df['shape_pt_lon']))
    except: return []
    finally: conn.close()

# --- האפליקציה ---
st.title("🚍 SmartBus Ultimate")

# לשוניות הניווט
tab_route, tab_line, tab_station, tab_env = st.tabs([
    "🗺️ תכנון מסלול (A ל-B)", 
    "🔢 איתור קו (SQL)", 
    "🚏 חיפוש תחנה", 
    "📍 סורק סביבה"
])

# ==========================================
# 1. תכנון מסלול (A ל-B)
# ==========================================
with tab_route:
    st.caption("ניווט בזמן אמת עם צפי פקקים וזמני הגעה")
    with st.form("nav_form"):
        c1, c2 = st.columns(2)
        with c1: origin = st.text_input("מוצא", "המיקום שלי")
        with c2: dest = st.text_input("יעד", "עזריאלי תל אביב")
        
        # בחירת זמן
        time_type = st.selectbox("מתי?", ["עכשיו", "עתידי"])
        req_time = datetime.now()
        if time_type == "עתידי":
            d = st.date_input("תאריך")
            t = st.time_input("שעה")
            req_time = datetime.combine(d, t)

        submit_nav = st.form_submit_button("חפש מסלול 🚀")

    if submit_nav:
        try:
            actual_origin = "תחנה מרכזית נתניה" if origin == "המיקום שלי" else origin
            directions = gmaps.directions(
                actual_origin, dest, 
                mode="transit", transit_mode="bus", 
                departure_time=req_time, language='he'
            )
            
            if directions:
                leg = directions[0]['legs'][0]
                
                # --- תצוגת זמנים משודרגת ---
                t1, t2, t3 = st.columns(3)
                t1.markdown(f"<div class='time-box'>⏱️ משך נסיעה<br><b>{leg['duration']['text']}</b></div>", unsafe_allow_html=True)
                t2.markdown(f"<div class='time-box'>🛫 יציאה<br><b>{leg['departure_time']['text']}</b></div>", unsafe_allow_html=True)
                t3.markdown(f"<div class='time-box'>🏁 הגעה משוערת<br><b>{leg['arrival_time']['text']}</b></div>", unsafe_allow_html=True)
                
                # מפה
                start = leg['start_location']
                m = folium.Map(location=[start['lat'], start['lng']], zoom_start=13)
                
                # שכבת פקקים (תמיד למטה)
                folium.TileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}', attr='Google Traffic', name='Traffic', overlay=True).add_to(m)
                
                # ציור מסלול (בצבע כחול-סגול כדי לא לבלבל עם פקקים)
                # אנחנו משתמשים ב-Weight 6 כדי שיהיה בולט
                import polyline
                pts = polyline.decode(directions[0]['overview_polyline']['points'])
                folium.PolyLine(pts, color="#581845", weight=6, opacity=0.8, tooltip="מסלול נסיעה").add_to(m)
                
                # מרקרים
                folium.Marker([start['lat'], start['lng']], icon=folium.Icon(color='green', icon='play')).add_to(m)
                folium.Marker([leg['end_location']['lat'], leg['end_location']['lng']], icon=folium.Icon(color='red', icon='stop')).add_to(m)
                
                st_folium(m, height=450, width="100%")
                
                with st.expander("פירוט מלא"):
                    for step in leg['steps']: st.write(step['html_instructions'], unsafe_allow_html=True)
            else:
                st.error("לא נמצא מסלול")
        except Exception as e:
            st.error(f"שגיאה: {e}")

# ==========================================
# 2. איתור קו (SQL)
# ==========================================
with tab_line:
    st.caption("חיפוש מסלול קו מלא מתוך המאגר הממשלתי")
    if init_database():
        line_num = st.text_input("הכנס מספר קו:", "")
        
        if line_num:
            res = get_routes_sql(line_num)
            if not res.empty:
                opts = {f"{r['route_long_name']}": r['route_id'] for i, r in res.iterrows()}
                sel = st.selectbox("בחר כיוון:", list(opts.keys()))
                
                if st.button("הצג קו 🗺️"):
                    pts = get_shape_sql(opts[sel])
                    if pts:
                        mid = pts[len(pts)//2]
                        m2 = folium.Map(location=mid, zoom_start=12)
                        
                        # שכבת פקקים
                        folium.TileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}', attr='Traffic', overlay=True).add_to(m2)
                        
                        # הקו בצבע סגול (Purple) כדי לבלוט על הפקקים
                        folium.PolyLine(pts, color="purple", weight=5, opacity=0.8, tooltip=f"קו {line_num}").add_to(m2)
                        
                        st_folium(m2, height=500, width="100%")
                    else: st.warning("אין מידע מפה לקו זה")
            else: st.warning("קו לא נמצא")

# ==========================================
# 3. חיפוש תחנה
# ==========================================
with tab_station:
    st.caption("איתור תחנה ספציפית וסביבתה")
    station_q = st.text_input("שם תחנה או מק\"ט:", "תחנה מרכזית ירושלים")
    if st.button("חפש תחנה 🔎"):
        res = gmaps.places(query=station_q)
        if res['status'] == 'OK':
            loc = res['results'][0]['geometry']['location']
            name = res['results'][0]['name']
            
            st.success(f"נמצאה: {name}")
            m3 = folium.Map(location=[loc['lat'], loc['lng']], zoom_start=16)
            folium.TileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}', attr='Traffic', overlay=True).add_to(m3)
            
            folium.Marker([loc['lat'], loc['lng']], icon=folium.Icon(color='blue', icon='bus', prefix='fa'), popup=name).add_to(m3)
            st_folium(m3, height=400, width="100%")
        else: st.error("תחנה לא נמצאה")

# ==========================================
# 4. סורק סביבה
# ==========================================
with tab_env:
    st.caption("מה קורה סביבי?")
    if st.button("טען מפה חיה 📡"):
        m4 = folium.Map(location=[32.08, 34.78], zoom_start=12)
        plugins.LocateControl(auto_start=True).add_to(m4)
        folium.TileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}', attr='Traffic', overlay=True).add_to(m4)
        
        # טעינת תחנות באזור המרכז כדוגמה (בטלפון זה יתמקד עליך)
        st_folium(m4, height=500, width="100%")
