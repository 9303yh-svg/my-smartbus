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
from datetime import datetime, timedelta, time as dt_time
import time
import json

# --- הגדרות ---
st.set_page_config(page_title="SmartBus Ultimate", page_icon="🚍", layout="wide")
DB_FILE = 'gtfs_israel.db'

# --- CSS ---
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
    .stButton>button { width: 100%; height: 50px; font-size: 18px; border-radius: 12px; }
    </style>
""", unsafe_allow_html=True)

# --- חיבור לגוגל ---
api_key = "AIzaSyAZOiy_DWHLNVipXZgSzFBC8N2eGasydwY"
gmaps = googlemaps.Client(key=api_key)

# --- פונקציות ---
@st.cache_resource(show_spinner=False)
def init_db():
    if os.path.exists(DB_FILE): 
        return True
    try:
        url = "https://gtfs.mot.gov.il/gtfsfiles/israel-public-transportation.zip"
        with st.spinner('📥 מוריד נתוני GTFS...'):
            r = requests.get(url, timeout=30)
            z = zipfile.ZipFile(io.BytesIO(r.content))
            conn = sqlite3.connect(DB_FILE)
            pd.read_csv(z.open('routes.txt'), usecols=['route_id','route_short_name','route_long_name']).to_sql('routes', conn, if_exists='replace', index=False)
            pd.read_csv(z.open('trips.txt'), usecols=['route_id','shape_id']).drop_duplicates('route_id').to_sql('trips', conn, if_exists='replace', index=False)
            pd.read_csv(z.open('shapes.txt')).iloc[::8].to_sql('shapes', conn, if_exists='replace', index=False)
            conn.close()
        return True
    except:
        return False

def get_route_shape(line_num):
    try:
        conn = sqlite3.connect(DB_FILE)
        routes = pd.read_sql_query(f"SELECT * FROM routes WHERE TRIM(route_short_name) = '{line_num.strip()}'", conn)
        if routes.empty:
            conn.close()
            return None, None
        route_id = routes.iloc[0]['route_id']
        route_desc = routes.iloc[0]['route_long_name']
        df = pd.read_sql_query(f"SELECT s.shape_pt_lat, s.shape_pt_lon FROM trips t JOIN shapes s ON t.shape_id = s.shape_id WHERE t.route_id = '{route_id}' ORDER BY s.shape_pt_sequence", conn)
        conn.close()
        if df.empty:
            return None, None
        return list(zip(df['shape_pt_lat'].values[::3], df['shape_pt_lon'].values[::3])), route_desc
    except:
        return None, None

def decode_polyline(s):
    points = []
    index, lat, lng = 0, 0, 0
    while index < len(s):
        result, shift = 0, 0
        while True:
            b = ord(s[index]) - 63
            index += 1
            result |= (b & 0x1f) << shift
            shift += 5
            if b < 0x20: break
        lat += ~(result >> 1) if result & 1 else result >> 1
        result, shift = 0, 0
        while True:
            b = ord(s[index]) - 63
            index += 1
            result |= (b & 0x1f) << shift
            shift += 5
            if b < 0x20: break
        lng += ~(result >> 1) if result & 1 else result >> 1
        points.append((lat / 1e5, lng / 1e5))
    return points

def get_upcoming_buses(station_name):
    return [
        {"line": "5", "destination": "תחנה מרכזית", "minutes": 2},
        {"line": "18", "destination": "רמת אביב", "minutes": 8},
        {"line": "61", "destination": "בת ים", "minutes": 12},
    ]

def get_multiple_routes(origin, destination, num_alt=3, dep_time=None, arr_time=None):
    try:
        params = {"mode": "transit", "transit_mode": ["bus", "subway", "train"], "language": "he", "alternatives": True, "region": "il"}
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
            normal_dur = leg.get('duration', {}).get('value', 0)
            
            if normal_dur > 0:
                ratio = dur_sec / normal_dur
                if ratio < 1.15:
                    t_level, t_text = "low", "תנועה קלה"
                elif ratio < 1.35:
                    t_level, t_text = "medium", "תנועה בינונית"
                else:
                    t_level, t_text = "high", "פקקים כבדים"
            else:
                t_level, t_text = "unknown", "לא ידוע"
            
            lines = []
            for step in leg['steps']:
                if step['travel_mode'] == 'TRANSIT':
                    lines.append(step.get('transit_details', {}).get('line', {}).get('short_name', 'N/A'))
            
            polyline_pts = []
            for step in leg['steps']:
                if 'polyline' in step:
                    polyline_pts.extend(decode_polyline(step['polyline']['points']))
            
            processed.append({
                'index': idx,
                'duration_seconds': dur_sec,
                'duration_text': dur_traffic.get('text', 'N/A'),
                'distance': leg['distance']['text'],
                'traffic_level': t_level,
                'traffic_text': t_text,
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
            lat1, lon1, lat2, lon2 = radians(lat), radians(lng), radians(s_lat), radians(s_lng)
            dlat, dlon = lat2 - lat1, lon2 - lon1
            a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
            c = 2 * atan2(sqrt(a), sqrt(1-a))
            distance = int(R * c)
            stations.append({'name': p.get('name', 'תחנה'), 'vicinity': p.get('vicinity', ''), 'lat': s_lat, 'lng': s_lng, 'distance': distance})
        stations.sort(key=lambda x: x['distance'])
        return stations
    except:
        return []

def get_user_location_from_browser():
    """מקבל מיקום אמיתי מהדפדפן"""
    # JavaScript שמריץ geolocation
    location_js = """
    <script>
    function getLocation() {
        if (navigator.geolocation) {
            navigator.geolocation.getCurrentPosition(function(position) {
                const data = {
                    lat: position.coords.latitude,
                    lng: position.coords.longitude
                };
                window.parent.postMessage({type: 'streamlit:setComponentValue', value: data}, '*');
            }, function(error) {
                window.parent.postMessage({type: 'streamlit:setComponentValue', value: null}, '*');
            });
        }
    }
    getLocation();
    </script>
    """
    return components.html(location_js, height=0)

# --- Session State ---
if 'nav_step' not in st.session_state: st.session_state.nav_step = 0
if 'nav_data' not in st.session_state: st.session_state.nav_data = None
if 'selected_route' not in st.session_state: st.session_state.selected_route = None
if 'routes_options' not in st.session_state: st.session_state.routes_options = []
if 'user_location' not in st.session_state: st.session_state.user_location = None

# --- ממשק ---
st.title("🚍 SmartBus Ultimate")

tab1, tab2, tab3 = st.tabs(["🚦 מסלולים", "🔢 קווים", "📍 תחנות חיות"])

# טאב 1: מסלולים
with tab1:
    st.subheader("🔍 חפש מסלול")
    
    with st.form("search"):
        c1, c2 = st.columns(2)
        with c1: 
            org = st.text_input("מאיפה?", "תחנה מרכזית תל אביב")
        with c2: 
            dst = st.text_input("לאן?", "עזריאלי תל אביב")
        
        st.markdown("#### ⏰ זמן נסיעה")
        time_opt = st.radio("", ["יציאה עכשיו", "יציאה בזמן מסוים", "הגעה בזמן מסוים"], horizontal=True)
        
        dep_time = arr_time = None
        
        if time_opt == "יציאה בזמן מסוים":
            st.markdown("📅 **בחר תאריך ושעה מדויקת:**")
            cd, ct = st.columns(2)
            with cd: 
                d_date = st.date_input("תאריך", datetime.now(), key="dep_date")
            with ct:
                # שעה ידנית - הקלדה חופשית
                hour_min = st.text_input("שעה (HH:MM)", datetime.now().strftime("%H:%M"), 
                                        help="לדוגמה: 14:37 או 08:05", key="dep_time_manual")
                try:
                    h, m = map(int, hour_min.split(':'))
                    d_time = dt_time(h, m)
                    dep_time = datetime.combine(d_date, d_time)
                    st.success(f"🚀 יציאה: {dep_time.strftime('%d/%m/%Y %H:%M')}")
                except:
                    st.error("❌ פורמט שגוי - השתמש ב-HH:MM (לדוגמה: 14:30)")
            
        elif time_opt == "הגעה בזמן מסוים":
            st.markdown("📅 **בחר תאריך ושעה מדויקת:**")
            cd, ct = st.columns(2)
            with cd: 
                a_date = st.date_input("תאריך", datetime.now(), key="arr_date")
            with ct:
                hour_min = st.text_input("שעה (HH:MM)", (datetime.now() + timedelta(hours=1)).strftime("%H:%M"),
                                        help="לדוגמה: 16:23 או 09:47", key="arr_time_manual")
                try:
                    h, m = map(int, hour_min.split(':'))
                    a_time = dt_time(h, m)
                    arr_time = datetime.combine(a_date, a_time)
                    st.success(f"🏁 הגעה: {arr_time.strftime('%d/%m/%Y %H:%M')}")
                except:
                    st.error("❌ פורמט שגוי - השתמש ב-HH:MM")
        
        num = st.slider("כמה אופציות?", 2, 5, 3)
        sub = st.form_submit_button("🚀 חפש", type="primary")
        
        if sub:
            with st.spinner('מחשב...'):
                routes = get_multiple_routes(org, dst, num, dep_time, arr_time)
                if routes:
                    st.session_state.routes_options = routes
                    st.success(f"✅ {len(routes)} מסלולים")
                    st.rerun()
                else:
                    st.error("❌ לא נמצאו מסלולים")
    
    if st.session_state.routes_options:
        st.markdown("---")
        for idx, r in enumerate(st.session_state.routes_options):
            is_fast = (idx == 0)
            card = "route-card route-fastest" if is_fast else "route-card"
            badge = "⚡ המהיר!" if is_fast else f"#{idx+1}"
            
            st.markdown(f"""
            <div class='{card}'>
                <div class='route-header'>{badge} <span class='traffic-indicator traffic-{r['traffic_level']}'></span></div>
                <div style='font-size:20px'>⏱️ {r['duration_text']} | 📏 {r['distance']}</div>
                <div style='font-size:16px'>🚀 {r['departure_time']} | 🏁 {r['arrival_time']}</div>
                <div class='route-badge'>🚦 {r['traffic_text']}</div>
                {''.join([f"<div class='route-badge'>🚌 {l}</div>" for l in r['lines']])}
            </div>
            """, unsafe_allow_html=True)
            
            c1, c2 = st.columns([3,1])
            with c1:
                if st.button(f"📍 מפה", key=f"s{idx}"):
                    st.session_state.selected_route = idx
                    st.rerun()
            with c2:
                if st.button(f"▶️", key=f"n{idx}", type="primary"):
                    st.session_state.nav_data = r['steps']
                    st.session_state.nav_step = 0
                    st.rerun()
        
        st.markdown("---")
        routes = st.session_state.routes_options
        center = routes[0]['polyline'][len(routes[0]['polyline'])//2] if routes[0]['polyline'] else [32.0853, 34.7818]
        
        m = folium.Map(location=center, zoom_start=13)
        folium.TileLayer(tiles='https://mt1.google.com/vt/lyrs=h@159000000,traffic&x={x}&y={y}&z={z}', attr='Traffic', name='פקקים', overlay=True).add_to(m)
        
        colors = ['#11998e', '#667eea', '#f093fb']
        show = routes if st.session_state.selected_route is None else [routes[st.session_state.selected_route]]
        
        for r in show:
            if r['polyline']:
                folium.PolyLine(r['polyline'], color=colors[r['index']%3], weight=6 if r['index']==0 else 4).add_to(m)
                if r['index'] == 0:
                    folium.Marker(r['polyline'][0], icon=folium.Icon(color='green', icon='play', prefix='fa')).add_to(m)
                    folium.Marker(r['polyline'][-1], icon=folium.Icon(color='red', icon='flag', prefix='fa')).add_to(m)
        
        folium.LayerControl().add_to(m)
        components.html(m._repr_html_(), height=600)
        
        if st.button("🔄 חדש"):
            st.session_state.routes_options = []
            st.rerun()

# טאב 2: קווים
with tab2:
    init_db()
    line = st.text_input("מספר קו:")
    if line and st.button("הצג"):
        pts, desc = get_route_shape(line)
        if pts:
            st.success(desc)
            m = folium.Map(location=pts[len(pts)//2], zoom_start=13)
            folium.PolyLine(pts, color="#9C27B0", weight=5).add_to(m)
            components.html(m._repr_html_(), height=500)
        else:
            st.warning(f"קו {line} לא נמצא")

# טאב 3: תחנות חיות - מפה אינטראקטיבית מלאה
with tab3:
    st.subheader("🗺️ תחנות חיות - מפה אינטראקטיבית")
    
    st.info("""
    💡 **הוראות שימוש:**
    - 🖱️ **לחץ בכל מקום על המפה** לחיפוש תחנות באזור
    - 🔍 **גרור את המפה** - תחנות יעודכנו אוטומטית
    - 🚏 **לחץ על תחנה** לפרטים מלאים + ניווט
    - 📍 **כפתור GPS** - למיקום המדויק שלך
    """)
    
    # JavaScript למפה אינטראקטיבית מלאה
    interactive_map_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
        <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
        <style>
            body {{ margin: 0; padding: 0; }}
            #map {{ width: 100%; height: 700px; }}
            .custom-popup {{
                direction: rtl;
                text-align: right;
                font-family: Arial;
            }}
        </style>
    </head>
    <body>
        <div id="map"></div>
        <script>
            var map = L.map('map').setView([32.0853, 34.7818], 14);
            
            // מפה + פקקים
            L.tileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={{x}}&y={{y}}&z={{z}}', {{
                attribution: 'Google Maps + Traffic',
                maxZoom: 20
            }}).addTo(map);
            
            var markers = [];
            var userMarker = null;
            
            // פונקציה לטעינת תחנות
            async function loadStations(lat, lng) {{
                // ניקוי סמנים ישנים
                markers.forEach(m => map.removeLayer(m));
                markers = [];
                
                try {{
                    const response = await fetch(
                        `https://maps.googleapis.com/maps/api/place/nearbysearch/json?location=${{lat}},${{lng}}&radius=600&type=transit_station&key={api_key}`,
                        {{ mode: 'no-cors' }}
                    );
                    
                    // סימולציה - בפועל צריך proxy server
                    // כרגע נציג תחנות דמה
                    const demoStations = [
                        {{name: "תחנה מרכזית", lat: lat + 0.002, lng: lng + 0.001}},
                        {{name: "דיזנגוף סנטר", lat: lat - 0.003, lng: lng + 0.002}},
                        {{name: "עזריאלי", lat: lat + 0.004, lng: lng - 0.001}},
                        {{name: "רוטשילד", lat: lat - 0.001, lng: lng + 0.003}},
                    ];
                    
                    demoStations.forEach(station => {{
                        const buses = [
                            {{line: "5", dest: "תחנה מרכזית", min: 2}},
                            {{line: "18", dest: "רמת אביב", min: 7}},
                            {{line: "61", dest: "בת ים", min: 12}}
                        ];
                        
                        let busesHtml = buses.map(b => 
                            `<div style='background:#4CAF50;color:white;padding:5px;border-radius:5px;margin:3px'>
                                🚌 ${{b.line}} → ${{b.dest}} (${{b.min}}')</div>`
                        ).join('');
                        
                        const popupContent = `
                            <div class="custom-popup" style="width:300px">
                                <h3 style="color:#007bff;margin:0 0 10px 0">🚏 ${{station.name}}</h3>
                                <p style="font-weight:bold">📏 מרחק: ~200 מטר</p>
                                <div style="background:#f0f8ff;padding:10px;border-radius:8px;margin:10px 0">
                                    <h4 style="margin:0 0 10px 0">🚌 קרובים:</h4>
                                    ${{busesHtml}}
                                </div>
                                <a href="https://www.google.com/maps/dir/?api=1&destination=${{station.lat}},${{station.lng}}" target="_blank">
                                    <button style="background:#4CAF50;color:white;border:none;padding:10px;width:100%;
                                                   border-radius:8px;font-weight:bold;cursor:pointer;margin-top:10px">
                                        🧭 נווט לתחנה
                                    </button>
                                </a>
                                <button onclick="navigator.clipboard.writeText('${{station.name}}')" 
                                        style="background:#2196F3;color:white;border:none;padding:8px;width:100%;
                                               border-radius:8px;cursor:pointer;margin-top:5px">
                                    📋 העתק שם תחנה
                                </button>
                            </div>
                        `;
                        
                        const marker = L.marker([station.lat, station.lng], {{
                            icon: L.icon({{
                                iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-2x-blue.png',
                                iconSize: [25, 41],
                                iconAnchor: [12, 41],
                                popupAnchor: [1, -34]
                            }})
                        }}).addTo(map);
                        
                        marker.bindPopup(popupContent, {{maxWidth: 320}});
                        markers.push(marker);
                    }});
                    
                }} catch(e) {{
                    console.log("טעינת תחנות:", e);
                }}
            }}
            
            // GPS - מיקום משתמש
            map.locate({{setView: false, maxZoom: 16}});
            
            map.on('locationfound', function(e) {{
                if (userMarker) map.removeLayer(userMarker);
                
                userMarker = L.marker(e.latlng, {{
                    icon: L.icon({{
                        iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-2x-red.png',
                        iconSize: [25, 41],
                        iconAnchor: [12, 41]
                    }})
                }}).addTo(map);
                
                userMarker.bindPopup('<b>📍 אני כאן!</b>').openPopup();
                map.setView(e.latlng, 16);
                loadStations(e.latlng.lat, e.latlng.lng);
            }});
            
            // לחיצה על המפה - חיפוש תחנות
            map.on('click', function(e) {{
                const lat = e.latlng.lat;
                const lng = e.latlng.lng;
                
                // סימון הנקודה שנלחצה
                if (userMarker) map.removeLayer(userMarker);
                userMarker = L.marker([lat, lng], {{
                    icon: L.icon({{
                        iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-2x-orange.png',
                        iconSize: [25, 41]
                    }})
                }}).addTo(map);
                
                userMarker.bindPopup(`
                    <div style="direction:rtl;text-align:right">
                        <b>📍 נקודה נבחרת</b><br>
                        <button onclick="window.open('https://www.google.com/maps/dir/?api=1&destination=${{lat}},${{lng}}', '_blank')"
                                style="background:#4CAF50;color:white;border:none;padding:8px 16px;
                                       border-radius:8px;cursor:pointer;margin-top:8px;width:100%">
                            🧭 נווט לכאן
                        </button>
                    </div>
                `).openPopup();
                
                loadStations(lat, lng);
            }});
            
            // גרירת המפה - עדכון תחנות
            var updateTimeout;
            map.on('moveend', function() {{
                clearTimeout(updateTimeout);
                updateTimeout = setTimeout(() => {{
                    const center = map.getCenter();
                    loadStations(center.lat, center.lng);
                }}, 1000);
            }});
            
            // טעינה ראשונית
            const initialCenter = map.getCenter();
            loadStations(initialCenter.lat, initialCenter.lng);
            
            // כפתור GPS
            L.Control.GPS = L.Control.extend({{
                onAdd: function(map) {{
                    var btn = L.DomUtil.create('button');
                    btn.innerHTML = '📍 המיקום שלי';
                    btn.style.background = 'white';
                    btn.style.padding = '10px 15px
