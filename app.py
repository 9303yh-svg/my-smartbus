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
import polyline
import time

# --- הגדרות מערכת ---
st.set_page_config(page_title="SmartBus Stable", page_icon="🚍", layout="wide")
DB_FILE = 'gtfs_israel.db'
ISRAEL_TZ = pytz.timezone('Asia/Jerusalem')

# --- התחברות לגוגל ---
try:
    # וודא שיש לך את המפתח ב-secrets.toml
    api_key = st.secrets["GOOGLE_API_KEY"]
    gmaps = googlemaps.Client(key=api_key)
except:
    st.error("⚠️ מפתח Google API חסר או לא תקין.")
    st.stop()

# --- פונקציות עזר ---

def format_hebrew_time(seconds):
    """ממיר שניות לטקסט קריא בעברית"""
    mins = int(seconds / 60)
    if mins < 60:
        return f"{mins} דקות"
    hours = int(mins / 60)
    rem_mins = mins % 60
    if rem_mins == 0:
        return f"{hours} שעות"
    return f"{hours} שעות ו-{rem_mins} דקות"

# --- מנוע הנתונים (SQL) ---
@st.cache_resource(show_spinner=False)
def init_database():
    if os.path.exists(DB_FILE): return True
    try:
        url = "https://gtfs.mot.gov.il/gtfsfiles/israel-public-transportation.zip"
        with st.spinner('📥 טוען נתונים ראשוניים...'):
            r = requests.get(url)
            z = zipfile.ZipFile(io.BytesIO(r.content))
            conn = sqlite3.connect(DB_FILE)
            
            # טעינת נתונים בסיסיים בלבד לביצועים
            pd.read_csv(z.open('routes.txt'), usecols=['route_id', 'route_short_name', 'route_long_name']).to_sql('routes', conn, if_exists='replace', index=False)
            trips = pd.read_csv(z.open('trips.txt'), usecols=['route_id', 'shape_id'])
            trips.drop_duplicates(subset=['route_id']).to_sql('trips', conn, if_exists='replace', index=False)
            
            # טעינת צורות בבלוקים
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
        # === דילול אגרסיבי למניעת קריסה ===
        # לוקח רק נקודה אחת מכל 20. מונע עומס על הדפדפן.
        return list(zip(df['shape_pt_lat'].values[::20], df['shape_pt_lon'].values[::20]))
    except: return []
    finally: conn.close()

# --- עיצוב CSS נקי ---
st.markdown("""
    <style>
    /* כיוון טקסט כללי */
    .element-container { direction: rtl; }
    
    /* כרטיסי מידע */
    .info-card {
        background-color: #f0f2f6;
        border-right: 5px solid #ff4b4b;
        padding: 15px;
        margin-bottom: 10px;
        border-radius: 8px;
        text-align: right;
    }
    
    /* ארנק */
    .wallet-card {
        background: linear-gradient(135deg, #00b09b, #96c93d);
        color: white; padding: 25px; border-radius: 15px; text-align: center;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
    }
    
    /* יישור כפתורי רדיו */
    div[role="radiogroup"] { direction: rtl; text-align: right; }
    </style>
""", unsafe_allow_html=True)

# --- האפליקציה ---
st.title("🚍 SmartBus Final")

# טאבים
tab_nav, tab_lines, tab_near, tab_pay = st.tabs(["🗺️ תכנון מסלול", "🔢 איתור קו", "📍 תחנות סביבי", "💳 ארנק"])

