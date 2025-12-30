import streamlit as st
import pandas as pd
import folium
from folium import plugins # <--- הנה התיקון לשגיאה האדומה!
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
st.set_page_config(page_title="SmartBus Fixed", page_icon="🚍", layout="wide")
DB_FILE = 'gtfs_israel.db'
ISRAEL_TZ = pytz.timezone('Asia/Jerusalem')

# --- התחברות לגוגל ---
try:
    api_key = st.secrets["GOOGLE_API_KEY"]
    gmaps = googlemaps.Client(key=api_key)
except:
    st.error("⚠️ מפתח Google API חסר.")
    st.stop()

# --- מנוע ה-SQL ---
@st.cache_resource(show_spinner=False)
def init_database():
    if os.path.exists(DB_FILE): return True
    try:
        url = "https://gtfs.mot.gov.il/gtfsfiles/israel-public-transportation.zip"
        with st.spinner('📥 טוען נתונים...'):
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
        # === דיאטה רצחנית לקווים (למניעת קריסה) ===
        # לוקחים רק נקודה אחת מכל 20!
        return list(zip(df['shape_pt_lat'].values[::20], df['shape_pt_lon'].values[::20]))
    except: return []
    finally: conn.close()

# --- פונקציית סיכום מסלול ---
def get_route_summary(leg):
    s = []
    for step in leg['steps']:
        if step['travel_mode'] == 'TRANSIT':
            s.append("🚌 " + step['transit_details']['line']['short_name'])
        elif step['travel_mode'] == 'WALKING':
            s.append("🚶")
    # ניקוי כפילויות
    clean = []
    for x in s:
        if not clean or clean[-1] != x: clean.append(x)
    return " ➔ ".join(clean)

# --- עיצוב CSS (תיקון לתצוגה השבורה) ---
# ביטלתי את ה-RTL הגלובלי ששבר את האתר!
st.markdown("""
    <style>
    /* עיצוב כרטיסים */
    .info-box { background-color: #f0f8ff; padding: 10px; border-radius: 8px; margin-bottom: 5px; text-align: right; border-right: 4px solid #007bff; }
    .wallet-card { background: linear-gradient(135deg, #43cea2 0%, #185a9d 100%); color: white; padding: 20px; border-radius: 15px; text-align: center; }
    
    /* יישור לימין רק לטקסטים ספציפיים */
    .rtl-text { direction: rtl; text-align: right; }
    
    /* תיקון לכפתורים */
    .stButton>button { width: 100%; border-radius: 8px; }
    </style>
""", unsafe_allow_html=True)

# --- סרגל צד ---
with st.sidebar:
    st.header("⚙️ הגדרות")
    max_walking = st.slider("מקסימום הליכה (דקות):", 0, 30, 10)
    st.write(f"🚶 מוגדר: {max_walking} דקות")
    if st.button("🔄 אתחול מלא (Reset)"):
        st.session_state.clear()
        st.rerun()

st.title("🚍 SmartBus Fixed")

# טאבים
tab_nav, tab_lines, tab_stations, tab_wallet = st.tabs(["🗺️ תכנון", "🔢 קווים", "📍 סביבה", "💳 ארנק"])

