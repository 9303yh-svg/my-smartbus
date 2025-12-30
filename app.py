import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
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
st.set_page_config(page_title="SmartBus Final", page_icon="🚍", layout="wide")
DB_FILE = 'gtfs_israel.db'
ISRAEL_TZ = pytz.timezone('Asia/Jerusalem')

# --- התחברות לגוגל ---
try:
    api_key = st.secrets["GOOGLE_API_KEY"]
    gmaps = googlemaps.Client(key=api_key)
except:
    st.error("⚠️ חסר מפתח API.")
    st.stop()

# --- מנוע ה-SQL ---
@st.cache_resource(show_spinner=False)
def init_database():
    if os.path.exists(DB_FILE): return True
    try:
        url = "https://gtfs.mot.gov.il/gtfsfiles/israel-public-transportation.zip"
        with st.spinner('📥 מוריד נתונים...'):
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
        return list(zip(df['shape_pt_lat'].values[::5], df['shape_pt_lon'].values[::5]))
    except: return []
    finally: conn.close()

# --- עיצוב CSS ---
st.markdown("""
    <style>
    .stApp { direction: rtl; }
    .wallet-card {
        background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%);
        color: white; padding: 20px; border-radius: 15px;
        box-shadow: 0 4px 15px rgba(0,0,0,0.2); text-align: right;
    }
    .warning-box {
        background-color: #ffcccc; color: #990000; padding: 10px;
        border-radius: 5px; margin-top: 10px; font-weight: bold; text-align: center;
    }
    </style>
""", unsafe_allow_html=True)

# --- סרגל צד להגדרות ---
with st.sidebar:
    st.header("⚙️ הגדרות משתמש")
    # הנה הסליידר שביקשת להחזיר
    max_walking = st.slider("מקסימום הליכה (דקות):", 0, 30, 10)
    st.info(f"מסלולים עם יותר מ-{max_walking} דקות הליכה יסומנו באזהרה.")

# --- האפליקציה ---
st.title("🚍 SmartBus Final")

# טאבים
tab_plan, tab_lines, tab_stations, tab_wallet = st.tabs(["🗺️ תכנון", "🔢 קווים", "🚏 תחנות", "💳 ארנק"])

# ==========================================
# 1. ניווט ותכנון (עם סינון הליכה)
# ==========================================
with tab_plan:
    with st.form("nav_form"):
        c1, c2 = st.columns(2)
        with c1: origin = st.text_input("מוצא", "המיקום שלי")
        with c2: dest = st.text_input("יעד", "עזריאלי תל אביב")
        submit_nav = st.form_submit_button("חפש מסלול 🚀")

    if submit_nav:
        with st.spinner('מחשב מסלול...'):
            try:
                real_orig = "תחנה מרכזית נתניה" if origin == "המיקום שלי" else origin
                routes = gmaps.directions(real_orig, dest, mode="transit", transit_mode="bus", language='he', alternatives=True)
                
                if routes:
                    st.success(f"נמצאו {len(routes)} אפשרויות:")
                    
                    # בחירת מסלול
                    selected_route_idx = st.radio(
                        "בחר מסלול:",
                        options=range(len(routes)),
                        format_func=lambda i: f"אפשרות {i+1}: {routes[i]['legs'][0]['duration']['text']}"
                    )
                    
                    leg = routes[selected_route_idx]['legs'][0]
                    
                    # חישוב הליכה כולל
                    total_walk_sec = sum([s['duration']['value'] for s in leg['steps'] if s['travel_mode']=='WALKING'])
                    total_walk_min = int(total_walk_sec / 60)
                    
                    # אזהרת הליכה (הפיצ'ר שחזר)
                    if total_walk_min > max_walking:
                        st.warning(f"⚠️ שים לב: מסלול זה דורש {total_walk_min} דקות הליכה (יותר מ-{max_walking} שהגדרת).")
                    else:
                        st.info(f"✅ מסלול נוח: רק {total_walk_min} דקות הליכה.")

                    # מפה
                    m = folium.Map(location=[leg['start_location']['lat'], leg['start_location']['lng']], zoom_start=14)
                    folium.TileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}', attr='Traffic', overlay=True).add_to(m)
                    
                    path_pts = []
                    for step in leg['steps']:
                        pts = polyline.decode(step['polyline']['points'])
                        path_pts.extend(pts)
                        if step['travel_mode'] == 'TRANSIT':
                            line = step['transit_details']['line']['short_name']
                            folium.PolyLine(pts, color="#581845", weight=6, opacity=0.8, tooltip=f"קו {line}").add_to(m)
                        else:
                            folium.PolyLine(pts, color="blue", dash_array='5, 10', weight=4, opacity=0.6).add_to(m)
                    
                    m.fit_bounds(path_pts)
                    components.html(m._repr_html_(), height=400)
                    
                else:
                    st.error("לא נמצא מסלול.")
            except Exception as e:
                st.error(f"שגיאה: {e}")

