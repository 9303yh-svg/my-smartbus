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
import time

# --- הגדרות ---
st.set_page_config(page_title="SmartBus Ultimate", page_icon="🚍", layout="wide")
DB_FILE = 'gtfs_israel.db'

# --- CSS מותאם ---
st.markdown("""
    <style>
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
    .station-popup { direction: rtl; text-align: right; font-family: sans-serif; }
    .line-badge { 
        background-color: #4CAF50; 
        color: white;
        padding: 4px 10px; 
        border-radius: 12px; 
        font-size: 14px; 
        margin: 3px; 
        display: inline-block;
        font-weight: bold;
    }
    .stButton>button { width: 100%; height: 50px; font-size: 18px; border-radius: 12px; }
    </style>
""", unsafe_allow_html=True)

# --- חיבור לגוגל ---
try:
    # ניסיון ראשון: קריאה מ-secrets
    api_key = st.secrets.get("GOOGLE_API_KEY")
    if not api_key:
        # גיבוי: מפתח קבוע
        api_key = "AIzaSyAZOiy_DWHLNVipXZgSzFBC8N2eGasydwY"
    gmaps = googlemaps.Client(key=api_key)
except Exception as e:
    # אם אין secrets, משתמש במפתח ישירות
    api_key = "AIzaSyAZOiy_DWHLNVipXZgSzFBC8N2eGasydwY"
    try:
        gmaps = googlemaps.Client(key=api_key)
    except Exception as e2:
        st.error(f"⚠️ שגיאה בחיבור ל-Google Maps API: {str(e2)}")
        st.stop()

# --- SQL מהיר ---
@st.cache_resource(show_spinner=False)
def init_db():
    if os.path.exists(DB_FILE): 
        return True
    try:
        url = "https://gtfs.mot.gov.il/gtfsfiles/israel-public-transportation.zip"
        with st.spinner('📥 מוריד נתונים מ-GTFS...'):
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            z = zipfile.ZipFile(io.BytesIO(r.content))
            conn = sqlite3.connect(DB_FILE)
            
            # טעינת טבלאות
            pd.read_csv(z.open('routes.txt'), 
                       usecols=['route_id','route_short_name','route_long_name']
                       ).to_sql('routes', conn, if_exists='replace', index=False)
            
            trips = pd.read_csv(z.open('trips.txt'), 
                               usecols=['route_id','shape_id']
                               ).drop_duplicates('route_id')
            trips.to_sql('trips', conn, if_exists='replace', index=False)
            
            # דילול shapes למניעת קריסה
            shapes_df = pd.read_csv(z.open('shapes.txt'))
            shapes_df.iloc[::8].to_sql('shapes', conn, if_exists='replace', index=False)
            
            conn.close()
        return True
    except Exception as e:
        st.error(f"שגיאה בהורדת נתוני GTFS: {str(e)}")
        return False

def get_route_shape(line_num):
    """מחזיר נקודות מסלול של קו"""
    try:
        conn = sqlite3.connect(DB_FILE)
        
        # חיפוש קו
        q = f"SELECT * FROM routes WHERE TRIM(route_short_name) = '{line_num.strip()}'"
        routes = pd.read_sql_query(q, conn)
        
        if routes.empty:
            conn.close()
            return None, None
            
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
        
        if df.empty:
            return None, None
            
        # דילול נוסף
        points = list(zip(df['shape_pt_lat'].values[::3], 
                         df['shape_pt_lon'].values[::3]))
        return points, route_desc
    except Exception as e:
        st.error(f"שגיאה בטעינת מסלול: {str(e)}")
        return None, None

def get_nearby_buses(lat, lng):
    """מחזיר קווי אוטובוס סמוכים למיקום"""
    try:
        places = gmaps.places_nearby(
            location=(lat, lng), 
            radius=150,
            type='transit_station'
        )
        return places.get('results', [])
    except Exception as e:
        st.warning(f"לא ניתן לטעון תחנות: {str(e)}")
        return []

def get_traffic_layer():
    """מחזיר שכבת תנועה של גוגל"""
    return """
    <script>
    function initTraffic() {
        var trafficLayer = new google.maps.TrafficLayer();
        trafficLayer.setMap(map);
    }
    </script>
    """

# --- אתחול Session State (תיקון קריסות) ---
if 'nav_step' not in st.session_state: 
    st.session_state.nav_step = 0
if 'nav_data' not in st.session_state: 
    st.session_state.nav_data = None
if 'last_search' not in st.session_state:
    st.session_state.last_search = None

# --- ממשק ראשי ---
st.title("🚍 SmartBus Ultimate")
tab1, tab2, tab3 = st.tabs(["🗺️ ניווט חי", "🔢 קווים", "📍 תחנות סביבי"])

