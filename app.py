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
from datetime import datetime, timedelta
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
    .station-item {
        background: white;
        border: 1px solid #ddd;
        border-radius: 8px;
        padding: 12px;
        margin: 8px 0;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    }
    .station-name {
        font-size: 16px;
        font-weight: bold;
        color: #007bff;
    }
    .station-distance {
        color: #666;
        font-size: 14px;
    }
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

def decode_polyline(polyline_str):
    """מפענח polyline של Google"""
    points = []
    index = 0
    lat = 0
    lng = 0
    
    while index < len(polyline_str):
        b = 0
        shift = 0
        result = 0
        
        while True:
            b = ord(polyline_str[index]) - 63
            index += 1
            result |= (b & 0x1f) << shift
            shift += 5
            if b < 0x20:
                break
        
        dlat = ~(result >> 1) if result & 1 else result >> 1
        lat += dlat
        
        shift = 0
        result = 0
        
        while True:
            b = ord(polyline_str[index]) - 63
            index += 1
            result |= (b & 0x1f) << shift
            shift += 5
            if b < 0x20:
                break
        
        dlng = ~(result >> 1) if result & 1 else result >> 1
        lng += dlng
        
        points.append((lat / 1e5, lng / 1e5))
    
    return points

def get_multiple_routes(origin, destination, num_alternatives=3, departure_time=None, arrival_time=None):
    """מחזיר מספר אלטרנטיבות מסלול"""
    try:
        # הכנת פרמטרים
        params = {
            "mode": "transit",
            "transit_mode": ["bus", "subway", "train", "tram"],
            "language": "he",
            "alternatives": True,
            "region": "il"
        }
        
        # בחירת זמן
        if arrival_time:
            params["arrival_time"] = arrival_time
        elif departure_time:
            params["departure_time"] = departure_time
        else:
            params["departure_time"] = datetime.now()
        
        # קריאה ל-API
        routes = gmaps.directions(origin, destination, **params)
        
        if not routes:
            return []
        
        # עיבוד מסלולים
        processed_routes = []
        for idx, route in enumerate(routes[:num_alternatives]):
            leg = route['legs'][0]
            
            # זמנים
            duration_in_traffic = leg.get('duration_in_traffic', leg.get('duration'))
            duration_seconds = duration_in_traffic.get('value', 0)
            duration_text = duration_in_traffic.get('text', 'N/A')
            
            normal_duration = leg.get('duration', {}).get('value', 0)
            
            # חישוב עומס
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
            
            # איסוף קווים
            steps = leg['steps']
            transit_lines = []
            for step in steps:
                if step['travel_mode'] == 'TRANSIT':
                    td = step.get('transit_details', {})
                    line = td.get('line', {}).get('short_name', 'N/A')
                    transit_lines.append(line)
            
            # פענוח polyline
            polyline_points = []
            for step in steps:
                if 'polyline' in step:
                    decoded = decode_polyline(step['polyline']['points'])
                    polyline_points.extend(decoded)
            
            # זמני יציאה והגעה
            dep_time = leg.get('departure_time', {}).get('text', 'N/A')
            arr_time = leg.get('arrival_time', {}).get('text', 'N/A')
            
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
                'end_address': leg['end_address'],
                'departure_time': dep_time,
                'arrival_time': arr_time
            })
        
        # מיון
        processed_routes.sort(key=lambda x: x['duration_seconds'])
        return processed_routes
        
    except Exception as e:
        st.error(f"שגיאה בחיפוש מסלולים: {str(e)}")
        return []

def get_nearby_stations(lat, lng, radius=500):
    """מחזיר תחנות סמוכות עם מרחק"""
    try:
        places = gmaps.places_nearby(
            location=(lat, lng),
            radius=radius,
            type='transit_station'
        )
        
        stations = []
        for p in places.get('results', []):
            s_lat = p['geometry']['location']['lat']
            s_lng = p['geometry']['location']['lng']
            
            # חישוב מרחק
            from math import radians, sin, cos, sqrt, atan2
            R = 6371000  # רדיוס כדור הארץ במטרים
            
            lat1, lon1 = radians(lat), radians(lng)
            lat2, lon2 = radians(s_lat), radians(s_lng)
            
            dlat = lat2 - lat1
            dlon = lon2 - lon1
            
            a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
            c = 2 * atan2(sqrt(a), sqrt(1-a))
            distance = R * c
            
            stations.append({
                'name': p.get('name', 'תחנה'),
                'vicinity': p.get('vicinity', ''),
                'lat': s_lat,
                'lng': s_lng,
                'distance': int(distance)
            })
        
        # מיון לפי מרחק
        stations.sort(key=lambda x: x['distance'])
        return stations
        
    except Exception as e:
        st.warning(f"לא ניתן לטעון תחנות: {str(e)}")
        return []

# --- אתחול Session State ---
if 'nav_step' not in st.session_state: 
    st.session_state.nav_step = 0
if 'nav_data' not in st.session_state: 
    st.session_state.nav_data = None