# ==========================================
# 1. תכנון מסלול (יציב)
# ==========================================
with tab_nav:
    if 'live_mode' not in st.session_state: st.session_state.live_mode = False
    
    if st.session_state.live_mode:
        st.success("🟢 מצב ניווט פעיל")
        if st.button("❌ יציאה"):
            st.session_state.live_mode = False
            st.rerun()
            
        # כפתור גוגל
        u_orig = st.session_state.live_orig
        u_dest = st.session_state.live_dest
        link = f"https://www.google.com/maps/dir/?api=1&origin={u_orig}&destination={u_dest}&travelmode=transit"
        st.markdown(f'<a href="{link}" target="_blank"><button style="width:100%; padding:15px; background:#4285F4; color:white; border:none; border-radius:10px;">🔊 פתח ניווט קולי</button></a>', unsafe_allow_html=True)
        
        # הוראות טקסט בלבד (למניעת קריסה)
        st.subheader("הוראות:")
        for step in st.session_state.live_steps:
            icon = "🚌" if step['travel_mode'] == 'TRANSIT' else "🚶"
            txt = step['html_instructions']
            st.markdown(f"<div class='info-box' dir='rtl'>{icon} {txt}</div>", unsafe_allow_html=True)

    else:
        with st.form("nav"):
            c1, c2 = st.columns(2)
            with c1: org = st.text_input("מוצא", "המיקום שלי")
            with c2: dst = st.text_input("יעד", "עזריאלי תל אביב")
            submit = st.form_submit_button("חפש 🚀")
        
        if submit:
            with st.spinner('מחשב...'):
                try:
                    real_org = "תחנה מרכזית נתניה" if org == "המיקום שלי" else org
                    res = gmaps.directions(real_org, dst, mode="transit", transit_mode="bus", alternatives=True, language='he')
                    
                    if res:
                        opts = []
                        for i, r in enumerate(res):
                            leg = r['legs'][0]
                            dur = leg['duration']['text']
                            summ = get_route_summary(leg)
                            opts.append({"label": f"#{i+1}: {dur} | {summ}", "data": r})
                        
                        sel = st.radio("בחר מסלול:", opts, format_func=lambda x: x['label'])
                        
                        if sel:
                            r = sel['data']
                            leg = r['legs'][0]
                            
                            # כפתור מעבר ללייב
                            if st.button("🚶‍♂️ התחל ניווט"):
                                st.session_state.live_steps = leg['steps']
                                st.session_state.live_orig = real_org
                                st.session_state.live_dest = dst
                                st.session_state.live_mode = True
                                st.rerun()

                            # מפה סופר-קלה
                            m = folium.Map(location=[32.08, 34.78], zoom_start=12)
                            plugins.LocateControl().add_to(m)
                            folium.TileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}', attr='Traffic', overlay=True).add_to(m)
                            
                            # ציור מסלול עם דילול נקודות (כל נקודה 5)
                            all_pts = []
                            for step in leg['steps']:
                                pts = polyline.decode(step['polyline']['points'])
                                # דילול נקודות ציור!! קריטי לקריסות
                                thin_pts = pts[::5] 
                                all_pts.extend(thin_pts)
                                color = "#800080" if step['travel_mode'] == 'TRANSIT' else "blue"
                                folium.PolyLine(thin_pts, color=color, weight=5).add_to(m)
                            
                            if all_pts: m.fit_bounds(all_pts)
                            components.html(m._repr_html_(), height=400)
                    else: st.warning("לא נמצא מסלול")
                except Exception as e: st.error(f"שגיאה: {e}")

# ==========================================
# 2. איתור קו (השורת חיפוש חזרה!)
# ==========================================
with tab_lines:
    if init_database():
        # הנה השורה שהייתה חסרה!
        ln = st.text_input("הכנס מספר קו:", "")
        
        if ln:
            res = get_routes_sql(ln)
            if not res.empty:
                opts = {f"{r['route_long_name']}": r['route_id'] for i, r in res.iterrows()}
                s = st.selectbox("כיוון:", list(opts.keys()))
                if st.button("הצג"):
                    pts = get_shape_sql(opts[s])
                    if pts:
                        mid = pts[len(pts)//2]
                        m2 = folium.Map(location=mid, zoom_start=12)
                        folium.TileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}', attr='Traffic', overlay=True).add_to(m2)
                        folium.PolyLine(pts, color="purple", weight=5).add_to(m2)
                        components.html(m2._repr_html_(), height=400)
            else: st.warning("לא נמצא")

# ==========================================
# 3. תחנות סביבי (המתוקן)
# ==========================================
with tab_stations:
    col_me, col_search = st.columns([1,2])
    with col_search: q = st.text_input("כתובת:", "")
    with col_me: 
        st.write("")
        st.write("")
        me_btn = st.button("📍 סביבי")
    
    if me_btn or (q and st.button("חפש")):
        # מרכז ברירת מחדל (ת"א) כדי שלא יראה את כל העולם
        center = [32.0800, 34.7800] 
        
        if q and not me_btn:
            g = gmaps.geocode(q)
            if g: center = [g[0]['geometry']['location']['lat'], g[0]['geometry']['location']['lng']]
        
        m3 = folium.Map(location=center, zoom_start=15)
        # כפתור GPS קריטי
        plugins.LocateControl(auto_start=(True if me_btn else False)).add_to(m3)
        folium.TileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}', attr='Traffic', overlay=True).add_to(m3)
        
        if q and not me_btn:
             folium.Marker(center, icon=folium.Icon(color='red', icon='star')).add_to(m3)
             
        components.html(m3._repr_html_(), height=500)

# ==========================================
# 4. ארנק
# ==========================================
with tab_wallet:
    st.markdown("""<div class="wallet-card"><h1>₪ 45.90</h1><p>יתרה נוכחית</p></div>""", unsafe_allow_html=True)
    if st.button("📷 תשלום"):
        st.success("בוצע!")
        st.balloons()
