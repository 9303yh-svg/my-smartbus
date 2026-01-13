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

st.set_page_config(page_title="SmartBus", page_icon="🚍", layout="wide")
DB_FILE = 'gtfs_israel.db'

st.markdown("""<style>
.route-card{background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);color:white;border-radius:15px;padding:20px;margin:15px 0;box-shadow:0 8px 16px rgba(0,0,0,0.2)}
.route-fastest{background:linear-gradient(135deg,#11998e 0%,#38ef7d 100%);border:3px solid gold}
.route-header{font-size:24px;font-weight:bold;margin-bottom:10px}
.route-badge{background:rgba(255,255,255,0.3);padding:5px 12px;border-radius:20px;display:inline-block;margin:5px;font-size:14px}
.traffic-low{background:#4CAF50;width:12px;height:12px;border-radius:50%;display:inline-block}
.traffic-medium{background:#FFC107;width:12px;height:12px;border-radius:50%;display:inline-block}
.traffic-high{background:#F44336;width:12px;height:12px;border-radius:50%;display:inline-block}
.station-item{background:white;border:1px solid #ddd;border-radius:8px;padding:12px;margin:8px 0;cursor:pointer;transition:all 0.2s}
.station-item:hover{box-shadow:0 4px 12px rgba(0,123,255,0.3);transform:scale(1.02)}
.bus-line-clickable{background:#2196F3;color:white;padding:6px 12px;border-radius:8px;margin:3px;display:inline-block;cursor:pointer;font-weight:bold}
.bus-line-clickable:hover{background:#1976D2;transform:scale(1.05)}
</style>""", unsafe_allow_html=True)

api_key = "AIzaSyAZOiy_DWHLNVipXZgSzFBC8N2eGasydwY"
gmaps = googlemaps.Client(key=api_key)

@st.cache_resource(show_spinner=False)
def init_db():
    if os.path.exists(DB_FILE): return True
    try:
        with st.spinner('מוריד GTFS...'):
            r = requests.get("https://gtfs.mot.gov.il/gtfsfiles/israel-public-transportation.zip", timeout=30)
            z = zipfile.ZipFile(io.BytesIO(r.content))
            conn = sqlite3.connect(DB_FILE)
            pd.read_csv(z.open('routes.txt'), usecols=['route_id','route_short_name','route_long_name','route_desc']).to_sql('routes', conn, if_exists='replace', index=False)
            pd.read_csv(z.open('trips.txt'), usecols=['route_id','shape_id','trip_headsign','direction_id']).to_sql('trips', conn, if_exists='replace', index=False)
            pd.read_csv(z.open('shapes.txt')).iloc[::5].to_sql('shapes', conn, if_exists='replace', index=False)
            stops = pd.read_csv(z.open('stops.txt'), usecols=['stop_id','stop_name','stop_lat','stop_lon'])
            stops.to_sql('stops', conn, if_exists='replace', index=False)
            stop_times = pd.read_csv(z.open('stop_times.txt'), usecols=['trip_id','stop_id','stop_sequence'])
            stop_times.to_sql('stop_times', conn, if_exists='replace', index=False)
            conn.close()
        return True
    except: return False