# ==========================================
# 1. תכנון מסלול (יציב ולא קורס)
# ==========================================
with tab_nav:
    with st.form("search_form"):
        c1, c2 = st.columns(2)
        with c1: org = st.text_input("מוצא", "המיקום שלי")
        with c2: dst = st.text_input("יעד", "עזריאלי תל אביב")
        
        # זמן
        t1, t2 = st.columns(2)
        with t1: time_opt = st.selectbox("זמן", ["יציאה עכשיו", "יציאה ב...", "הגעה עד..."])
        
        req_time = datetime.now()
        is_arr = False
        if time_opt != "יציאה עכשיו":
            with t2: 
                chosen_time = st.time_input("שעה", value=datetime.now().time())
                req_time = datetime.combine(datetime.now().date(), chosen_time)
                if "הגעה" in time_opt: is_arr = True

        submitted = st.form_submit_button("חפש מסלול 🚀")

    if submitted:
        with st.spinner('מחשב מסלול...'):
            try:
                # ברירת מחדל אם המשתמש כותב "המיקום שלי"
                # הערה: בגרסת ווב אמיתית צריך JS למיקום, כאן נשתמש בברירת מחדל לת"א אם לא ניתן לאתר
                real_org = "תחנה מרכזית תל אביב" if org == "המיקום שלי" else org
                
                params = {
                    "origin": real_org, "destination": dst,
                    "mode": "transit", "transit_mode": "bus",
                    "alternatives": True, "language": "he"
                }
                if is_arr: params["arrival_time"] = req_time
                else: params["departure_time"] = req_time
                
                res = gmaps.directions(**params)
                
                if res:
                    st.success(f"נמצאו {len(res)} מסלולים:")
                    
                    # הכנת אפשרויות לתצוגה
                    options = []
                    for i, r in enumerate(res):
                        leg = r['legs'][0]
                        
                        # פורמט זמן נקי
                        duration_text = format_hebrew_time(leg['duration']['value'])
                        
                        # בניית תקציר מסלול (למשל: הליכה > קו 5 > הליכה)
                        steps_summary = []
                        for s in leg['steps']:
                            if s['travel_mode'] == 'TRANSIT':
                                line = s['transit_details']['line']['short_name']
                                steps_summary.append(f"🚌 {line}")
                            elif s['travel_mode'] == 'WALKING':
                                steps_summary.append("🚶")
                        
                        # ניקוי כפילויות רצופות בתקציר
                        clean_summary = [x for n, x in enumerate(steps_summary) if n == 0 or x != steps_summary[n-1]]
                        summary_str = " ➔ ".join(clean_summary)
                        
                        label = f"אפשרות {i+1}: {duration_text} | {summary_str}"
                        options.append({"label": label, "data": r})
                    
                    # בחירת מסלול
                    selection = st.radio("בחר מסלול להצגה:", options, format_func=lambda x: x['label'])
                    
                    if selection:
                        r = selection['data']
                        leg = r['legs'][0]
                        
                        # מפה סטטית יציבה
                        m = folium.Map(location=[leg['start_location']['lat'], leg['start_location']['lng']], zoom_start=13)
                        folium.TileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}', attr='Traffic', overlay=True).add_to(m)
                        
                        # כפתור GPS
                        plugins.LocateControl().add_to(m)
                        
                        # ציור המסלול עם דילול נקודות (מונע קריסה!)
                        all_points = []
                        for step in leg['steps']:
                            # קבלת הנקודות המקוריות
                            pts = polyline.decode(step['polyline']['points'])
                            # דילול: לוקחים רק כל נקודה עשירית
                            thinned_pts = pts[::10]
                            all_points.extend(thinned_pts)
                            
                            color = "#800080" if step['travel_mode'] == 'TRANSIT' else "#0000FF"
                            weight = 5 if step['travel_mode'] == 'TRANSIT' else 3
                            dash = None if step['travel_mode'] == 'TRANSIT' else '5, 10'
                            
                            folium.PolyLine(thinned_pts, color=color, weight=weight, dash_array=dash, opacity=0.7).add_to(m)
                        
                        # התאמת זום למסלול
                        if all_points:
                            m.fit_bounds(all_points)
                        
                        # הצגת המפה
                        components.html(m._repr_html_(), height=400)
                        
                        # פירוט כתוב
                        with st.expander("📝 הוראות נסיעה מפורטות"):
                            for step in leg['steps']:
                                icon = "🚌" if step['travel_mode'] == 'TRANSIT' else "🚶"
                                st.markdown(f"<div style='direction:rtl; text-align:right;'>{icon} {step['html_instructions']}</div>", unsafe_allow_html=True)
                        
                        # כפתור לניווט חיצוני
                        nav_url = f"https://www.google.com/maps/dir/?api=1&origin={real_org}&destination={dst}&travelmode=transit"
                        st.markdown(f"[🔊 פתח ניווט קולי בגוגל מפות]({nav_url})")

                else:
                    st.warning("לא נמצא מסלול מתאים.")
            except Exception as e:
                st.error(f"שגיאה בחיפוש: {e}")

