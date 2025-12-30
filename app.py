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

# --- הגדרות מערכת ---
st.set_page_config(page_title="SmartBus Master", page_icon="🚍", layout="wide")
DB_FILE = 'gtfs_israel.db'
ISRAEL_TZ = pytz.timezone('Asia/Jerusalem')

# --- התחברות לגוגל ---
try:
    api_key = st.secrets["GOOGLE_API_KEY"]
    gmaps = googlemaps.Client(key=api_key)
except:
    st.error("⚠️ שגיאה: מפתח Google API חסר בקובץ הסודות.")
    st.stop()

# --- מנוע ה-SQL (כולל מנגנון למניעת קריסות) ---
@st.cache_resource(show_spinner=False)
def init_database():
    if os.path.exists(DB_FILE): return True
    try:
        url = "https://gtfs.mot.gov.il/gtfsfiles/israel-public-transportation.zip"
        with st.spinner('📥 טוען את מאגר משרד התחבורה (חד פעמי)...'):
            r = requests.get(url)
            z = zipfile.ZipFile(io.BytesIO(r.content))
            conn = sqlite3.connect(DB_FILE)
            
            # טעינת טבלאות
            pd.read_csv(z.open('routes.txt'), usecols=['route_id', 'route_short_name', 'route_long_name']).to_sql('routes', conn, if_exists='replace', index=False)
            trips = pd.read_csv(z.open('trips.txt'), usecols=['route_id', 'shape_id'])
            trips.drop_duplicates(subset=['route_id']).to_sql('trips', conn, if_exists='replace', index=False)
            
            # טעינת צורות בבלוקים
            for chunk in pd.read_csv(z.open('shapes.txt'), chunksize=100000):
                chunk.to_sql('shapes', conn, if_exists='append', index=False)
            
            # אינדקסים למהירות
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
        
        # === דילול נקודות למניעת קריסה ===
        # לוקח רק כל נקודה חמישית. זה שומר על האפליקציה מהירה ויציבה בטלפון
        return list(zip(df['shape_pt_lat'].values[::5], df['shape_pt_lon'].values[::5]))
    except: return []
    finally: conn.close()

# --- עיצוב CSS (עברית תקינה + ארנק) ---
st.markdown("""
    <style>
    .stApp { direction: rtl; }
    
    /* כרטיס תוצאה בניווט */
    .route-card {
        background-color: #fff; padding: 15px; border-radius: 10px;
        border-right: 5px solid #581845; margin-bottom: 10px;
        box-shadow: 0 2px 5px rgba(0,0,0,0.1);
    }
    
    /* ארנק */
    .wallet-card {
        background: linear-gradient(135deg, #0f2027 0%, #203a43 50%, #2c5364 100%);
        color: white; padding: 20px; border-radius: 15px;
        text-align: right; margin-bottom: 20px;
    }
    
    /* אזהרת סימולציה */
    .sim-warning {
        background-color: #ffeba1; color: #856404; padding: 10px;
        border-radius: 5px; font-size: 12px; text-align: center; margin-top: 10px;
    }
    </style>
""", unsafe_allow_html=True)

# --- סרגל צד להגדרות ---
with st.sidebar:
    st.header("⚙️ הגדרות")
    max_walking = st.slider("מקסימום הליכה (דקות):", 0, 30, 10)
    st.caption("מסלולים עם הליכה ארוכה יסומנו באזהרה ⚠️")

# --- כותרת ראשית ---
st.title("🚍 SmartBus Master")

# --- הטאבים (כל הפיצ'רים ביחד) ---
tab_nav, tab_lines, tab_stations, tab_wallet = st.tabs(["🗺️ תכנון מסלול", "🔢 איתור קו", "🚏 תחנות", "💳 ארנק"])