def get_route_details(line_num):
    try:
        conn = sqlite3.connect(DB_FILE)
        routes = pd.read_sql_query(f"SELECT * FROM routes WHERE TRIM(route_short_name)='{line_num.strip()}'", conn)
        if routes.empty:
            conn.close()
            return None
        
        route_id = routes.iloc[0]['route_id']
        route_name = routes.iloc[0]['route_long_name']
        
        trips = pd.read_sql_query(f"""
            SELECT DISTINCT direction_id, trip_headsign, shape_id, trip_id
            FROM trips 
            WHERE route_id='{route_id}' 
            ORDER BY direction_id
        """, conn)
        
        directions = []
        for _, trip in trips.head(2).iterrows():
            direction_id = trip['direction_id']
            headsign = trip['trip_headsign']
            shape_id = trip['shape_id']
            trip_id = trip['trip_id']
            
            shape_data = pd.read_sql_query(f"""
                SELECT shape_pt_lat, shape_pt_lon 
                FROM shapes 
                WHERE shape_id='{shape_id}' 
                ORDER BY shape_pt_sequence
            """, conn)
            
            polyline = list(zip(shape_data['shape_pt_lat'].values[::2], 
                              shape_data['shape_pt_lon'].values[::2])) if not shape_data.empty else []
            
            stations = pd.read_sql_query(f"""
                SELECT s.stop_name, s.stop_lat, s.stop_lon, st.stop_sequence
                FROM stop_times st
                JOIN stops s ON st.stop_id = s.stop_id
                WHERE st.trip_id = '{trip_id}'
                ORDER BY st.stop_sequence
            """, conn)
            
            stations_list = []
            for _, station in stations.iterrows():
                stations_list.append({
                    'name': station['stop_name'],
                    'lat': station['stop_lat'],
                    'lon': station['stop_lon'],
                    'sequence': station['stop_sequence']
                })
            
            directions.append({
                'direction_id': direction_id,
                'headsign': headsign,
                'polyline': polyline,
                'stations': stations_list
            })
        
        conn.close()
        
        return {
            'route_id': route_id,
            'route_name': route_name,
            'line_number': line_num,
            'directions': directions
        }
    except Exception as e:
        st.error(f"שגיאה: {str(e)}")
        return None

def find_nearest_station(user_lat, user_lon, stations):
    from math import radians, sin, cos, sqrt, atan2
    min_dist = float('inf')
    nearest = None
    for station in stations:
        R = 6371000
        lat1, lon1 = radians(user_lat), radians(user_lon)
        lat2, lon2 = radians(station['lat']), radians(station['lon'])
        a = sin((lat2-lat1)/2)**2 + cos(lat1)*cos(lat2)*sin((lon2-lon1)/2)**2
        dist = R * 2 * atan2(sqrt(a), sqrt(1-a))
        if dist < min_dist:
            min_dist = dist
            nearest = station.copy()
            nearest['distance'] = int(dist)
    return nearest

def decode_poly(s):
    pts, i, lat, lng = [], 0, 0, 0
    while i < len(s):
        r, sh = 0, 0
        while True:
            b = ord(s[i]) - 63
            i += 1
            r |= (b & 0x1f) << sh
            sh += 5
            if b < 0x20: break
        lat += ~(r >> 1) if r & 1 else r >> 1
        r, sh = 0, 0
        while True:
            b = ord(s[i]) - 63
            i += 1
            r |= (b & 0x1f) << sh
            sh += 5
            if b < 0x20: break
        lng += ~(r >> 1) if r & 1 else r >> 1
        pts.append((lat / 1e5, lng / 1e5))
    return pts

def get_routes(org, dst, n=3, dep=None, arr=None):
    try:
        p = {"mode":"transit","transit_mode":["bus","train"],"language":"he","alternatives":True,"region":"il"}
        if arr: p["arrival_time"] = arr
        elif dep: p["departure_time"] = dep
        else: p["departure_time"] = datetime.now()
        routes = gmaps.directions(org, dst, **p)
        if not routes: return []
        proc = []
        for i, route in enumerate(routes[:n]):
            leg = route['legs'][0]
            dur = leg.get('duration_in_traffic', leg.get('duration'))
            ds = dur.get('value', 0)
            norm = leg.get('duration',{}).get('value',0)
            if norm > 0:
                ratio = ds / norm
                tlvl, ttxt = ("low","קלה") if ratio<1.15 else ("medium","בינונית") if ratio<1.35 else ("high","כבדה")
            else: tlvl, ttxt = "low", "רגילה"
            lines = [step.get('transit_details',{}).get('line',{}).get('short_name','?') for step in leg['steps'] if step['travel_mode']=='TRANSIT']
            poly = []
            for step in leg['steps']:
                if 'polyline' in step: poly.extend(decode_poly(step['polyline']['points']))
            proc.append({
                'index':i,'duration_seconds':ds,'duration_text':dur.get('text','N/A'),
                'distance':leg['distance']['text'],'traffic_level':tlvl,'traffic_text':ttxt,
                'lines':lines,'steps':leg['steps'],'polyline':poly,
                'departure_time':leg.get('departure_time',{}).get('text','N/A'),
                'arrival_time':leg.get('arrival_time',{}).get('text','N/A')
            })
        proc.sort(key=lambda x:x['duration_seconds'])
        return proc
    except Exception as e:
        st.error(str(e))
        return []