# ==========================================
# 2. איתור קו
# ==========================================
with tab_lines:
    if init_database():
        line_input = st.text_input("הזן מספר קו (למשל 1, 480):")
        if line_input:
            routes = get_routes_sql(line_input)
            if not routes.empty:
                # יצירת מילון לבחירה
                opts = {f"{row['route_long_name']}": row['route_id'] for idx, row in routes.iterrows()}
                selected_opt = st.selectbox("בחר כיוון:", list(opts.keys()))
                
                if st.button("הצג קו"):
                    shape_pts = get_shape_sql(opts[selected_opt])
                    if shape_pts:
                        mid_pt = shape_pts[len(shape_pts)//2]
                        m2 = folium.Map(location=mid_pt, zoom_start=12)
                        folium.TileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}', attr='Traffic', overlay=True).add_to(m2)
                        
                        # ציור הקו
                        folium.PolyLine(shape_pts, color="purple", weight=5).add_to(m2)
                        
                        components.html(m2._repr_html_(), height=450)
            else:
                st.warning("הקו לא נמצא במאגר.")

# ==========================================
# 3. תחנות סביבי (המתוקן והמהיר)
# ==========================================
with tab_near:
    st.info("🔎 מצא תחנות ברדיוס 300 מטר")
    
    col_input, col_btn = st.columns([3, 1])
    with col_input:
        search_addr = st.text_input("כתובת לחיפוש (או השאר ריק למיקום כללי):", "דיזנגוף סנטר תל אביב")
    with col_btn:
        st.write("") 
        st.write("") 
        btn_search = st.button("מצא סביבי")

    if btn_search:
        # מציאת מיקום
        center_loc = [32.0853, 34.7818] # ברירת מחדל (ת"א)
        
        if search_addr:
            geo_res = gmaps.geocode(search_addr)
            if geo_res:
                loc = geo_res[0]['geometry']['location']
                center_loc = [loc['lat'], loc['lng']]
            else:
                st.warning("לא מצאתי את הכתובת, מציג ברירת מחדל.")

        # יצירת מפה
        m3 = folium.Map(location=center_loc, zoom_start=16)
        folium.TileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}', attr='Traffic', overlay=True).add_to(m3)
        plugins.LocateControl(auto_start=True).add_to(m3)
        
        # סימון המרכז ורדיוס
        folium.Marker(center_loc, icon=folium.Icon(color='red', icon='user', prefix='fa'), tooltip="המיקום שלך").add_to(m3)
        folium.Circle(center_loc, radius=300, color='blue', fill=True, fill_opacity=0.1).add_to(m3)
        
        # חיפוש תחנות
        try:
            nearby = gmaps.places_nearby(location=(center_loc[0], center_loc[1]), radius=300, type='transit_station')
            
            count = 0
            for place in nearby.get('results', []):
                count += 1
                lat = place['geometry']['location']['lat']
                lng = place['geometry']['location']['lng']
                name = place['name']
                
                # יצירת Popup לחיץ ויפה בעברית
                popup_html = f"""
                <div style="font-family: Arial; text-align: right; direction: rtl; width: 150px;">
                    <b>🚏 {name}</b><br>
                    <span style="font-size: 12px; color: gray;">לחץ לפרטים</span>
                </div>
                """
                
                folium.Marker(
                    [lat, lng],
                    popup=folium.Popup(popup_html, max_width=200),
                    icon=folium.Icon(color='blue', icon='bus', prefix='fa')
                ).add_to(m3)
            
            st.success(f"נמצאו {count} תחנות ברדיוס 300 מטר.")
            components.html(m3._repr_html_(), height=500)
            
        except Exception as e:
            st.error(f"שגיאה בטעינת תחנות: {e}")

# ==========================================
# 4. ארנק
# ==========================================
with tab_pay:
    st.markdown("""
        <div class="wallet-card">
            <h1 style="margin:0;">₪ 45.90</h1>
            <p style="margin:0;">יתרה צבורה</p>
            <hr style="border:1px solid rgba(255,255,255,0.3);">
            <p>חוזה: חופשי חודשי (גוש דן)</p>
        </div>
    """, unsafe_allow_html=True)
    
    st.write("")
    if st.button("📷 סרוק ברקוד לתשלום", use_container_width=True):
        with st.spinner("מבצע אימות..."):
            time.sleep(1)
        st.balloons()
        st.success("✅ התשלום אושר בהצלחה!")