# ==========================================
# 1. תכנון מסלול (ניווט + זמנים + מיקום שלי)
# ==========================================
with tab_nav:
    st.caption("תכנון נסיעה חכם עם פקקים בזמן אמת")
    
    with st.form("nav_form"):
        # מיקום ויעד
        c1, c2 = st.columns(2)
        with c1: origin = st.text_input("מוצא", "המיקום שלי")
        with c2: dest = st.text_input("יעד", "עזריאלי תל אביב")
        
        st.divider()
        
        # בחירת זמן (חזרה לבקשתך!)
        t_col1, t_col2, t_col3 = st.columns(3)
        with t_col1:
            time_option = st.selectbox("זמן הנסיעה", ["יציאה עכשיו", "יציאה בשעה...", "הגעה עד שעה..."])
        
        req_time = datetime.now()
        is_arrival = False
        
        if time_option != "יציאה עכשיו":
            with t_col2: d = st.date_input("תאריך")
            with t_col3: t = st.time_input("שעה")
            req_time = datetime.combine(d, t)
            if "הגעה" in time_option: is_arrival = True
            
        submit_nav = st.form_submit_button("חפש מסלול ופקקים 🚀")

    if submit_nav:
        with st.spinner('מנתח חלופות נסיעה...'):
            try:
                # לוגיקה ל"מיקום שלי"
                real_origin = "תחנה מרכזית נתניה" if origin == "המיקום שלי" else origin
                
                # הכנת הפרמטרים לגוגל
                params = {
                    "origin": real_origin,
                    "destination": dest,
                    "mode": "transit",
                    "transit_mode": "bus",
                    "alternatives": True,
                    "language": "he"
                }
                if is_arrival:
                    params["arrival_time"] = req_time
                else:
                    params["departure_time"] = req_time
                
                # שליחה לגוגל
                routes = gmaps.directions(**params)
                
                if routes:
                    st.success(f"נמצאו {len(routes)} מסלולים:")
                    
                    # הצגת אפשרויות בחירה (רדיו)
                    options = []
                    for i, r in enumerate(routes):
                        leg = r['legs'][0]
                        dur = leg['duration']['text']
                        walk_sec = sum([s['duration']['value'] for s in leg['steps'] if s['travel_mode']=='WALKING'])
                        walk_min = int(walk_sec / 60)
                        
                        label = f"מסלול {i+1}: ⏱️ {dur} | 🚶 {walk_min} דק' הליכה"
                        if walk_min > max_walking: label += " ⚠️"
                        
                        options.append({"label": label, "data": r, "walk": walk_min})
                    
                    selection = st.radio("בחר מסלול:", options, format_func=lambda x: x['label'])
                    
                    if selection:
                        r = selection['data']
                        leg = r['legs'][0]
                        
                        # כרטיס פרטים יפה
                        dep = leg['departure_time']['text']
                        arr = leg['arrival_time']['text']
                        st.markdown(f"""
                        <div class="route-card">
                            <div style="display:flex; justify-content:space-between;">
                                <div><b>יציאה:</b> {dep}</div>
                                <div><b>משך:</b> {leg['duration']['text']}</div>
                                <div><b>הגעה:</b> {arr}</div>
                            </div>
                        </div>
                        """, unsafe_allow_html=True)
                        
                        # בניית המפה
                        m = folium.Map(location=[leg['start_location']['lat'], leg['start_location']['lng']], zoom_start=14)
                        
                        # כפתור GPS (המיקום שלי)
                        from folium import plugins
                        plugins.LocateControl(auto_start=False, strings={"title": "המיקום שלי"}).add_to(m)
                        
                        # שכבת פקקים
                        folium.TileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}', attr='Traffic', overlay=True).add_to(m)
                        
                        path_pts = []
                        for step in leg['steps']:
                            pts = polyline.decode(step['polyline']['points'])
                            path_pts.extend(pts)
                            
                            if step['travel_mode'] == 'TRANSIT':
                                line_name = step['transit_details']['line']['short_name']
                                folium.PolyLine(pts, color="#581845", weight=6, opacity=0.8, tooltip=f"קו {line_name}").add_to(m)
                            else:
                                folium.PolyLine(pts, color="blue", weight=4, dash_array='5, 10', opacity=0.6, tooltip="הליכה").add_to(m)
                        
                        m.fit_bounds(path_pts)
                        
                        # שימוש ברכיב היציב (Components) למניעת קריסות
                        components.html(m._repr_html_(), height=450)
                        
                        with st.expander("📝 הוראות נסיעה מפורטות"):
                            for step in leg['steps']:
                                st.write(step['html_instructions'], unsafe_allow_html=True)

                else:
                    st.error("לא נמצאו מסלולים.")
            except Exception as e:
                st.error(f"שגיאה: {e}")