def create_interactive_map(center):
    map_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
        <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
        <style>body{{margin:0;padding:0}}#map{{width:100%;height:700px}}</style>
    </head>
    <body>
        <div id="map"></div>
        <script>
            var map = L.map('map').setView([{center[0]}, {center[1]}], 15);
            L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{maxZoom:19}}).addTo(map);
            var trafficLayer = L.tileLayer('https://mt1.google.com/vt/lyrs=h@159000000,traffic&x={{x}}&y={{y}}&z={{z}}', {{maxZoom:20}}).addTo(map);
            var userMarker = L.marker([{center[0]}, {center[1]}], {{
                icon: L.icon({{iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-2x-red.png',iconSize: [25, 41]}})
            }}).addTo(map).bindPopup('<b>📍 אני כאן</b>');
            var stationMarkers = [];
            function loadStations(lat, lng) {{
                stationMarkers.forEach(m => map.removeLayer(m));
                stationMarkers = [];
                var demoStations = [
                    {{name:"דיזנגוף/פרישמן", lat:lat+0.003, lng:lng+0.002, buses:["5","18","61"]}},
                    {{name:"ארלוזורוב/אבן גבירול", lat:lat-0.004, lng:lng-0.001, buses:["4","1","89"]}},
                    {{name:"אלנבי/שנקר", lat:lat+0.002, lng:lng+0.004, buses:["18","10"]}},
                    {{name:"רוטשילד/הרצל", lat:lat-0.001, lng:lng-0.003, buses:["61","3"]}}
                ];
                demoStations.forEach(s => {{
                    var busesHtml = s.buses.map(b => 
                        `<div onclick='alert("עבור לטאב קווים וחפש קו " + b)' style='background:#2196F3;color:white;padding:6px 12px;border-radius:8px;margin:3px;display:inline-block;cursor:pointer;font-weight:bold'>🚌 קו ${{b}}</div>`
                    ).join('');
                    var popup = `<div style='width:280px;direction:rtl;font-family:Arial'>
                        <h3 style='color:#007bff;margin:5px 0;border-bottom:2px solid #007bff'>🚏 ${{s.name}}</h3>
                        <div style='background:#f0f8ff;padding:10px;border-radius:8px;margin:10px 0'>
                            <h4 style='margin:5px 0'>🚌 קווים בתחנה:</h4>
                            <div style='text-align:center'>${{busesHtml}}</div>
                            <small style='color:#666'>לחץ על קו → עבור לטאב "קווים + תחנות"</small>
                        </div>
                        <button onclick='alert("ניווט פנימי ל-${{s.name}}")' style='background:#4CAF50;color:white;border:none;padding:10px;width:100%;border-radius:8px;font-weight:bold;cursor:pointer'>🧭 נווט לתחנה באפליקציה</button>
                    </div>`;
                    var marker = L.marker([s.lat, s.lng], {{
                        icon: L.icon({{iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-2x-blue.png',iconSize: [25, 41]}})
                    }}).addTo(map);
                    marker.bindPopup(popup, {{maxWidth: 300}});
                    stationMarkers.push(marker);
                }});
            }}
            map.on('click', function(e) {{
                map.removeLayer(userMarker);
                userMarker = L.marker([e.latlng.lat, e.latlng.lng], {{
                    icon: L.icon({{iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-2x-orange.png',iconSize: [25, 41]}})
                }}).addTo(map).bindPopup('📍 נקודה נבחרה').openPopup();
                loadStations(e.latlng.lat, e.latlng.lng);
            }});
            loadStations({center[0]}, {center[1]});
        </script>
    </body>
    </html>
    """
    return map_html

if 'nav_step' not in st.session_state: st.session_state.nav_step=0
if 'nav_data' not in st.session_state: st.session_state.nav_data=None
if 'routes_options' not in st.session_state: st.session_state.routes_options=[]
if 'selected_route' not in st.session_state: st.session_state.selected_route=None
if 'selected_line' not in st.session_state: st.session_state.selected_line=None
if 'nav_destination' not in st.session_state: st.session_state.nav_destination=None

st.title("🚍 SmartBus Ultimate")
tab1, tab2, tab3 = st.tabs(["🚦 מסלולים","🔢 קווים + תחנות","📍 תחנות חיות"])

with tab1:
    st.subheader("חפש מסלול")
    with st.form("s"):
        c1,c2 = st.columns(2)
        with c1: org = st.text_input("מאיפה?","תחנה מרכזית תל אביב")
        with c2: dst = st.text_input("לאן?","עזריאלי תל אביב")
        opt = st.radio("זמן:",["עכשיו","יציאה","הגעה"],horizontal=True)
        dep = arr = None
        if opt == "יציאה":
            c1,c2 = st.columns(2)
            with c1: dd = st.date_input("תאריך",datetime.now())
            with c2: 
                tm = st.text_input("שעה HH:MM",datetime.now().strftime("%H:%M"))
                try:
                    h,m = map(int,tm.split(':'))
                    dep = datetime.combine(dd,dt_time(h,m))
                    st.success(f"🚀 {dep.strftime('%H:%M')}")
                except: st.error("שגוי")
        elif opt == "הגעה":
            c1,c2 = st.columns(2)
            with c1: ad = st.date_input("תאריך",datetime.now())
            with c2:
                tm = st.text_input("שעה HH:MM",(datetime.now()+timedelta(hours=1)).strftime("%H:%M"))
                try:
                    h,m = map(int,tm.split(':'))
                    arr = datetime.combine(ad,dt_time(h,m))
                    st.success(f"🏁 {arr.strftime('%H:%M')}")
                except: st.error("שגוי")
        num = st.slider("אופציות",2,5,3)
        if st.form_submit_button("🚀 חפש",type="primary"):
            with st.spinner('מחשב...'):
                routes = get_routes(org,dst,num,dep,arr)
                if routes:
                    st.session_state.routes_options = routes
                    st.success(f"✅ {len(routes)}")
                    st.rerun()
                else: st.error("לא נמצא")
    
    if st.session_state.routes_options:
        st.markdown("---")
        for i,r in enumerate(st.session_state.routes_options):
            fast = i==0
            st.markdown(f"""<div class='{"route-card route-fastest" if fast else "route-card"}'>
            <div class='route-header'>{"⚡ המהיר" if fast else f"#{i+1}"} <span class='traffic-{r["traffic_level"]}'></span></div>
            <div style='font-size:20px'>⏱️ {r['duration_text']} | 📏 {r['distance']}</div>
            <div style='font-size:16px'>🚀 {r['departure_time']} | 🏁 {r['arrival_time']}</div>
            <div class='route-badge'>🚦 {r['traffic_text']}</div>
            {''.join([f"<div class='route-badge'>🚌 {l}</div>" for l in r['lines']])}
            </div>""", unsafe_allow_html=True)
            c1,c2 = st.columns([3,1])
            with c1:
                if st.button(f"📍 מפה + פקקים במסלול בלבד",key=f"s{i}"): 
                    st.session_state.selected_route=i
                    st.rerun()
            with c2:
                if st.button(f"▶️",key=f"n{i}",type="primary"):
                    st.session_state.nav_data=r['steps']
                    st.rerun()
        
        if st.session_state.selected_route is not None:
            st.markdown("---")
            st.subheader("🗺️ פקקים על המסלול בלבד")
            r = st.session_state.routes_options[st.session_state.selected_route]
            if r['polyline']:
                center = r['polyline'][len(r['polyline'])//2]
                m = folium.Map(location=center,zoom_start=14)
                folium.PolyLine(r['polyline'],color='#11998e',weight=8,opacity=0.9).add_to(m)
                folium.Marker(r['polyline'][0],popup="מוצא",icon=folium.Icon(color='green',icon='play',prefix='fa')).add_to(m)
                folium.Marker(r['polyline'][-1],popup="יעד",icon=folium.Icon(color='red',icon='flag',prefix='fa')).add_to(m)
                bounds = [[min(p[0] for p in r['polyline']), min(p[1] for p in r['polyline'])],[max(p[0] for p in r['polyline']), max(p[1] for p in r['polyline'])]]
                m.fit_bounds(bounds, padding=[50,50])
                folium.TileLayer(tiles='https://mt1.google.com/vt/lyrs=h@159000000,traffic&x={x}&y={y}&z={z}',attr='Traffic',name='פקקים',overlay=True).add_to(m)
                components.html(m._repr_html_(),height=600)
                st.success("🚦 פקקים מוצגים רק על המסלול!")
                if st.button("◀️ חזור לרשימה"):
                    st.session_state.selected_route = None
                    st.rerun()

with tab2:
    init_db()
    st.subheader("🔍 חיפוש קו + תחנות")
    line = st.text_input("מספר קו:", placeholder="5, 18, 61")
    if line and st.button("🚌 הצג פרטי קו",type="primary"):
        with st.spinner('טוען...'):
            details = get_route_details(line)
            if details:
                st.session_state.selected_line = details
                st.success(f"✅ {details['route_name']}")
                st.rerun()
            else:
                st.warning(f"❌ קו {line} לא נמצא")
    
    if st.session_state.selected_line:
        details = st.session_state.selected_line
        st.markdown(f"### 🚌 קו {details['line_number']}: {details['route_name']}")
        if len(details['directions']) > 0:
            dir_options = [f"כיוון {i+1}: {d['headsign']}" for i,d in enumerate(details['directions'])]
            selected_dir_idx = st.radio("בחר כיוון:", range(len(dir_options)), format_func=lambda x: dir_options[x], horizontal=True)
            selected_dir = details['directions'][selected_dir_idx]
            st.markdown(f"#### 📍 {len(selected_dir['stations'])} תחנות")
            user_location = [32.0853, 34.7818]
            nearest = None
            if selected_dir['stations']:
                nearest = find_nearest_station(user_location[0], user_location[1], selected_dir['stations'])
                if nearest:
                    st.info(f"🎯 **קרוב אליך:** {nearest['name']} ({nearest['distance']}מ')")
            col_list, col_map = st.columns([1, 2])
            with col_list:
                st.markdown("##### 📋 תחנות:")
                for idx, station in enumerate(selected_dir['stations'][:20]):
                    is_nearest = nearest and station['name'] == nearest['name']
                    badge = "🎯" if is_nearest else f"{idx+1}."
                    st.markdown(f"""<div class='station-item' style='{"border:2px solid #4CAF50" if is_nearest else ""}'>
                        <b>{badge} {station['name']}</b></div>""", unsafe_allow_html=True)
                    if st.button(f"🧭 נווט", key=f"nav_st_{idx}"):
                        st.session_state.nav_destination = {'lat': station['lat'], 'lon': station['lon'], 'name': station['name']}
                        st.success(f"ניווט פנימי ל-{station['name']}")
            with col_map:
                st.markdown("##### 🗺️ מסלול:")
                if selected_dir['polyline']:
                    m = folium.Map(location=selected_dir['polyline'][len(selected_dir['polyline'])//2], zoom_start=13)
                    folium.PolyLine(selected_dir['polyline'], color="#9C27B0", weight=6).add_to(m)
                    for idx, station in enumerate(selected_dir['stations']):
                        is_nearest = nearest and station['name'] == nearest['name']
                        folium.CircleMarker([station['lat'], station['lon']],radius=8 if is_nearest else 5,
                            color='#4CAF50' if is_nearest else '#2196F3',fill=True,
                            popup=f"<b>{station['name']}</b><br>#{idx+1}",tooltip=station['name']).add_to(m)
                    components.html(m._repr_html_(), height=500)
        if st.button("🔄 חפש קו אחר"):
            st.session_state.selected_line = None
            st.rerun()

with tab3:
    st.subheader("🗺️ מפה אינטראקטיבית")
    st.info("💡 לחץ על תחנה → לחץ על קו → עבור לטאב 'קווים + תחנות' וחפש את הקו!")
    center = [32.0853, 34.7818]
    map_html = create_interactive_map(center)
    components.html(map_html, height=750)

st.caption("🚍 SmartBus Ultimate | Google Maps + GTFS")
