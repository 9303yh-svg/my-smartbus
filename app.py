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
st.set_page_config(page_title="SmartBus Pro", page_icon="🚍", layout="wide")
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

# --- עיצוב CSS מתקדם (מסדר את העברית והזמנים) ---
st.markdown("""
    <style>
    /* יישור לימין לכל האפליקציה */
    .stApp { direction: rtl; }
    
    /* כרטיס תוצאה מעוצב */
    .result-card {
        background-color: white;
        padding: 15px;
        border-radius: 10px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        margin-bottom: 10px;
        border-right: 5px solid #581845;
        direction: rtl;
        text-align: right;
    }
    
    .metric-box {
        background: #f8f9fa;
        padding: 10px;
        border-radius: 5px;
        text-align: center;
        border: 1px solid #eee;
    }
    
    /* תיקון לכיוון טקסט בכפתורי רדיו */
    div[role="radiogroup"] { direction: rtl; text-align: right; }
    p { text-align: right; }
    </style>
""", unsafe_allow_html=True)

# --- פונקציית עזר לחישוב פקקים למקטע ---
def get_traffic_delay(start, end, time):
    try:
        res = gmaps.directions(f"{start['lat']},{start['lng']}", f"{end['lat']},{end['lng']}", mode="driving", departure_time=time)
        if res:
            leg = res[0]['legs'][0]
            norm = leg['duration']['value']
            traf = leg.get('duration_in_traffic', {}).get('value', norm)
            return max(0, (traf - norm) / 60)
    except: pass
    return 0

# --- האפליקציה ---
st.title("🚍 SmartBus Pro 15.0")

# טאבים
tab_plan, tab_lines, tab_stations, tab_gps = st.tabs(["🗺️ תכנון מסלול (חכם)", "🔢 איתור קו", "🚏 חיפוש תחנה", "📍 GPS"])

# ==========================================
# 1. תכנון מסלול (המתוקן והמשודרג)
# ==========================================
with tab_plan:
    st.caption("השוואת מסלולים, זמנים ופקקים")
    
    with st.form("route_planner"):
        c1, c2 = st.columns(2)
        with c1: 
            origin = st.text_input("מוצא (כתובת או שם תחנה)", "המיקום שלי")
        with c2: 
            dest = st.text_input("יעד (כתובת או שם תחנה)", "עזריאלי תל אביב")
        
        # --- בחירת זמן מתקדמת ---
        st.write("---")
        t_col1, t_col2, t_col3 = st.columns(3)
        with t_col1:
            time_mode = st.selectbox("סוג זמן", ["יציאה עכשיו", "יציאה בשעה...", "הגעה עד שעה..."])
        
        req_time = datetime.now()
        is_arrival = False
        
        if time_mode != "יציאה עכשיו":
            with t_col2: d = st.date_input("תאריך")
            with t_col3: t = st.time_input("שעה")
            req_time = datetime.combine(d, t)
            if "הגעה" in time_mode: is_arrival = True

        # כפתור חיפוש
        submitted = st.form_submit_button("חפש מסלול 🚀")

    if submitted:
        with st.spinner('⏳ מחשב מסלול, בודק פקקים ומסדר זמנים...'):
            try:
                real_origin = "תחנה מרכזית נתניה" if origin == "המיקום שלי" else origin
                
                # פרמטרים לגוגל
                params = {
                    "origin": real_origin,
                    "destination": dest,
                    "mode": "transit",
                    "transit_mode": "bus",
                    "alternatives": True,
                    "language": "he"
                }
                
                # טיפול בזמנים (הגעה או יציאה)
                if is_arrival:
                    params["arrival_time"] = req_time
                else:
                    params["departure_time"] = req_time
                
                routes = gmaps.directions(**params)
                
                if routes:
                    # --- עיבוד התוצאות לתצוגה יפה ---
                    options = []
                    
                    for idx, r in enumerate(routes):
                        leg = r['legs'][0]
                        duration = leg['duration']['text'].replace("hours", "שעות").replace("mins", "דקות").replace("hour", "שעה")
                        
                        # חישוב הליכה
                        walk_sec = sum([s['duration']['value'] for s in leg['steps'] if s['travel_mode']=='WALKING'])
                        walk_min = int(walk_sec / 60)
                        
                        # כותרת ברורה לרדיו
                        label = f"אופציה {idx+1}: ⏱️ {duration} | 🚶 {walk_min} דק' הליכה"
                        
                        # שמירת המידע
                        options.append({
                            "label": label,
                            "data": r,
                            "walk": walk_min,
                            "duration_text": duration
                        })
                    
                    st.success(f"נמצאו {len(options)} אפשרויות נסיעה:")
                    
                    # בחירת מסלול עם תצוגה מתוקנת
                    selection = st.radio("בחר מסלול להצגה:", options, format_func=lambda x: x["label"])
                    
                    if selection:
                        r = selection["data"]
                        leg = r['legs'][0]
                        
                        # --- כרטיס מידע מפורט (HTML) ---
                        # כאן אנחנו מסדרים את הזמנים בצורה גרפית יפה
                        dep_time = leg['departure_time']['text']
                        arr_time = leg['arrival_time']['text']
                        
                        st.markdown(f"""
                        <div class="result-card">
                            <h3 style="margin:0; color:#581845;">פרטי המסלול הנבחר</h3>
                            <div style="display:flex; justify-content:space-around; margin-top:10px;">
                                <div class="metric-box"><b>🛫 יציאה</b><br>{dep_time}</div>
                                <div class="metric-box"><b>⏱️ משך</b><br>{selection['duration_text']}</div>
                                <div class="metric-box"><b>🏁 הגעה</b><br>{arr_time}</div>
                            </div>
                        </div>
                        """, unsafe_allow_html=True)

                        # --- המפה ---
                        m = folium.Map(location=[leg['start_location']['lat'], leg['start_location']['lng']], zoom_start=13)
                        folium.TileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}', attr='Traffic', overlay=True).add_to(m)
                        
                        # ציור המסלול
                        path_pts = []
                        for step in leg['steps']:
                            pts = polyline.decode(step['polyline']['points'])
                            path_pts.extend(pts)
                            
                            if step['travel_mode'] == 'TRANSIT':
                                # קו אוטובוס
                                line = step['transit_details']['line']['short_name']
                                
                                # בדיקת פקק קטנה
                                traffic_add = 0
                                if not is_arrival: # אי אפשר לבדוק פקקים עתידיים מדויק, רק נוכחי או יציאה
                                    d_time = datetime.fromtimestamp(step['transit_details']['departure_time']['value'])
                                    traffic_add = get_traffic_delay(step['transit_details']['departure_stop']['location'], step['transit_details']['arrival_stop']['location'], d_time)
                                
                                color = "#581845"
                                tooltip = f"קו {line}"
                                if traffic_add > 5: 
                                    color = "red"
                                    tooltip += f" (עומס +{int(traffic_add)} דק')"
                                
                                folium.PolyLine(pts, color=color, weight=6, opacity=0.8, tooltip=tooltip).add_to(m)
                            else:
                                # הליכה
                                folium.PolyLine(pts, color="blue", weight=4, dash_array='5, 10', opacity=0.5, tooltip="הליכה").add_to(m)
                                
                        # זום למסלול
                        m.fit_bounds(path_pts)
                        components.html(m._repr_html_(), height=450)
                        
                        # הוראות כתובות (מתוקנות RTL)
                        with st.expander("📝 הוראות נסיעה מפורטות"):
                            for step in leg['steps']:
                                instr = step['html_instructions']
                                dist = step['distance']['text']
                                st.markdown(f"<div style='direction:rtl; text-align:right;'>• {instr} ({dist})</div>", unsafe_allow_html=True)
                else:
                    st.error("לא נמצאו מסלולים לחיפוש זה.")
            except Exception as e:
                st.error(f"אירעה שגיאה: {e}")

