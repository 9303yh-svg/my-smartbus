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
        transition: transform 0.2s;
    }
    .route-card:hover { transform: scale(1.02); }
    .route-fastest {
        background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%);
        border: 3px solid #FFD700;
    }
    .route-header { font-size: 24px; font-weight: bold; margin-bottom: 10px; }
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
        background: #ffffff;
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
    .station-item {
        background: white;
        border: 1px solid #ddd;
        border-radius: 8px;
        padding: 12px;
        margin: 8px 0;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    }
    .station-name { font-size: 16px; font-weight: bold; color: #007bff; }
    .bus-line-tag {
        display: inline-block;
        background: #4CAF50;
        color: white;
        padding: 4px 10px;
        border-radius: 12px;
        margin: 3px;
        font-weight: bold;
        font-size: 13px;
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
except:
    api_key = "AIzaSyAZOiy_DWHLNVipXZgSzFBC8N2eGasydwY"
    gmaps = googlemaps.Client(key=api_key)

# --- פונקציות עזר ---
@st.cache_resource(show_spinner=False)
def init_db():
    if os.path.exists(DB_FILE): 
        return True
    try:
        url = "https://gtfs.mot.gov.il/gtfsfiles/israel-public-transportation.zip"
        with st.spinner('📥 מוריד נתוני GTFS...'):
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
        st.error(f"שגיאה: {str(e)}")
        return False

def get_route_shape(line_num):
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
            
        points = list(zip(df['shape_pt_lat'].values[::3], df['shape_pt_lon'].values[::3]))
        return points, route_desc
    except:
        return None, None

def decode_polyline(polyline_str):
    points = []
    index, lat, lng = 0, 0, 0
    
    while index < len(polyline_str):
        result, shift = 0, 0
        while True:
            b = ord(polyline_str[index]) - 63
            index += 1
            result |= (b & 0x1f) << shift
            shift += 5
            if b < 0x20: break
        lat += ~(result >> 1) if result & 1 else result >> 1
        
        result, shift = 0, 0
        while True:
            b = ord(polyline_str[index]) - 63
            index += 1
            result |= (b & 0x1f) << shift
            shift += 5
            if b < 0x20: break
        lng += ~(result >> 1) if result & 1 else result >> 1
        
        points.append((lat / 1e5, lng / 1e5))
    return points

def get_upcoming_buses(station_name, lat, lng):
    """מחזיר קווים קרובים - דוגמה (צריך API של משרד התחבורה לזמן אמת)"""
    return [
        {"line": "5", "destination": "תחנה מרכזית", "minutes": 2},
        {"line": "18", "destination": "רמת אביב", "minutes": 8},
        {"line": "61", "destination": "בת ים", "minutes": 12},
        {"line": "4", "destination": "יפו", "minutes": 15},
    ]

def get_multiple_routes(origin, destination, num_alt=3, dep_time=None, arr_time=None):
    try:
        params = {
            "mode": "transit",
            "transit_mode": ["bus", "subway", "train", "tram"],
            "language": "he",
            "alternatives": True,
            "region": "il"
        }
        
        if arr_time:
            params["arrival_time"] = arr_time
        elif dep_time:
            params["departure_time"] = dep_time
        else:
            params["departure_time"] = datetime.now()
        
        routes = gmaps.directions(origin, destination, **params)
        if not routes:
            return []
        
        processed = []
        for idx, route in enumerate(routes[:num_alt]):
            leg = route['legs'][0]
            
            dur_traffic = leg.get('duration_in_traffic', leg.get('duration'))
            dur_sec = dur_traffic.get('value', 0)
            dur_text = dur_traffic.get('text', 'N/A')
            normal_dur = leg.get('duration', {}).get('value', 0)
            
            if normal_dur > 0:
                ratio = dur_sec / normal_dur
                if ratio < 1.15:
                    t_level, t_text, t_color = "low", "תנועה קלה", "#4CAF50"
                elif ratio < 1.35:
                    t_level, t_text, t_color = "medium", "תנועה בינונית", "#FFC107"
                else:
                    t_level, t_text, t_color = "high", "פקקים כבדים", "#F44336"
            else:
                t_level, t_text, t_color = "unknown", "לא ידוע", "#999"
            
            lines = []
            for step in leg['steps']:
                if step['travel_mode'] == 'TRANSIT':
                    line = step.get('transit_details', {}).get('line', {}).get('short_name', 'N/A')
                    lines.append(line)
            
            polyline_pts = []
            for step in leg['steps']:
                if 'polyline' in step:
                    polyline_pts.extend(decode_polyline(step['polyline']['points']))
            
            processed.append({
                'index': idx,
                'duration_seconds': dur_sec,
                'duration_text': dur_text,
                'distance': leg['distance']['text'],
                'traffic_level': t_level,
                'traffic_text': t_text,
                'traffic_color': t_color,
                'lines': lines,
                'steps': leg['steps'],
                'polyline': polyline_pts,
                'start_address': leg['start_address'],
                'end_address': leg['end_address'],
                'departure_time': leg.get('departure_time', {}).get('text', 'N/A'),
                'arrival_time': leg.get('arrival_time', {}).get('text', 'N/A')
            })
        
        processed.sort(key=lambda x: x['duration_seconds'])
        return processed
    except Exception as e:
        st.error(f"שגיאה: {str(e)}")
        return []

def get_nearby_stations(lat, lng, radius=500):
    try:
        places = gmaps.places_nearby(location=(lat, lng), radius=radius, type='transit_station')
        
        stations = []
        for p in places.get('results', []):
            s_lat = p['geometry']['location']['lat']
            s_lng = p['geometry']['location']['lng']
            
            from math import radians, sin, cos, sqrt, atan2
            R = 6371000
            lat1, lon1 = radians(lat), radians(lng)
            lat2, lon2 = radians(s_lat), radians(s_lng)
            dlat, dlon = lat2 - lat1, lon2 - lon1
            a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
            c = 2 * atan2(sqrt(a), sqrt(1-a))
            distance = int(R * c)
            
            stations.append({
                'name': p.get('name', 'תחנה'),
                'vicinity': p.get('vicinity', ''),
                'lat': s_lat,
                'lng': s_lng,
                'distance': distance
            })
        
        stations.sort(key=lambda x: x['distance'])
        return stations
    except:
        return []

# --- Session State ---
if 'nav_step' not in st.session_state: st.session_state.nav_step = 0
if 'nav_data' not in st.session_state: st.session_state.nav_data = None
if 'selected_route' not in st.session_state: st.session_state.selected_route = None
if 'routes_options' not in st.session_state: st.session_state.routes_options = []
if 'map_center' not in st.session_state: st.session_state.map_center = [32.0853, 34.7818]

# --- ממשק ---
st.title("🚍 SmartBus Ultimate - ניווט חכם עם פקקים")

tab1, tab2, tab3 = st.tabs(["🚦 מסלולים חכמים", "🔢 קווים", "📍 תחנות חיות"])

# ==================================================
# טאב 1: מסלולים
# ==================================================
with tab1:
    st.subheader("🔍 חפש את המסלול הכי מהיר")
    
    with st.form("smart_search"):
        c1, c2 = st.columns(2)
        with c1: 
            org = st.text_input("מאיפה?", "תחנה מרכזית תל אביב")
        with c2: 
            dst = st.text_input("לאן?", "עזריאלי תל אביב")
        
        st.markdown("#### ⏰ מתי לנסוע?")
        time_opt = st.radio("בחר:", ["יציאה עכשיו", "יציאה בזמן מסוים", "הגעה בזמן מסוים"], horizontal=True)
        
        dep_time = arr_time = None
        
        if time_opt == "יציאה בזמן מסוים":
            st.markdown("##### 📅 הזן תאריך ושעת יציאה:")
            col_d, col_t = st.columns(2)
            with col_d:
                d_date = st.date_input("תאריך", datetime.now())
            with col_t:
                d_time = st.time_input("שעה", datetime.now().time())
            dep_time = datetime.combine(d_date, d_time)
            st.info(f"🚀 יציאה: {dep_time.strftime('%d/%m/%Y %H:%M')}")
            
        elif time_opt == "הגעה בזמן מסוים":
            st.markdown("##### 📅 הזן תאריך ושעת הגעה:")
            col_d, col_t = st.columns(2)
            with col_d:
                a_date = st.date_input("תאריך", datetime.now())
            with col_t:
                a_time = st.time_input("שעה", (datetime.now() + timedelta(hours=1)).time())
            arr_time = datetime.combine(a_date, a_time)
            st.info(f"🏁 הגעה: {arr_time.strftime('%d/%m/%Y %H:%M')}")
        
        num_routes = st.slider("כמה אופציות?", 2, 5, 3)
        submitted = st.form_submit_button("🚀 חפש מסלולים", type="primary")
        
        if submitted:
            with st.spinner('מחשב...'):
                routes = get_multiple_routes(org, dst, num_routes, dep_time, arr_time)
                if routes:
                    st.session_state.routes_options = routes
                    st.success(f"✅ {len(routes)} מסלולים!")
                    time.sleep(0.3)
                    st.rerun()
                else:
                    st.error("❌ לא נמצאו מסלולים")
                    st.info("💡 בדוק כתיבה / שעות פעילות / הוסף עיר")
    
    if st.session_state.routes_options:
        st.markdown("---")
        st.subheader("📊 השוואת מסלולים")
        
        for idx, route in enumerate(st.session_state.routes_options):
            is_fast = (idx == 0)
            card = "route-card route-fastest" if is_fast else "route-card"
            badge = "⚡ המהיר ביותר!" if is_fast else f"אופציה {idx+1}"
            
            st.markdown(f"""
            <div class='{card}'>
                <div class='route-header'>{badge} <span class='traffic-indicator traffic-{route['traffic_level']}'></span></div>
                <div style='font-size:20px; margin:10px 0'>⏱️ <b>{route['duration_text']}</b> | 📏 {route['distance']}</div>
                <div style='font-size:16px; margin:10px 0'>🚀 {route['departure_time']} | 🏁 {route['arrival_time']}</div>
                <div class='route-badge'>🚦 {route['traffic_text']}</div>
                {''.join([f"<div class='route-badge'>🚌 {l}</div>" for l in route['lines']])}
            </div>
            """, unsafe_allow_html=True)
            
            c1, c2 = st.columns([3,1])
            with c1:
                if st.button(f"📍 הצג במפה", key=f"s_{idx}"):
                    st.session_state.selected_route = idx
                    st.rerun()
            with c2:
                if st.button(f"▶️ נווט", key=f"n_{idx}", type="primary"):
                    st.session_state.nav_data = route['steps']
                    st.session_state.nav_step = 0
                    st.rerun()
        
        st.markdown("---")
        st.subheader("🗺️ מפה + פקקים")
        
        routes = st.session_state.routes_options
        if routes and routes[0]['polyline']:
            center = routes[0]['polyline'][len(routes[0]['polyline'])//2]
        else:
            center = [32.0853, 34.7818]
        
        m = folium.Map(location=center, zoom_start=13)
        
        # שכבת פקקים
        folium.TileLayer(
            tiles='https://mt1.google.com/vt/lyrs=h@159000000,traffic&x={x}&y={y}&z={z}',
            attr='Google Traffic',
            name='פקקים',
            overlay=True,
            control=True
        ).add_to(m)
        
        colors = ['#11998e', '#667eea', '#f093fb', '#4facfe', '#fa709a']
        show_routes = routes if st.session_state.selected_route is None else [routes[st.session_state.selected_route]]
        
        for route in show_routes:
            idx = route['index']
            if route['polyline']:
                folium.PolyLine(
                    route['polyline'],
                    color=colors[idx % len(colors)],
                    weight=7 if idx==0 else 5,
                    opacity=0.9 if idx==0 else 0.7,
                    popup=f"מסלול {idx+1}"
                ).add_to(m)
                
                if idx == 0 or st.session_state.selected_route is not None:
                    folium.Marker(route['polyline'][0], popup="מוצא", 
                                 icon=folium.Icon(color='green', icon='play', prefix='fa')).add_to(m)
                    folium.Marker(route['polyline'][-1], popup="יעד",
                                 icon=folium.Icon(color='red', icon='flag-checkered', prefix='fa')).add_to(m)
        
        folium.LayerControl().add_to(m)
        components.html(m._repr_html_(), height=600)
        
        st.info("🚦 אדום=פקקים | צהוב=איטי | ירוק=זורם")
        
        if st.button("🔄 חדש"):
            st.session_state.routes_options = []
            st.rerun()
    
    if st.session_state.nav_data:
        st.markdown("---")
        st.subheader("🧭 ניווט")
        
        steps = st.session_state.nav_data
        idx = st.session_state.nav_step
        if idx >= len(steps): idx = len(steps) - 1
        
        current = steps[idx]
        
        c1, c2, c3 = st.columns([1,2,1])
        with c1:
            if idx > 0 and st.button("⬅️ הקודם"):
                st.session_state.nav_step -= 1
                st.rerun()
        with c2:
            st.markdown(f"<h3 style='text-align:center'>שלב {idx+1}/{len(steps)}</h3>", unsafe_allow_html=True)
        with c3:
            if idx < len(steps)-1:
                if st.button("הבא ➡️", type="primary"):
                    st.session_state.nav_step += 1
                    st.rerun()
            else:
                st.success("🎉 הגעת!")
        
        icon = "🚶" if current['travel_mode']=='WALKING' else "🚌"
        instr = current.get('html_instructions', 'המשך')
        dist = current.get('distance',{}).get('text','N/A')
        dur = current.get('duration',{}).get('text','N/A')
        
        st.markdown(f"""
        <div class="nav-card">
            <span class="big-icon">{icon}</span>
            <div>{instr}</div>
            <div style="color:#666; margin-top:10px">📏 {dist} | ⏱️ {dur}</div>
        </div>
        """, unsafe_allow_html=True)
        
        if current['travel_mode'] == 'TRANSIT':
            td = current.get('transit_details', {})
            line = td.get('line',{}).get('short_name','N/A')
            head = td.get('headsign','N/A')
            stops = td.get('num_stops',0)
            st.info(f"🚌 קו {line} → {head} | 🛑 {stops} תחנות")
        
        if st.button("❌ סיים"):
            st.session_state.nav_data = None
            st.rerun()

# ==================================================
# טאב 2: קווים
# ==================================================
with tab2:
    init_db()
    st.subheader("🔍 חפש קו")
    
    line = st.text_input("מספר קו:", placeholder="1, 480, 89")
    if line and st.button("הצג"):
        with st.spinner('טוען...'):
            pts, desc = get_route_shape(line)
            if pts:
                st.success(f"✅ {desc}")
                m = folium.Map(location=pts[len(pts)//2], zoom_start=13)
                folium.PolyLine(pts, color="#9C27B0", weight=5).add_to(m)
                folium.Marker(pts[0], popup="התחלה", icon=folium.Icon(color='green', icon='play', prefix='fa')).add_to(m)
                folium.Marker(pts[-1], popup="סוף", icon=folium.Icon(color='red', icon='stop', prefix='fa')).add_to(m)
                components.html(m._repr_html_(), height=500)
            else:
                st.warning(f"קו {line} לא נמצא")

# ==================================================
# טאב 3: תחנות חיות עם מפה דינמית
# ==================================================
with tab3:
    st.subheader("🗺️ תחנות חיות באזור")
    
    search_type = st.radio("חפש לפי:", ["📍 המיקום שלי", "🔍 כתובת"], horizontal=True)
    
    addr = None
    if search_type == "🔍 כתובת":
        addr = st.text_input("הכנס כתובת:", "דיזנגוף סנטר")
    
    if st.button("🔍 חפש תחנות", type="primary"):
        loc = None
        
        if search_type == "📍 המיקום שלי":
            st.info("🌍 משתמש במיקום ברירת מחדל (תל אביב)")
            loc = [32.0853, 34.7818]
        else:
            if addr:
                try:
                    geo = gmaps.geocode(addr)
                    if geo:
                        l = geo[0]['geometry']['location']
                        loc = [l['lat'], l['lng']]
                except:
                    st.error("לא נמצא")
        
        if loc:
            st.session_state.map_center = loc
            stations = get_nearby_stations(loc[0], loc[1], 500)
            
            col_map, col_list = st.columns([2, 1])
            
            with col_map:
                st.markdown("### 🗺️ מפה אינטראקטיבית")
                
                # מפה ממוקדת על המיקום
                m = folium.Map(location=loc, zoom_start=16)
                
                # שכבת פקקים
                folium.TileLayer(
                    tiles='https://mt1.google.com/vt/lyrs=h@159000000,traffic&x={x}&y={y}&z={z}',
                    attr='Google Traffic',
                    name='פקקים',
                    overlay=True,
                    control=True
                ).add_to(m)
                
                # סימון המיקום שלי
                folium.Marker(
                    loc,
                    popup="<b>אני כאן</b>",
                    icon=folium.Icon(color='red', icon='user', prefix='fa'),
                    tooltip="המיקום שלי"
                ).add_to(m)
                
                # תחנות עם פופאפ מפורט
                for station in stations:
                    buses = get_upcoming_buses(station['name'], station['lat'], station['lng'])
                    
                    buses_html = ""
                    for bus in buses:
                        buses_html += f'<div class="bus-line-tag">🚌 {bus["line"]} → {bus["destination"]} ({bus["minutes"]}\')</div><br>'
                    
                    popup_html = f"""
                    <div style='width:320px; font-family:Arial; direction:rtl; text-align:right'>
                        <h3 style='color:#007bff; border-bottom:2px solid #007bff; padding-bottom:5px'>
                            🚏 {station['name']}
                        </h3>
                        <p style='color:#666; font-size:13px'>{station['vicinity']}</p>
                        <p style='font-weight:bold'>📏 {station['distance']} מטר</p>
                        
                        <div style='background:#f0f8ff; padding:10px; border-radius:8px; margin:10px 0'>
                            <h4 style='margin:0 0 10px 0'>🚌 אוטובוסים קרובים:</h4>
                            {buses_html}
                        </div>
                        
                        <a href='https://www.google.com/maps/dir/?api=1&destination={station['lat']},{station['lng']}' 
                           target='_blank'>
                            <button style='background:#4CAF50; color:white; border:none; 
                                           padding:10px; width:100%; border-radius:8px; 
                                           font-size:14px; font-weight:bold; cursor:pointer; margin-top:10px'>
                                🧭 נווט לתחנה
                            </button>
                        </a>
                    </div>
                    """
                    
                    folium.Marker(
                        [station['lat'], station['lng']],
                        popup=folium.Popup(popup_html, max_width=350),
                        tooltip=f"{station['name']} ({station['distance']}מ')",
                        icon=folium.Icon(color='blue', icon='bus', prefix='fa')
                    ).add_to(m)
                
                # כפתורי שליטה
                plugins.LocateControl(auto_start=(search_type=="📍 המיקום שלי"), flyTo=True).add_to(m)
                plugins.MeasureControl(primary_length_unit='meters').add_to(m)
                folium.LayerControl().add_to(m)
                
                components.html(m._repr_html_(), height=600)
                
                st.info("""
                🎯 **המפה ממוקדת על המיקום שלך!**
                - 📍 סמן אדום = אתה
                - 🚏 סמנים כחולים = תחנות
                - לחץ על תחנה ל