# ==========================================
# 2. איתור קו (SQL)
# ==========================================
with tab_lines:
    if init_database():
        line = st.text_input("מספר קו:", "")
        if line:
            res = get_routes_sql(line)
            if not res.empty:
                opts = {f"{r['route_long_name']}": r['route_id'] for i, r in res.iterrows()}
                sel = st.selectbox("כיוון:", list(opts.keys()))
                if st.button("הצג"):
                    pts = get_shape_sql(opts[sel])
                    if pts:
                        mid = pts[len(pts)//2]
                        m2 = folium.Map(location=mid, zoom_start=12)
                        folium.TileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}', attr='Traffic', overlay=True).add_to(m2)
                        folium.PolyLine(pts, color="purple", weight=5).add_to(m2)
                        components.html(m2._repr_html_(), height=500)
            else: st.warning("לא נמצא")

# ==========================================
# 3. חיפוש תחנה (משופר - מרכז + סביבה)
# ==========================================
with tab_stations:
    st.info("מציג את התחנה המבוקשת ואת הסביבה שלה")
    q = st.text_input("שם תחנה:", "סבידור מרכז")
    if st.button("מצא והצג סביבה 🔎"):
        r = gmaps.places(query=q)
        if r['status'] == 'OK':
            # 1. התחנה הראשית שנמצאה
            main_loc = r['results'][0]['geometry']['location']
            name = r['results'][0]['name']
            
            # 2. יצירת מפה ממוקדת עליה
            m3 = folium.Map(location=[main_loc['lat'], main_loc['lng']], zoom_start=17)
            folium.TileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}', attr='Traffic', overlay=True).add_to(m3)
            
            # מרקר ראשי (אדום וגדול)
            folium.Marker(
                [main_loc['lat'], main_loc['lng']], 
                popup=name, 
                tooltip="התחנה שחיפשת",
                icon=folium.Icon(color='red', icon='star', prefix='fa')
            ).add_to(m3)
            
            # 3. הוספת תחנות סביבה (הפיצ'ר החדש)
            try:
                nearby = gmaps.places_nearby(location=main_loc, radius=300, type='transit_station')
                for p in nearby.get('results', []):
                    # לא להוסיף את אותה תחנה פעמיים
                    if p['place_id'] != r['results'][0]['place_id']:
                        ploc = p['geometry']['location']
                        folium.Marker(
                            [ploc['lat'], ploc['lng']],
                            tooltip=p['name'],
                            icon=folium.Icon(color='blue', icon='bus', prefix='fa') # כחול וקטן יותר
                        ).add_to(m3)
            except: pass
            
            components.html(m3._repr_html_(), height=450)
            st.success(f"נמצאה: {name} (וסימנו תחנות נוספות בכחול מסביב)")
        else:
            st.error("תחנה לא נמצאה")

# ==========================================
# 4. ארנק (עם אזהרה ברורה)
# ==========================================
with tab_wallet:
    st.markdown("""
    <div class="wallet-card">
        <div style="font-size:14px;">יתרה זמינה</div>
        <div style="font-size:32px; font-weight:bold;">₪ 45.90</div>
    </div>
    """, unsafe_allow_html=True)
    
    if st.button("📷 סרוק ותקף נסיעה", type="primary", use_container_width=True):
        with st.spinner("מתחבר..."):
            time.sleep(1.5)
        st.balloons()
        st.success("✅ האישור התקבל!")
        
        # האזהרה החשובה
        st.markdown("""
            <div class="warning-box">
            🛑 שים לב: זוהי סימולציה בלבד!<br>
            תשלום זה אינו אמיתי ולא יתקבל ע"י פקחים.<br>
            לנסיעה בפועל יש להשתמש באפליקציות המורשות.
            </div>
        """, unsafe_allow_html=True)