# ==========================================
# 2. איתור קו (SQL) - התיקון שהיה חסר!
# ==========================================
with tab_lines:
    st.info("חיפוש מסלול מדויק של כל קו בישראל")
    if init_database():
        # הנה שורת החיפוש שהחזרתי!
        line_num = st.text_input("הזן מספר קו:", "")
        
        if line_num:
            res = get_routes_sql(line_num)
            if not res.empty:
                opts = {f"{r['route_long_name']}": r['route_id'] for i, r in res.iterrows()}
                sel = st.selectbox("בחר כיוון נסיעה:", list(opts.keys()))
                
                if st.button("הצג מסלול על המפה 🗺️"):
                    pts = get_shape_sql(opts[sel])
                    if pts:
                        mid = pts[len(pts)//2]
                        m2 = folium.Map(location=mid, zoom_start=12)
                        folium.TileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}', attr='Traffic', overlay=True).add_to(m2)
                        
                        # קו סגול למניעת בלבול עם פקקים
                        folium.PolyLine(pts, color="purple", weight=5, opacity=0.8).add_to(m2)
                        
                        components.html(m2._repr_html_(), height=500)
                    else:
                        st.warning("אין נתוני מפה לקו זה.")
            else:
                st.warning("קו לא נמצא במאגר.")

# ==========================================
# 3. תחנות וסביבה
# ==========================================
with tab_stations:
    st.caption("חיפוש תחנה וסריקת הסביבה")
    q = st.text_input("שם תחנה:", "סבידור מרכז")
    if st.button("חפש תחנה 🔎"):
        r = gmaps.places(query=q)
        if r['status'] == 'OK':
            loc = r['results'][0]['geometry']['location']
            name = r['results'][0]['name']
            
            m3 = folium.Map(location=[loc['lat'], loc['lng']], zoom_start=17)
            plugins.LocateControl(auto_start=False).add_to(m3)
            folium.TileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}', attr='Traffic', overlay=True).add_to(m3)
            
            # התחנה הראשית
            folium.Marker([loc['lat'], loc['lng']], popup=name, icon=folium.Icon(color='red', icon='star', prefix='fa')).add_to(m3)
            
            # תחנות מסביב
            try:
                nearby = gmaps.places_nearby(location=loc, radius=300, type='transit_station')
                for p in nearby.get('results', []):
                    if p['place_id'] != r['results'][0]['place_id']:
                        ploc = p['geometry']['location']
                        folium.Marker([ploc['lat'], ploc['lng']], tooltip=p['name'], icon=folium.Icon(color='blue', icon='bus', prefix='fa')).add_to(m3)
            except: pass
            
            components.html(m3._repr_html_(), height=450)
            st.success(f"נמצאה: {name}")
        else:
            st.error("לא נמצא.")

# ==========================================
# 4. ארנק (סימולציה)
# ==========================================
with tab_wallet:
    st.markdown("""
    <div class="wallet-card">
        <div style="font-size:14px; opacity:0.8;">יתרה נוכחית</div>
        <div style="font-size:36px; font-weight:bold;">₪ 45.90</div>
        <div style="margin-top:10px; font-size:14px;">חוזה: חופשי חודשי (גוש דן)</div>
    </div>
    """, unsafe_allow_html=True)
    
    if st.button("📱 סרוק ברקוד לתשלום", type="primary", use_container_width=True):
        with st.spinner("מתקשר למסוף..."):
            time.sleep(1.5)
        st.balloons()
        st.success("✅ אושר! נסיעה נעימה.")
        st.markdown('<div class="sim-warning">🛑 שים לב: זוהי סימולציה. לא לשימוש מול פקחים.</div>', unsafe_allow_html=True)
