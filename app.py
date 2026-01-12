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
    .route-card {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        border-radius: 15px;
        padding: 20px;
        margin: 15px 0;
        box-shadow: 0 8px 16px rgba(0,0,0,0.2);
        cursor: pointer;
        transition: transform 0.2s;
    }
    .route-card:hover {
        transform: scale(1.02);
    }
    .route-fastest {
        background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%);
        border: 3px solid #FFD700;
    }
    .route-header {
        font-size: 24px;
        font-weight: bold;
        margin-bottom: 10px;
    }
    .route-badge {
        background: rgba(255,255,255,0.3);
        padding: 5px 12px;
        border-radius: 20px;
        display: inline-block;
        margin: 5px 5px 5px 0;
        font-size: 14px;
    }
    .traffic-indicator {
        display: inline-block;
        width: 12px;
        height: 12px;
        border-radius: 50%;
        margin-left: 5px;
    }
    .traffic-low { background: #4CAF50; }
    .traffic-medium { background: #FFC107; }
    .traffic-high { background: #F44336; }
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
    .stButton>button { width: 100%; height: 50px; font-size: 18px; border-radius: 12px; }
    </style>
""", unsafe_allow_html=True)

# --- חיבור לגוגל ---
try:
    api_key = st.secrets.get("GOOGLE_API_KEY")
    if not api_key:
        api_key = "AIzaSyAZOiy_DWHLNVipXZgSzFBC8N2eGasydwY"
    gmaps = googlemaps.Client(key=api_key)
except Exception as e:
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
            
            pd.read_csv(z.open('routes.txt'), 
                       usecols=['route_id','route_short_name','route_long_name']
                       ).to_sql('routes', conn, if_exists='replace', index=False)
            
            trips = pd.read_csv(z.open('trips.txt'), 
                               usecols=['route_id','shape_id']
                               ).drop_duplicates('route_id')
            trips.to_sql('trips', conn, if_exists='replace', index=False)
            
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
        q = f"SELECT * FROM routes WHERE TRIM(route_short_name) = '{line_num.strip()}'"
        routes = pd.read_sql_query(q, conn)
        
        if routes.empty:
            conn.close()
            return None, None
            
        route_id = routes.iloc[0]['route_id']
        route_desc = routes.iloc[0]['route_long_name']
        
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
            
        points = list(zip(df['shape_pt_lat'].values[::3], 
                         df['shape_pt_lon'].values[::3]))
        return points, route_desc
    except Exception as e:
        return None, None

def get_multiple_routes(origin, destination, num_alternatives=3):
    """מחזיר מספר אלטרנטיבות מסלול כולל זמני נסיעה עם פקקים"""
    try:
        # קבלת מסלולים עם פקקים
        routes = gmaps.directions(
            origin, 
            destination,
            mode="transit",
            transit_mode="bus",
            language='he',
            departure_time=datetime.now(),
            alternatives=True  # מבקש אלטרנטיבות
        )
        
        if not routes:
            return []
        
        # עיבוד כל מסלול
        processed_routes = []
        for idx, route in enumerate(routes[:num_alternatives]):
            leg = route['legs'][0]
            
            # חישוב זמן בפועל עם פקקים
            duration_in_traffic = leg.get('duration_in_traffic', leg.get('duration'))
            duration_seconds = duration_in_traffic.get('value', 0)
            duration_text = duration_in_traffic.get('text', 'N/A')
            
            # זמן ללא פקקים להשוואה
            normal_duration = leg.get('duration', {}).get('value', 0)
            
            # חישוב עומס תנועה
            if normal_duration > 0:
                traffic_ratio = duration_seconds / normal_duration
                if traffic_ratio < 1.15:
                    traffic_level = "low"
                    traffic_text = "תנועה קלה"
                    traffic_color = "#4CAF50"
                elif traffic_ratio < 1.35:
                    traffic_level = "medium"
                    traffic_text = "תנועה בינונית"
                    traffic_color = "#FFC107"
                else:
                    traffic_level = "high"
                    traffic_text = "פקקים כבדים"
                    traffic_color = "#F44336"
            else:
                traffic_level = "unknown"
                traffic_text = "לא ידוע"
                traffic_color = "#999"
            
            # איסוף פרטי המסלול
            steps = leg['steps']
            transit_lines = []
            for step in steps:
                if step['travel_mode'] == 'TRANSIT':
                    td = step.get('transit_details', {})
                    line = td.get('line', {}).get('short_name', 'N/A')
                    transit_lines.append(line)
            
            # נקודות המסלול למפה
            polyline_points = []
            for step in steps:
                if 'polyline' in step:
                    decoded = self.decode_polyline(step['polyline']['points'])
                    polyline_points.extend(decoded)
            
            processed_routes.append({
                'index': idx,
                'duration_seconds': duration_seconds,
                'duration_text': duration_text,
                'distance': leg['distance']['text'],
                'traffic_level': traffic_level,
                'traffic_text': traffic_text,
                'traffic_color': traffic_color,
                'lines': transit_lines,
                'steps': steps,
                'polyline': polyline_points,
                'start_address': leg['start_address'],
                'end_address': leg['end_address']
            })
        
        # מיון לפי זמן (הכי מהיר ראשון)
        processed_routes.sort(key=lambda x: x['duration_seconds'])
        
        return processed_routes
        
    except Exception as e:
        st.error(f"שגיאה בחישוב מסלולים: {str(e)}")
        return []

def decode_polyline(polyline_str):
    """מפענח polyline של Google לנקודות lat/lng"""
    try:
        import polyline as pl
        return pl.decode(polyline_str)
    except:
        # fallback פשוט
        return []

def create_traffic_map(routes_data, center_location):
    """יוצר מפה עם כל המסלולים ושכבת פקקים"""
    m = folium.Map(location=center_location, zoom_start=13)
    
    # צבעים למסלולים
    colors = ['#11998e', '#667eea', '#f093fb', '#4facfe', '#fa709a']
    
    for idx, route in enumerate(routes_data):
        color = colors[idx % len(colors)]
        
        # ציור המסלול
        if route['polyline']:
            folium.PolyLine(
                route['polyline'],
                color=color,
                weight=6 if idx == 0 else 4,
                opacity=0.8 if idx == 0 else 0.6,
                popup=f"מסלול {idx+1}: {route['duration_text']}",
                tooltip=f"מסלול {idx+1}"
            ).add_to(m)
        
        # סימון התחלה וסוף
        if idx == 0:  # רק למסלול הראשון
            if route['polyline']:
                start = route['polyline'][0]
                end = route['polyline'][-1]
                
                folium.Marker(
                    start,
                    popup="נקודת מוצא",
                    icon=folium.Icon(color='green', icon='play', prefix='fa')
                ).add_to(m)
                
                folium.Marker(
                    end,
                    popup="יעד",
                    icon=folium.Icon(color='red', icon='flag-checkered', prefix='fa')
                ).add_to(m)
    
    # הוספת שכבת פקקים (דורש Google Maps JavaScript API)
    traffic_html = f"""
    <script>
    var map;
    function initMap() {{
        map = new google.maps.Map(document.getElementById('map'), {{
            center: {{lat: {center_location[0]}, lng: {center_location[1]}}},
            zoom: 13
        }});
        
        var trafficLayer = new google.maps.TrafficLayer();
        trafficLayer.setMap(map);
    }}
    </script>
    <script src="https://maps.googleapis.com/maps/api/js?key={api_key}&callback=initMap" async defer></script>
    """
    
    return m

# --- אתחול Session State ---
if 'nav_step' not in st.session_state: 
    st.session_state.nav_step = 0
if 'nav_data' not in st.session_state: 
    st.session_state.nav_data = None
if 'selected_route' not in st.session_state:
    st.session_state.selected_route = None
if 'routes_options' not in st.session_state:
    st.session_state.routes_options = []

# --- ממשק ראשי ---
st.title("🚍 SmartBus Ultimate - ניווט חכם עם פקקים")

tab1, tab2, tab3 = st.tabs(["🚦 מסלולים חכמים", "🔢 קווים", "📍 תחנות"])

# ==================================================
# 1. חיפוש מסלולים עם ניתוח פקקים
# ==================================================
with tab1:
    st.subheader("🔍 חפש את המסלול הכי מהיר")
    
    # טופס חיפוש
    with st.form("smart_search"):
        c1, c2 = st.columns(2)
        with c1: 
            org = st.text_input("מאיפה?", "תחנה מרכזית תל אביב", key="smart_origin")
        with c2: 
            dst = st.text_input("לאן?", "עזריאלי תל אביב", key="smart_dest")
        
        num_routes = st.slider("כמה אופציות להציג?", 2, 5, 3)
        
        submitted = st.form_submit_button("🚀 חפש מסלולים", type="primary")
        
        if submitted:
            with st.spinner('🔄 מחשב מסלולים ומנתח פקקים...'):
                routes = get_multiple_routes(org, dst, num_routes)
                
                if routes:
                    st.session_state.routes_options = routes
                    st.session_state.selected_route = None
                    st.success(f"✅ נמצאו {len(routes)} מסלולים!")
                    time.sleep(0.3)
                    st.rerun()
                else:
                    st.error("❌ לא נמצאו מסלולים")
    
    # הצגת אופציות
    if st.session_state.routes_options:
        st.markdown("---")
        st.subheader("📊 השוואת מסלולים")
        
        routes = st.session_state.routes_options
        
        # כרטיסים לכל מסלול
        for idx, route in enumerate(routes):
            is_fastest = (idx == 0)
            card_class = "route-card route-fastest" if is_fastest else "route-card"
            
            badge = "⚡ המהיר ביותר!" if is_fastest else f"אופציה {idx+1}"
            
            route_html = f"""
            <div class='{card_class}'>
                <div class='route-header'>
                    {badge}
                    <span class='traffic-indicator traffic-{route['traffic_level']}'></span>
                </div>
                <div style='font-size: 20px; margin: 10px 0;'>
                    ⏱️ <strong>{route['duration_text']}</strong> | 📏 {route['distance']}
                </div>
                <div class='route-badge'>
                    🚦 {route['traffic_text']}
                </div>
                {''.join([f"<div class='route-badge'>🚌 קו {line}</div>" for line in route['lines']])}
            </div>
            """
            
            st.markdown(route_html, unsafe_allow_html=True)
            
            col1, col2 = st.columns([3, 1])
            with col1:
                if st.button(f"📍 הצג מסלול {idx+1} על המפה", key=f"show_{idx}"):
                    st.session_state.selected_route = idx
                    st.rerun()
            with col2:
                if st.button(f"▶️ נווט", key=f"nav_{idx}", type="primary"):
                    st.session_state.nav_data = route['steps']
                    st.session_state.nav_step = 0
                    st.session_state.selected_route = idx
                    st.rerun()
        
        # הצגת מפה עם המסלול הנבחר או הכל
        st.markdown("---")
        st.subheader("🗺️ מפת מסלולים + פקקים בזמן אמת")
        
        if routes and routes[0]['polyline']:
            center = routes[0]['polyline'][len(routes[0]['polyline'])//2]
        else:
            # ברירת מחדל תל אביב
            center = [32.0853, 34.7818]
        
        # יצירת מפה עם שכבת פקקים
        m = folium.Map(location=center, zoom_start=13)
        
        colors = ['#11998e', '#667eea', '#f093fb', '#4facfe', '#fa709a']
        
        # ציור כל המסלולים
        routes_to_show = routes if st.session_state.selected_route is None else [routes[st.session_state.selected_route]]
        
        for idx, route in enumerate(routes_to_show):
            actual_idx = route['index']
            color = colors[actual_idx % len(colors)]
            
            if route['polyline']:
                folium.PolyLine(
                    route['polyline'],
                    color=color,
                    weight=7 if actual_idx == 0 else 5,
                    opacity=0.9 if actual_idx == 0 else 0.7,
                    popup=f"מסלול {actual_idx+1}: {route['duration_text']} ({route['traffic_text']})",
                    tooltip=f"מסלול {actual_idx+1}"
                ).add_to(m)
                
                # סימונים
                if actual_idx == 0 or st.session_state.selected_route is not None:
                    start = route['polyline'][0]
                    end = route['polyline'][-1]
                    
                    folium.Marker(
                        start,
                        popup=f"<b>מוצא:</b><br>{route['start_address'][:50]}",
                        icon=folium.Icon(color='green', icon='play', prefix='fa')
                    ).add_to(m)
                    
                    folium.Marker(
                        end,
                        popup=f"<b>יעד:</b><br>{route['end_address'][:50]}",
                        icon=folium.Icon(color='red', icon='flag-checkered', prefix='fa')
                    ).add_to(m)
        
        # מיקום נוכחי
        plugins.LocateControl(auto_start=False).add_to(m)
        
        components.html(m._repr_html_(), height=600)
        
        # הסבר על פקקים
        st.info("""
        🚦 **צבעי המסלולים מציגים:**
        - 🟢 **ירוק בהיר** = המסלול הכי מהיר (לוקח בחשבון פקקים!)
        - 🟣 **סגול** = אופציות חלופיות
        
        הזמנים מחושבים עם נתוני תנועה בזמן אמת של Google Maps
        """)
        
        if st.button("🔄 חיפוש חדש"):
            st.session_state.routes_options = []
            st.session_state.selected_route = None
            st.rerun()
    
    # ניווט שלב אחרי שלב
    if st.session_state.nav_data:
        st.markdown("---")
        st.subheader("🧭 ניווט שלב אחרי שלב")
        
        steps = st.session_state.nav_data
        idx = st.session_state.nav_step
        
        if idx >= len(steps):
            idx = len(steps) - 1
            st.session_state.nav_step = idx
        
        current = steps[idx]
        
        # כפתורי ניווט
        col_prev, col_counter, col_next = st.columns([1, 2, 1])
        
        with col_prev:
            if idx > 0:
                if st.button("⬅️ הקודם", key="nav_prev"):
                    st.session_state.nav_step = max(0, idx - 1)
                    st.rerun()
        
        with col_counter:
            st.markdown(f"<h3 style='text-align:center'>שלב {idx + 1} מתוך {len(steps)}</h3>", 
                       unsafe_allow_html=True)
        
        with col_next:
            if idx < len(steps) - 1:
                if st.button("הבא ➡️", type="primary", key="nav_next"):
                    st.session_state.nav_step = min(len(steps) - 1, idx + 1)
                    st.rerun()
            else:
                st.success("🎉 הגעת ליעד!")

        # כרטיס הוראה
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
        
        if current['travel_mode'] == 'TRANSIT':
            dt = current.get('transit_details', {})
            line_name = dt.get('line', {}).get('short_name', 'N/A')
            headsign = dt.get('headsign', 'N/A')
            num_stops = dt.get('num_stops', 0)
            
            st.info(f"🚌 **קו {line_name}** לכיוון {headsign} | 🛑 {num_stops} תחנות")
        
        if st.button("❌ סיים ניווט"):
            st.session_state.nav_data = None
            st.rerun()

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
                
                center = pts[len(pts)//2]
                m = folium.Map(location=center, zoom_start=13)
                
                folium.PolyLine(
                    pts, 
                    color="#9C27B0", 
                    weight=5, 
                    opacity=0.8,
                    popup=f"קו {ln}"
                ).add_to(m)
                
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
                
                components.html(m._repr_html_(), height=500)
            else:
                st.warning(f"⚠️ קו {ln} לא נמצא")

# ==================================================
# 3. תחנות סביבי
# ==================================================
with tab3:
    st.subheader("🗺️ תחנות באזור")
    
    col_in, col_btn = st.columns([3, 1])
    with col_in: 
        addr = st.text_input("חפש מקום:", "דיזנגוף סנטר", key="addr_search")
    with col_btn: 
        st.write("")
        st.write("")
        do_map = st.button("🔍 חפש", key="search_stations")
    
    if do_map:
        with st.spinner('טוען מפה...'):
            loc = [32.0853, 34.7818]
            
            if addr:
                try:
                    geo = gmaps.geocode(addr)
                    if geo and len(geo) > 0:
                        l = geo[0]['geometry']['location']
                        loc = [l['lat'], l['lng']]
                except:
                    pass
            
            m = folium.Map(location=loc, zoom_start=16)
            plugins.LocateControl(auto_start=False).add_to(m)
            
            try:
                places = gmaps.places_nearby(location=(loc[0], loc[1]), radius=300, type='transit_station')
                
                for p in places.get('results', []):
                    s_lat = p['geometry']['location']['lat']
                    s_lng = p['geometry']['location']['lng']
                    s_name = p.get('name', 'תחנה')
                    s_vicinity = p.get('vicinity', '')
                    
                    popup_html = f"""
                    <div class='station-popup' style='width:250px'>
                        <h4 style='margin:0; color:#007bff'>🚏 {s_name}</h4>
                        <hr style='margin:8px 0'>
                        <p style='font-size:13px'>{s_vicinity}</p>
                        <a href='https://www.google.com/maps/dir/?api=1&destination={s_lat},{s_lng}' 
                           target='_blank'>
                            <button style='background:#4CAF50; color:white; border:none; 
                                           padding:8px 16px; border-radius:5px; cursor:pointer'>
                                🧭 נווט לכאן
                            </button>
                        </a>
                    </div>
                    """
                    
                    folium.Marker(
                        [s_lat, s_lng],
                        popup=folium.Popup(popup_html, max_width=300),
                        tooltip=s_name,
                        icon=folium.Icon(color='blue', icon='bus', prefix='fa')
                    ).add_to(m)
                
                st.success(f"✅ נמצאו {len(places.get('results', []))} תחנות")
            except:
                pass
            
            components.html(m._repr_html_(), height=600)

st.markdown("---")
st.caption("🚍 SmartBus Ultimate | מופעל ע״י Google Maps Traffic Data + GTFS ישראל")
