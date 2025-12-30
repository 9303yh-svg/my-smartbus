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

# --- הגדרות ---
st.set_page_config(page_title="SmartBus Optimizer", page_icon="⚖️", layout="wide")
DB_FILE = 'gtfs_israel.db'

# אזור זמן
ISRAEL_TZ = pytz.timezone('Asia/Jerusalem')

# --- עיצוב ---
st.markdown("""
    <style>
    .route-card {
        background-color: #ffffff;
        padding: 15px;
        border-radius: 10px;
        border: 1px solid #e0e0e0;
        margin-bottom: 10px;
        box-shadow: 0 2px 5px rgba(0,0,0,0.05);
    }
    .highlight { color: #581845; font-weight: bold; }
    .traffic-bad { color: #d32f2f; font-weight: bold; }
    .traffic-good { color: #388e3c; font-weight: bold; }
    </style>
""", unsafe_allow_html=True)

# --- התחברות לגוגל ---
try:
    api_key = st.secrets["GOOGLE_API_KEY"]
    gmaps = googlemaps.Client(key=api_key)
except:
    st.error("⚠️ חסר מפתח API.")
    st.stop()

# --- מנוע ה-SQL (לחיפוש קווים כללי) ---
@st.cache_resource(show_spinner=False)
def init_database():
    if os.path.exists(DB_FILE): return True
    try:
        url = "https://gtfs.mot.gov.il/gtfsfiles/israel-public-transportation.zip"
        with st.spinner('📥 טוען מאגר נתונים (חד פעמי)...'):
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
        return list(zip(df['shape_pt_lat'].values[::5], df['shape_pt_lon'].values[::5])) # דילול
    except: return []
    finally: conn.close()

# --- פונקציית העל: חישוב פקקים למקטע ---
def analyze_segment_traffic(start_loc, end_loc, departure_time):
    """
    בודק כמה זמן לוקח לרכב לעשות את הקטע הזה, כדי להבין אם האוטובוס יתקע.
    מחזיר: (זמן רגיל בדקות, זמן עם פקקים בדקות)
    """
    try:
        res = gmaps.directions(
            f"{start_loc['lat']},{start_loc['lng']}",
            f"{end_loc['lat']},{end_loc['lng']}",
            mode="driving",
            departure_time=departure_time,
            traffic_model="best_guess"
        )
        if res:
            leg = res[0]['legs'][0]
            normal = leg['duration']['value']
            traffic = leg.get('duration_in_traffic', {}).get('value', normal)
            return normal / 60, traffic / 60
    except:
        pass
    return 0, 0

# --- האפליקציה ---
st.title("🚍 SmartBus Optimizer")

# הגדרות משתמש בסרגל צד
with st.sidebar:
    st.header("⚙️ הגדרות העדפה")
    max_walking_minutes = st.slider("מקסימום הליכה שאני מוכן (דקות):", 0, 30, 10)
    st.caption("אם מסלול ידרוש יותר הליכה מזה - נסנן אותו (אלא אם הוא ממש מהיר).")

# לשוניות
tab_opt, tab_line, tab_env = st.tabs(["⚖️ השוואת מסלולים חכמה", "🔢 איתור קו", "📍 סביבה"])

