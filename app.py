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
.route-fastest{background:linear-gradient(135deg,#11998e 0%,#38ef7d 100%);border:3px solid #FFD700}
.route-header{font-size:24px;font-weight:bold;margin-bottom:10px}
.route-badge{background:rgba(255,255,255,0.3);padding:5px 12px;border-radius:20px;display:inline-block;margin:5px;font-size:14px}
.traffic-low{background:#4CAF50;width:12px;height:12px;border-radius:50%;display:inline-block}
.traffic-medium{background:#FFC107;width:12px;height:12px;border-radius:50%;display:inline-block}
.traffic-high{background:#F44336;width:12px;height:12px;border-radius:50%;display:inline-block}
.station-item{background:white;border:1px solid #ddd;border-radius:8px;padding:12px;margin:8px 0}
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
            pd.read_csv(z.open('routes.txt'), usecols=['route_id','route_short_name','route_long_name']).to_sql('routes', conn, if_exists='replace', index=False)
            pd.read_csv(z.open('trips.txt'), usecols=['route_id','shape_id']).drop_duplicates('route_id').to_sql('trips', conn, if_exists='replace', index=False)
            pd.read_csv(z.open('shapes.txt')).iloc[::8].to_sql('shapes', conn, if_exists='replace', index=False)
            conn.close()
        return True
    except: return False

def get_route_shape(ln):
    try:
        conn = sqlite3.connect(DB_FILE)
        r = pd.read_sql_query(f"SELECT * FROM routes WHERE TRIM(route_short_name)='{ln.strip()}'", conn)
        if r.empty: 
            conn.close()
            return None, None
        rid = r.iloc[0]['route_id']
        desc = r.iloc[0]['route_long_name']
        df = pd.read_sql_query(f"SELECT s.shape_pt_lat,s.shape_pt_lon FROM trips t JOIN shapes s ON t.shape_id=s.shape_id WHERE t.route_id='{rid}' ORDER BY s.shape_pt_sequence", conn)
        conn.close()
        if df.empty: return None, None
        return list(zip(df['shape_pt_lat'].values[::3], df['shape_pt_lon'].values[::3])), desc
    except: return None, None

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

def get_buses():
    return [{"line":"5","dest":"ת.מרכזית","min":2},{"line":"18","dest":"רמת אביב","min":8}]

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

def get_stations(lat, lng, rad=500):
    try:
        places = gmaps.places_nearby(location=(lat,lng), radius=rad, type='transit_station')
        stas = []
        for p in places.get('results',[]):
            slat = p['geometry']['location']['lat']
            slng = p['geometry']['location']['lng']
            from math import radians, sin, cos, sqrt, atan2
            R = 6371000
            a = sin(radians((slat-lat)/2))**2 + cos(radians(lat))*cos(radians(slat))*sin(radians((slng-lng)/2))**2
            dist = int(R * 2 * atan2(sqrt(a), sqrt(1-a)))
            stas.append({'name':p.get('name','תחנה'),'vicinity':p.get('vicinity',''),'lat':slat,'lng':slng,'distance':dist})
        stas.sort(key=lambda x:x['distance'])
        return stas
    except: return []

if 'nav_step' not in st.session_state: st.session_state.nav_step=0
if 'nav_data' not in st.session_state: st.session_state.nav_data=None
if 'routes_options' not in st.session_state: st.session_state.routes_options=[]
if 'selected_route' not in st.session_state: st.session_state.selected_route=None
if 'map_center' not in st.session_state: st.session_state.map_center=[32.0853,34.7818]

st.title("🚍 SmartBus Ultimate")
tab1, tab2, tab3 = st.tabs(["🚦 מסלולים","🔢 קווים","📍 תחנות"])

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
                if st.button(f"📍 מפה",key=f"s{i}"): 
                    st.session_state.selected_route=i
                    st.rerun()
            with c2:
                if st.button(f"▶️",key=f"n{i}",type="primary"):
                    st.session_state.nav_data=r['steps']
                    st.rerun()
        
        st.markdown("---")
        routes = st.session_state.routes_options
        center = routes[0]['polyline'][len(routes[0]['polyline'])//2] if routes[0]['polyline'] else [32.0853,34.7818]
        
        m = folium.Map(location=center,zoom_start=13)
        folium.TileLayer(tiles='https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}',attr='Google').add_to(m)
        
        colors = ['#11998e','#667eea','#f093fb']
        show = routes if st.session_state.selected_route is None else [routes[st.session_state.selected_route]]
        
        for r in show:
            if r['polyline']:
                folium.PolyLine(r['polyline'],color=colors[r['index']%3],weight=6 if r['index']==0 else 4).add_to(m)
                if r['index']==0:
                    folium.Marker(r['polyline'][0],icon=folium.Icon(color='green',icon='play',prefix='fa')).add_to(m)
                    folium.Marker(r['polyline'][-1],icon=folium.Icon(color='red',icon='flag',prefix='fa')).add_to(m)
        
        components.html(m._repr_html_(),height=600)
        if st.button("🔄 חדש"): 
            st.session_state.routes_options=[]
            st.rerun()

with tab2:
    init_db()
    line = st.text_input("מספר קו:")
    if line and st.button("הצג"):
        pts,desc = get_route_shape(line)
        if pts:
            st.success(desc)
            m = folium.Map(location=pts[len(pts)//2],zoom_start=13)
            folium.PolyLine(pts,color="#9C27B0",weight=5).add_to(m)
            components.html(m._repr_html_(),height=500)
        else: st.warning(f"לא נמצא")

with tab3:
    st.subheader("תחנות באזור")
    st.info("💡 גרור את המפה ולחץ 'חפש' שוב לעדכון!")
    
    opt = st.radio("",["📍 המיקום שלי","🔍 כתובת"],horizontal=True)
    addr = st.text_input("כתובת:","דיזנגוף סנטר") if opt=="🔍 כתובת" else None
    
    if st.button("🔍 חפש תחנות",type="primary"):
        loc = [32.0853,34.7818]
        
        if opt=="🔍 כתובת" and addr:
            try:
                geo = gmaps.geocode(addr)
                if geo: loc=[geo[0]['geometry']['location']['lat'],geo[0]['geometry']['location']['lng']]
            except: pass
        
        st.session_state.map_center = loc
        stations = get_stations(loc[0],loc[1],600)
        
        col_m,col_l = st.columns([2,1])
        
        with col_m:
            m = folium.Map(location=loc,zoom_start=16)
            folium.TileLayer(tiles='https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}',attr='Google').add_to(m)
            folium.Marker(loc,popup="אני כאן",icon=folium.Icon(color='red',icon='user',prefix='fa')).add_to(m)
            
            for s in stations:
                buses = get_buses()
                bhtml = ''.join([f"<div style='background:#4CAF50;color:white;padding:5px;border-radius:5px;margin:3px'>🚌 {b['line']} → {b['dest']} ({b['min']}')</div>" for b in buses])
                
                popup = f"""<div style='width:300px;direction:rtl'>
                <h3 style='color:#007bff'>🚏 {s['name']}</h3>
                <p>{s['vicinity']}</p><p><b>📏 {s['distance']}מ'</b></p>
                <div style='background:#f0f8ff;padding:10px;border-radius:8px'><h4>🚌 קרובים:</h4>{bhtml}</div>
                <a href='https://www.google.com/maps/dir/?api=1&destination={s['lat']},{s['lng']}' target='_blank'>
                <button style='background:#4CAF50;color:white;border:none;padding:10px;width:100%;border-radius:8px;margin-top:10px;cursor:pointer'>🧭 נווט</button></a>
                </div>"""
                
                folium.Marker([s['lat'],s['lng']],popup=folium.Popup(popup,max_width=320),
                             tooltip=f"{s['name']} ({s['distance']}מ')",
                             icon=folium.Icon(color='blue',icon='bus',prefix='fa')).add_to(m)
            
            plugins.LocateControl(auto_start=(opt=="📍 המיקום שלי")).add_to(m)
            components.html(m._repr_html_(),height=650)
        
        with col_l:
            st.markdown("### רשימה")
            st.caption(f"{len(stations)} תחנות")
            for idx,s in enumerate(stations[:15]):
                st.markdown(f"<div class='station-item'><b style='color:#007bff'>🚏 {s['name']}</b><br><span style='color:#666'>📏 {s['distance']}מ'</span></div>",unsafe_allow_html=True)

st.caption("🚍 SmartBus | Google Maps + GTFS")
