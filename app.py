import streamlit as st
import googlemaps
from datetime import datetime, timedelta
import pytz
import folium
import polyline
import streamlit.components.v1 as components

# --- 1. התחברות לגוגל ---
try:
    api_key = st.secrets["GOOGLE_API_KEY"]
except:
    st.error("⚠️ חסר מפתח API. נא להגדיר ב-Advanced Settings ב-Streamlit.")
    st.stop()

gmaps = googlemaps.Client(key=api_key)
ISRAEL_TZ = pytz.timezone('Asia/Jerusalem')

# --- 2. הגדרות עמוד ---
st.set_page_config(page_title="SmartBus Ultimate", page_icon="🚍", layout="wide")

# --- 3. סרגל צד (המוח של האפליקציה) ---
with st.sidebar:
    st.title("📱 SmartBus Menu")
    
    # בחירת מצב עבודה (כאן נמצא מה שחיפשת!)
    mode = st.radio(
        "מה תרצה לעשות?",
        ["📍 סורק סביבה (איפה אני?)", "🗺️ תכנון מסלול (רגיל)", "🕵️‍♂️ חוקר קווים (מתקדם)"]
    )
    
    st.divider()

    # הגדרות זמן ומיקום (משותף לכולם)
    if mode == "📍 סורק סביבה (איפה אני?)":
        origin = st.text_input("המיקום שלך (עיר/רחוב)", "תחנה מרכזית נתניה")
        st.info("💡 באפליקציית האנדרואיד המיקום יזוהה אוטומטית ע''י GPS.")
    
    elif mode == "🗺️ תכנון מסלול (רגיל)":
        origin = st.text_input("מוצא", "תחנה מרכזית נתניה")
        destination = st.text_input("יעד", "עזריאלי תל אביב")
        
    elif mode == "🕵️‍♂️ חוקר קווים (מתקדם)":
        origin = st.text_input("תחנת מוצא של הקו", "תחנה מרכזית נתניה")
        destination = st.text_input("תחנת סוף של הקו", "תל אביב סבידור")
        st.caption("הזן את מסלול הקו כדי לראות את הפקקים עליו")

    st.divider()
    
    # זמן
    time_option = st.selectbox("זמן:", ["עכשיו 🕒", "עתידי 📅"])
    check_time = datetime.now(ISRAEL_TZ)
    if "עתידי" in time_option:
        d = st.date_input("תאריך", datetime.now().date())
        t = st.time_input("שעה", datetime.now().time())
        check_time = ISRAEL_TZ.localize(datetime.combine(d, t))

    # כפתור הפעולה
    btn_text = "סרוק אזור 📡" if "סורק" in mode else "הצג מפה 🚀"
    search_btn = st.button(btn_text, type="primary")

# --- 4. לוגיקה ראשית ---
st.header(f"{mode}")

if search_btn:
    with st.spinner('🛰️ מתחבר ללוויינים ומעבד נתונים...'):
        try:
            req_timestamp = int(check_time.timestamp())
            m = None # המפה שתיווצר
            
            # ==========================================
            # מצב 1: סורק סביבה (הצגת תחנות ליד הבית)
            # ==========================================
            if "סורק" in mode:
                # 1. מוצאים את הקואורדינטות של המיקום
                geocode_result = gmaps.geocode(origin)
                if geocode_result:
                    loc = geocode_result[0]['geometry']['location']
                    lat, lng = loc['lat'], loc['lng']
                    
                    # בניית מפה מרוכזת במיקום
                    m = folium.Map(location=[lat, lng], zoom_start=16)
                    
                    # סימון "אני"
                    folium.Marker(
                        [lat, lng], 
                        popup="המיקום שלך", 
                        icon=folium.Icon(color='red', icon='user', prefix='fa')
                    ).add_to(m)
                    
                    # מעגל ברדיוס 500 מטר
                    folium.Circle([lat, lng], radius=500, color='blue', fill=True, fill_opacity=0.1).add_to(m)

                    # חיפוש תחנות אוטובוס קרובות
                    places = gmaps.places_nearby(location=(lat, lng), radius=500, type='transit_station')
                    
                    stations_found = 0
                    if 'results' in places:
                        for place in places['results']:
                            stations_found += 1
                            p_loc = place['geometry']['location']
                            name = place['name']
                            # אייקון של תחנה
                            folium.Marker(
                                [p_loc['lat'], p_loc['lng']],
                                tooltip=f"🚏 {name}",
                                icon=folium.Icon(color='blue', icon='bus', prefix='fa')
                            ).add_to(m)
                    
                    st.success(f"נמצאו {stations_found} תחנות ברדיוס של 500 מטר ממך.")
                else:
                    st.error("לא הצלחתי למצוא את המיקום שהזנת.")

            # ==========================================
            # מצב 2+3: מסלולים וחוקר קווים
            # ==========================================
            else:
                # בחוקר קווים אנחנו בודקים "נהיגה" כדי לראות פקקים נטו
                # בתכנון מסלול אנחנו בודקים "תחב''צ"
                travel_mode = "driving" if "חוקר" in mode else "transit"
                
                directions = gmaps.directions(
                    origin, destination,
                    mode=travel_mode,
                    transit_mode="bus" if travel_mode == "transit" else None,
                    departure_time=req_timestamp,
                    language='he',
                    traffic_model="best_guess" if travel_mode == "driving" else None
                )
                
                if directions:
                    leg = directions[0]['legs'][0]
                    
                    # נתונים
                    c1, c2, c3 = st.columns(3)
                    c1.metric("זמן משוער", leg['duration']['text'])
                    if 'duration_in_traffic' in leg:
                        c1.metric("זמן בפקקים", leg['duration_in_traffic']['text'], delta_color="inverse")
                    
                    c2.metric("מרחק", leg['distance']['text'])
                    c3.metric("יעד", destination)

                    # מפה
                    start_loc = [leg['start_location']['lat'], leg['start_location']['lng']]
                    m = folium.Map(location=start_loc, zoom_start=13)
                    
                    # ציור המסלול
                    points = polyline.decode(directions[0]['overview_polyline']['points'])
                    
                    route_color = "blue"
                    if "חוקר" in mode:
                        # צביעה לפי עומס (סימולציה לפי זמן)
                        norm = leg['duration']['value']
                        traffic = leg.get('duration_in_traffic', {}).get('value', norm)
                        delay = (traffic - norm) / 60
                        if delay > 15: route_color = "red"
                        elif delay > 5: route_color = "orange"
                        else: route_color = "green"
                    
                    folium.PolyLine(points, color=route_color, weight=6, opacity=0.8).add_to(m)
                    
                    # מרקרים
                    folium.Marker(start_loc, icon=folium.Icon(color='green', icon='play')).add_to(m)
                    folium.Marker([leg['end_location']['lat'], leg['end_location']['lng']], icon=folium.Icon(color='red', icon='stop')).add_to(m)

                else:
                    st.error("לא נמצא מסלול.")

            # ==========================================
            # הצגת המפה (משותף לכולם)
            # ==========================================
            if m:
                # שכבת פקקים חיה של גוגל (על הכל)
                folium.TileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}', attr='Google Traffic', name='Traffic Layer').add_to(m)
                
                # הצגה באפליקציה
                map_html = m._repr_html_()
                components.html(map_html, height=500)

        except Exception as e:
            st.error(f"שגיאה: {e}")