# ==========================================
# 1. המנוע החכם (השוואת מסלולים)
# ==========================================
with tab_opt:
    st.info("🔎 המערכת תבדוק מספר מסלולים ותחשב עבורך את זמן הפקקים בכל אחד מהם.")
    
    with st.form("smart_route"):
        c1, c2 = st.columns(2)
        with c1: origin = st.text_input("מוצא", "המיקום שלי")
        with c2: dest = st.text_input("יעד", "עזריאלי תל אביב")
        time_mode = st.selectbox("זמן יציאה", ["עכשיו", "עתידי"])
        submit_smart = st.form_submit_button("נתח מסלולים 🚀")

    if submit_smart:
        with st.spinner('🔄 שואב נתונים, מנתח חלופות ובודק עומסי תנועה...'):
            try:
                actual_origin = "תחנה מרכזית נתניה" if origin == "המיקום שלי" else origin
                req_time = datetime.now()
                
                # בקשת חלופות (alternatives=True)
                routes = gmaps.directions(
                    actual_origin, dest,
                    mode="transit", transit_mode="bus",
                    alternatives=True, # זה המפתח!
                    departure_time=req_time, language='he'
                )
                
                if routes:
                    analyzed_routes = []
                    
                    # ניתוח כל חלופה
                    for idx, route in enumerate(routes):
                        leg = route['legs'][0]
                        total_duration_sec = leg['duration']['value']
                        total_walk_sec = 0
                        traffic_delay_min = 0
                        
                        steps_data = [] # לשמירת מידע לציור המפה
                        
                        # מעבר על השלבים (הליכה/אוטובוס)
                        for step in leg['steps']:
                            if step['travel_mode'] == 'WALKING':
                                total_walk_sec += step['duration']['value']
                                steps_data.append({'type': 'walk', 'points': step['polyline']['points']})
                            
                            elif step['travel_mode'] == 'TRANSIT':
                                # בדיקת פקקים למקטע האוטובוס הזה
                                start_stop = step['transit_details']['departure_stop']['location']
                                end_stop = step['transit_details']['arrival_stop']['location']
                                dept_time = datetime.fromtimestamp(step['transit_details']['departure_time']['value'])
                                
                                # קריאה לפונקציית העזר
                                norm, traf = analyze_segment_traffic(start_stop, end_stop, dept_time)
                                delay = max(0, traf - norm)
                                traffic_delay_min += delay
                                
                                line_name = step['transit_details']['line']['short_name']
                                steps_data.append({
                                    'type': 'bus', 
                                    'points': step['polyline']['points'], 
                                    'line': line_name,
                                    'delay': delay
                                })

                        total_walk_min = int(total_walk_sec / 60)
                        
                        # שמירת התוצאה
                        analyzed_routes.append({
                            'id': idx,
                            'duration_text': leg['duration']['text'],
                            'duration_val': total_duration_sec,
                            'walk_min': total_walk_min,
                            'traffic_delay': int(traffic_delay_min),
                            'steps': steps_data,
                            'summary': route['summary'] if 'summary' in route else f"דרך מסלול {idx+1}"
                        })

                    # === תצוגת התוצאות ===
                    st.success(f"נמצאו {len(analyzed_routes)} חלופות. הנה הניתוח:")
                    
                    # בחירת מסלול
                    selection = st.radio(
                        "בחר מסלול להצגה על המפה:",
                        options=analyzed_routes,
                        format_func=lambda x: f"⏱️ {x['duration_text']} | 🚶 {x['walk_min']} דק' הליכה | 🚦 +{x['traffic_delay']} דק' פקקים"
                    )
                    
                    # בדיקה מול הגדרות המשתמש
                    if selection['walk_min'] > max_walking_minutes:
                        st.warning(f"⚠️ שים לב: מסלול זה דורש {selection['walk_min']} דקות הליכה (יותר ממה שהגדרת).")
                    elif selection['traffic_delay'] > 15:
                        st.error(f"🔥 שים לב: מסלול זה כולל זמן פקקים משמעותי ({selection['traffic_delay']} דקות).")
                    else:
                        st.info("✅ בחירה מאוזנת וטובה.")

                    # === ציור המפה למסלול הנבחר ===
                    if selection:
                        m = folium.Map(location=[32.08, 34.78], zoom_start=12) # יתמרכז לבד ע"י fit_bounds
                        folium.TileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}', attr='Traffic', overlay=True).add_to(m)
                        
                        bounds_points = []
                        
                        for step in selection['steps']:
                            pts = polyline.decode(step['points'])
                            bounds_points.extend(pts)
                            
                            if step['type'] == 'walk':
                                folium.PolyLine(pts, color="blue", weight=4, dash_array='5, 10', opacity=0.6, tooltip="הליכה").add_to(m)
                            else:
                                # צבע הקו לפי הפקק
                                color = "#581845" # סגול רגיל
                                tooltip = f"קו {step['line']}"
                                
                                if step['delay'] > 10:
                                    color = "red"
                                    tooltip += f" (עומס כבד +{int(step['delay'])} דק')"
                                elif step['delay'] > 3:
                                    color = "orange"
                                    tooltip += f" (עומס +{int(step['delay'])} דק')"
                                
                                folium.PolyLine(pts, color=color, weight=6, opacity=0.9, tooltip=tooltip).add_to(m)

                        m.fit_bounds(bounds_points)
                        components.html(m._repr_html_(), height=500)

                else:
                    st.error("לא נמצאו מסלולים.")
            except Exception as e:
                st.error(f"שגיאה: {e}")

# ==========================================
# 2. איתור קו (SQL)
# ==========================================
with tab_line:
    st.caption("חיפוש קו ממאגר המידע")
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
# 3. סביבה
# ==========================================
with tab_env:
    if st.button("מפת סביבה"):
        m3 = folium.Map(location=[32.08, 34.78], zoom_start=14)
        from folium import plugins
        plugins.LocateControl(auto_start=True).add_to(m3)
        folium.TileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}', attr='Traffic', overlay=True).add_to(m3)
        st_folium(m3, height=500)