# ==========================================
# 2. איתור קו (SQL)
# ==========================================
with tab_lines:
    if init_database():
        line = st.text_input("הכנס מספר קו:", "")
        if line:
            res = get_routes_sql(line)
            if not res.empty:
                opts = {f"{r['route_long_name']}": r['route_id'] for i, r in res.iterrows()}
                sel = st.selectbox("בחר כיוון:", list(opts.keys()))
                if st.button("הצג מסלול"):
                    pts = get_shape_sql(opts[sel])
                    if pts:
                        mid = pts[len(pts)//2]
                        m2 = folium.Map(location=mid, zoom_start=12)
                        folium.TileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}', attr='Traffic', overlay=True).add_to(m2)
                        folium.PolyLine(pts, color="purple", weight=5).add_to(m2)
                        components.html(m2._repr_html_(), height=500)
            else: st.warning("לא נמצא")

# ==========================================
# 3. חיפוש תחנה
# ==========================================
with tab_stations:
    st.info("חפש תחנה ספציפית כדי לראות את מיקומה")
    q = st.text_input("שם תחנה:", "סבידור מרכז")
    if st.button("מצא תחנה"):
        r = gmaps.places(query=q)
        if r['status'] == 'OK':
            loc = r['results'][0]['geometry']['location']
            name = r['results'][0]['name']
            m3 = folium.Map(location=[loc['lat'], loc['lng']], zoom_start=17)
            folium.TileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}', attr='Traffic', overlay=True).add_to(m3)
            folium.Marker([loc['lat'], loc['lng']], popup=name, icon=folium.Icon(color='blue', icon='bus', prefix='fa')).add_to(m3)
            components.html(m3._repr_html_(), height=400)

# ==========================================
# 4. GPS
# ==========================================
with tab_gps:
    if st.button("איפה אני?"):
        m4 = folium.Map(location=[32.08, 34.78], zoom_start=14)
        from folium import plugins
        plugins.LocateControl(auto_start=True).add_to(m4)
        folium.TileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}', attr='Traffic', overlay=True).add_to(m4)
        st_folium(m4, height=500)