if 'selected_route' not in st.session_state:
    st.session_state.selected_route = None
if 'routes_options' not in st.session_state:
    st.session_state.routes_options = []
if 'user_location' not in st.session_state:
    st.session_state.user_location = None

# --- ממשק ראשי ---
st.title("🚍 SmartBus Ultimate - ניווט חכם עם פקקים")

tab1, tab2, tab3 = st.tabs(["🚦 מסלולים חכמים", "🔢 קווים", "📍 תחנות סביבי"])

# ==================================================
# 1. חיפוש מסלולים עם בחירת זמן
# ==================================================
with tab1:
    st.subheader("🔍 חפש את המסלול הכי מהיר")
    
    # טופס חיפוש משופר
    with st.form("smart_search"):
        c1, c2 = st.columns(2)
        with c1: 
            org = st.text_input("מאיפה?", "תחנה מרכזית תל אביב", key="smart_origin")
        with c2: 
            dst = st.text_input("לאן?", "עזריאלי תל אביב", key="smart_dest")
        
        # בחירת זמן
        st.markdown("#### ⏰ מתי לנסוע?")
        time_option = st.radio(
            "בחר אופציה:",
            ["יציאה עכשיו", "יציאה בזמן מסוים", "הגעה בזמן מסוים"],
            horizontal=True,
            key="time_option"
        )
        
        departure_time = None
        arrival_time = None
        
        if time_option == "יציאה בזמן מסוים":
            col_date, col_time = st.columns(2)
            with col_date:
                dep_date = st.date_input("תאריך יציאה", datetime.now())
            with col_time:
                dep_time = st.time_input("שעת יציאה", datetime.now().time())
            
            departure_time = datetime.combine(dep_date, dep_time)
            
        elif time_option == "הגעה בזמן מסוים":
            col_date, col_time = st.columns(2)
            with col_date:
                arr_date = st.date_input("תאריך הגעה", datetime.now())
            with col_time:
                arr_time = st.time_input("שעת הגעה", (datetime.now() + timedelta(hours=1)).time())
            
            arrival_time = datetime.combine(arr_date, arr_time)
        
        num_routes = st.slider("כמה אופציות להציג?", 2, 5, 3)
        
        submitted = st.form_submit_button("🚀 חפש מסלולים", type="primary")
        
        if submitted:
            with st.spinner('🔄 מחשב מסלולים ומנתח פקקים...'):
                try:
                    routes = get_multiple_routes(
                        org, dst, num_routes,
                        departure_time=departure_time,
                        arrival_time=arrival_time
                    )
                    
                    if routes:
                        st.session_state.routes_options = routes
                        st.session_state.selected_route = None
                        st.success(f"✅ נמצאו {len(routes)} מסלולים!")
                        time.sleep(0.3)
                        st.rerun()
                    else:
                        st.error("❌ לא נמצאו מסלולי תחבורה ציבורית")
                        st.info("💡 נסה:\n- לבדוק את כתיבת הכתובות\n- לחפש מסלול בשעות פעילות (6:00-23:00)\n- להוסיף עיר (למשל: 'תל אביב' במקום רק 'עזריאלי')")
                except Exception as e:
                    st.error(f"❌ שגיאה: {str(e)}")
                    st.info("💡 ודא שהכתובות נכונות ונסה שוב")
    
    # הצגת אופציות
    if st.session_state.routes_options:
        st.markdown("---")
        st.subheader("📊 השוואת מסלולים")
        
        routes = st.session_state.routes_options
        
        # כרטיסים
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
                <div style='font-size: 16px; margin: 10px 0;'>
                    🚀 יציאה: {route['departure_time']} | 🏁 הגעה: {route['arrival_time']}
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
        
        # מפה
        st.markdown("---")
        st.subheader("🗺️ מפת מסלולים + פקקים")
        
        if routes and routes[0]['polyline']:
            center = routes[0]['polyline'][len(routes[0]['polyline'])//2]
        else:
            center = [32.0853, 34.7818]
        
        m = folium.Map(location=center, zoom_start=13)
        colors = ['#11998e', '#667eea', '#f093fb', '#4facfe', '#fa709a']
        
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
                    popup=f"מסלול {actual_idx+1}: {route['duration_text']}",
                    tooltip=f"מסלול {actual_idx+1}"
                ).add_to(m)
                
                if actual_idx == 0 or st.session_state.selected_route is not None:
                    start = route['polyline'][0]
                    end = route['polyline'][-1]
                    
                    folium.Marker(
                        start,
                        popup=f"<b>מוצא</b><br>{route['start_address'][:50]}",
                        icon=folium.Icon(color='green', icon='play', prefix='fa')
                    ).add_to(m)
                    
                    folium.Marker(
                        end,
                        popup=f"<b>יעד</b><br>{route['end_address'][:50]}",
                        icon=folium.Icon(color='red', icon='flag-checkered', prefix='fa')
                    ).add_to(m)
        
        plugins.LocateControl(auto_start=False).add_to(m)
        components.html(m._repr_html_(), height=600)
        
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
# 3. תחנות סביבי - משופר עם מיקום וגם רשימה
# ==================================================
with tab3:
    st.subheader("🗺️ תחנות באזור")
    
    # אופציות חיפוש
    search_type = st.radio(
        "איך לחפש?",
        ["📍 המיקום שלי (GPS)", "🔍 כתובת ספציפית"],
        horizontal=True
    )
    
    addr = None
    use_gps = (search_type == "📍 המיקום שלי (GPS)")
    
    if not use_gps:
        addr = st.text_input("הכנס כתובת:", "דיזנגוף סנטר", key="addr_search")
    
    if st.button("🔍 חפש תחנות", key="search_stations", type="primary"):
        with st.spinner('טוען תחנות...'):
            loc = None
            
            if use_gps:
                # ניסיון לקבל מיקום מהמשתמש
                st.info("🌍 מנסה לאתר את המיקום שלך...")
                # ברירת מחדל אם GPS לא זמין
                loc = [32.0853, 34.7818]  # תל אביב
                st.warning("⚠️ GPS לא זמין בדפדפן - משתמש במיקום ברירת מחדל (תל אביב)")
            else:
                if addr:
                    try:
                        geo = gmaps.geocode(addr)
                        if geo and len(geo) > 0:
                            l = geo[0]['geometry']['location']
                            loc = [l['lat'], l['lng']]
                    except Exception as e:
                        st.error(f"לא נמצא מיקום: {str(e)}")
                        loc = None
            
            if loc:
                st.session_state.user_location = loc
                
                # קבלת תחנות
                stations = get_nearby_stations(loc[0], loc[1], radius=500)
                
                if stations:
                    # חלוקה לשתי עמודות - מפה ורשימה
                    col_map, col_list = st.columns([2, 1])
                    
                    # עמודת המפה
                    with col_map:
                        st.markdown("### 🗺️ מפת תחנות")
                        m = folium.Map(location=loc, zoom_start=16)
                        
                        # סימון המיקום הנוכחי
                        folium.Marker(
                            loc,
                            popup="<b>המיקום שלי</b>",
                            icon=folium.Icon(color='red', icon='user', prefix='fa'),
                            tooltip="אני כאן"
                        ).add_to(m)
                        
                        # סימון התחנות
                        for station in stations:
                            popup_html = f"""
                            <div class='station-popup' style='width:250px'>
                                <h4 style='margin:0; color:#007bff'>🚏 {station['name']}</h4>
                                <hr style='margin:8px 0'>
                                <p style='font-size:13px'>{station['vicinity']}</p>
                                <p style='color:#666'>📏 מרחק: {station['distance']} מטר</p>
                                <a href='https://www.google.com/maps/dir/?api=1&destination={station['lat']},{station['lng']}' 
                                   target='_blank'>
                                    <button style='background:#4CAF50; color:white; border:none; 
                                                   padding:8px 16px; border-radius:5px; cursor:pointer'>
                                        🧭 נווט לתחנה
                                    </button>
                                </a>
                            </div>
                            """
                            
                            folium.Marker(
                                [station['lat'], station['lng']],
                                popup=folium.Popup(popup_html, max_width=300),
                                tooltip=f"{station['name']} ({station['distance']}מ')",
                                icon=folium.Icon(color='blue', icon='bus', prefix='fa')
                            ).add_to(m)
                        
                        plugins.LocateControl(auto_start=use_gps).add_to(m)
                        components.html(m._repr_html_(), height=600)
                    
                    # עמודת הרשימה
                    with col_list:
                        st.markdown("### 📋 רשימת תחנות")
                        st.caption(f"נמצאו {len(stations)} תחנות בקרבת מקום")
                        
                        for idx, station in enumerate(stations[:10]):  # מגביל ל-10 ראשונות
                            # כרטיס תחנה
                            st.markdown(f"""
                            <div class='station-item'>
                                <div class='station-name'>🚏 {station['name']}</div>
                                <div class='station-distance'>📏 {station['distance']} מטר</div>
                                <div style='font-size:12px; color:#999; margin-top:5px'>
                                    {station['vicinity'][:40]}...
                                </div>
                            </div>
                            """, unsafe_allow_html=True)
                            
                            # כפתור ניווט
                            if st.button(
                                "🧭 נווט",
                                key=f"nav_station_{idx}",
                                help=f"פתח ניווט ל-{station['name']}"
                            ):
                                st.success(f"פותח ניווט ל-{station['name']}")
                                # ניתן להוסיף כאן אינטגרציה עם הטאב הראשון
                    
                    st.success(f"✅ נמצאו {len(stations)} תחנות בטווח של 500 מטר")
                else:
                    st.warning("⚠️ לא נמצאו תחנות באזור זה")

st.markdown("---")
st.caption("🚍 SmartBus Ultimate | מופעל ע״י Google Maps Traffic Data + GTFS ישראל")