# ==================================================
# 1. ניווט חי (תיקון קריסות במעבר בין שלבים)
# ==================================================
with tab1:
    # כפתור איפוס (תמיד זמין)
    if st.session_state.nav_data:
        if st.button("🔄 חיפוש חדש", key="reset_nav"):
            st.session_state.nav_data = None
            st.session_state.nav_step = 0
            st.session_state.last_search = None
            st.rerun()
    
    # טופס חיפוש
    if not st.session_state.nav_data:
        with st.form("search_form", clear_on_submit=False):
            c1, c2 = st.columns(2)
            with c1: 
                org = st.text_input("מוצא", "תחנה מרכזית תל אביב", key="origin_input")
            with c2: 
                dst = st.text_input("יעד", "עזריאלי תל אביב", key="dest_input")
            
            submitted = st.form_submit_button("🚀 חפש מסלול", type="primary")
            
            if submitted:
                # שמירת חיפוש למניעת כפילויות
                search_key = f"{org}->{dst}"
                if search_key != st.session_state.last_search:
                    with st.spinner('מחפש מסלול...'):
                        try:
                            res = gmaps.directions(
                                org, dst, 
                                mode="transit", 
                                transit_mode="bus",
                                language='he',
                                departure_time=datetime.now()
                            )
                            
                            if res and len(res) > 0:
                                st.session_state.nav_data = res[0]['legs'][0]['steps']
                                st.session_state.nav_step = 0
                                st.session_state.last_search = search_key
                                st.success("✅ מסלול נמצא!")
                                time.sleep(0.5)
                                st.rerun()
                            else:
                                st.error("❌ לא נמצא מסלול תחבורה ציבורית")
                        except Exception as e:
                            st.error(f"שגיאה: {str(e)}")
    
    # הצגת ניווט
    else:
        steps = st.session_state.nav_data
        idx = st.session_state.nav_step
        
        # בדיקת תקינות אינדקס
        if idx >= len(steps):
            st.session_state.nav_step = len(steps) - 1
            idx = st.session_state.nav_step
        
        current = steps[idx]
        
        # כפתורי ניווט
        col_prev, col_counter, col_next = st.columns([1, 2, 1])
        
        with col_prev:
            if idx > 0:
                if st.button("⬅️ הקודם", key="prev_btn"):
                    st.session_state.nav_step = max(0, idx - 1)
                    st.rerun()
        
        with col_counter:
            st.markdown(f"<h3 style='text-align:center'>שלב {idx + 1} מתוך {len(steps)}</h3>", 
                       unsafe_allow_html=True)
        
        with col_next:
            if idx < len(steps) - 1:
                if st.button("הבא ➡️", type="primary", key="next_btn"):
                    st.session_state.nav_step = min(len(steps) - 1, idx + 1)
                    st.rerun()
            else:
                st.success("🎉 הגעת ליעד!")

        # כרטיס הוראה נוכחית
        icon = "🚶" if current['travel_mode'] == 'WALKING' else "🚌"
        instr = current.get('html_instructions', 'המשך ישר')
        dist = current.get('distance', {}).get('text', 'N/A')
        duration = current.get('duration', {}).get('text', 'N/A')
        
        st.markdown(f"""
        <div class="nav-card">
            <span class="big-icon">{icon}</span>
            <div>{instr}</div>
            <div style="color:#666; font-size:16px; margin-top:10px;">
                📏 {dist} | ⏱️ {duration}
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        # פרטי תחבורה ציבורית
        if current['travel_mode'] == 'TRANSIT':
            dt = current.get('transit_details', {})
            line_name = dt.get('line', {}).get('short_name', 'N/A')
            headsign = dt.get('headsign', 'N/A')
            num_stops = dt.get('num_stops', 0)
            
            st.info(f"""
            **🚌 קו {line_name}** לכיוון {headsign}  
            🛑 {num_stops} תחנות
            """)
            
            # תחנות עליה וירידה
            dep_stop = dt.get('departure_stop', {}).get('name', 'N/A')
            arr_stop = dt.get('arrival_stop', {}).get('name', 'N/A')
            st.write(f"⬆️ עלייה: {dep_stop}")
            st.write(f"⬇️ ירידה: {arr_stop}")

# ==================================================
# 2. חיפוש קו
# ==================================================
with tab2:
    init_db()
    
    st.subheader("🔍 חפש מסלול קו")
    ln = st.text_input("הכנס מספר קו:", placeholder="לדוגמה: 1, 480, 89", key="line_search")
    
    if ln and st.button("הצג מסלול קו", key="show_line"):
        with st.spinner('טוען מסלול...'):
            pts, desc = get_route_shape(ln)
            
            if pts and len(pts) > 0:
                st.success(f"✅ **{desc}**")
                
                # יצירת מפה
                center = pts[len(pts)//2]
                m = folium.Map(location=center, zoom_start=13)
                
                # מסלול הקו
                folium.PolyLine(
                    pts, 
                    color="#9C27B0", 
                    weight=5, 
                    opacity=0.8,
                    popup=f"קו {ln}"
                ).add_to(m)
                
                # נקודות התחלה וסיום
                folium.Marker(
                    pts[0], 
                    popup="תחילת מסלול",
                    icon=folium.Icon(color='green', icon='play', prefix='fa')
                ).add_to(m)
                
                folium.Marker(
                    pts[-1], 
                    popup="סוף מסלול",
                    icon=folium.Icon(color='red', icon='stop', prefix='fa')
                ).add_to(m)
                
                # הצגה
                components.html(m._repr_html_(), height=500)
            else:
                st.warning(f"⚠️ קו {ln} לא נמצא במאגר GTFS")

# ==================================================
# 3. תחנות סביבי + פרטי תחנה בלחיצה
# ==================================================
with tab3:
    st.subheader("🗺️ תחנות ופקקים באזור")
    
    col_in, col_btn = st.columns([3, 1])
    with col_in: 
        addr = st.text_input("חפש כתובת או מקום:", "דיזנגוף סנטר", key="addr_search")
    with col_btn: 
        st.write("")
        st.write("")
        do_map = st.button("🔍 חפש", key="search_stations")
    
    if do_map:
        with st.spinner('טוען מפה...'):
            # מציאת מיקום
            loc = [32.0853, 34.7818]  # תל אביב ברירת מחדל
            
            if addr:
                try:
                    geo = gmaps.geocode(addr)
                    if geo and len(geo) > 0:
                        l = geo[0]['geometry']['location']
                        loc = [l['lat'], l['lng']]
                except Exception as e:
                    st.warning(f"לא נמצא מיקום מדויק, משתמש בברירת מחדל: {str(e)}")
            
            # יצירת מפה עם פקקים
            m = folium.Map(
                location=loc, 
                zoom_start=16,
                tiles='OpenStreetMap'
            )
            
            # מיקום נוכחי
            plugins.LocateControl(auto_start=False).add_to(m)
            
            # תחנות סמוכות
            try:
                stations = get_nearby_buses(loc[0], loc[1])
                
                for station in stations:
                    s_lat = station['geometry']['location']['lat']
                    s_lng = station['geometry']['location']['lng']
                    s_name = station.get('name', 'תחנה')
                    s_vicinity = station.get('vicinity', '')
                    
                    # HTML מתקדם לפופאפ
                    popup_html = f"""
                    <div class='station-popup' style='width:250px'>
                        <h4 style='margin:0; color:#007bff'>🚏 {s_name}</h4>
                        <hr style='margin:8px 0'>
                        <p style='font-size:13px; color:#666'>{s_vicinity}</p>
                        <div style='margin:10px 0'>
                            <a href='https://www.google.com/maps/dir/?api=1&destination={s_lat},{s_lng}' 
                               target='_blank' style='text-decoration:none'>
                                <button style='background:#4CAF50; color:white; border:none; 
                                               padding:8px 16px; border-radius:5px; cursor:pointer'>
                                    🧭 נווט לכאן
                                </button>
                            </a>
                        </div>
                        <small style='color:#999'>לחץ על התחנה למידע נוסף</small>
                    </div>
                    """
                    
                    folium.Marker(
                        [s_lat, s_lng],
                        popup=folium.Popup(popup_html, max_width=300),
                        tooltip=s_name,
                        icon=folium.Icon(color='blue', icon='bus', prefix='fa')
                    ).add_to(m)
                
                st.success(f"✅ נמצאו {len(stations)} תחנות באזור")
                
            except Exception as e:
                st.warning(f"לא ניתן לטעון תחנות: {str(e)}")
            
            # הצגת מפה
            components.html(m._repr_html_(), height=600)
            
            # הערה על פקקים
            st.info("""
            💡 **לצפייה בפקקים בזמן אמת:**  
            לחץ על תחנה ובחר "נווט לכאן" - גוגל מפות יציג את מצב התנועה הנוכחי.
            
            (שכבת פקקים מקורית של גוגל דורשת API נוסף - זו אלטרנטיבה מהירה)
            """)

# ==================================================
# פוטר
# ==================================================
st.markdown("---")
st.caption("🚍 SmartBus Ultimate | נתוני GTFS ממשרד התחבורה | Google Maps API")
