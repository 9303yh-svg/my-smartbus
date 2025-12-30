import streamlit as st
import googlemaps
from datetime import datetime, timedelta
import pytz
import folium
import polyline
from streamlit_folium import st_folium # הרכיב האינטראקטיבי החדש

# --- 1. הגדרות והתחברות ---
try:
    api_key = st.secrets["GOOGLE_API_KEY"]
except:
    st.error("⚠️ חסר מפתח API. נא להגדיר ב-Secrets.")
    st.stop()

gmaps = googlemaps.Client(key=api_key)
ISRAEL_TZ = pytz.timezone('Asia/Jerusalem')

st.set_page_config(page_title="SmartBus Interactive", page_icon="🚍", layout="wide")

# --- 2. ניהול זיכרון (Session State) ---
# זה מה שמאפשר לאפליקציה "לזכור" על איזו תחנה לחצת
if 'selected_station' not in st.session_state:
    st.session_state.selected_station = None
if 'map_center' not in st.session_state:
    st.session_state.map_center = [32.0853, 34.7818] # תל אביב כברירת מחדל
if 'zoom_level' not in st.session_state:
    st.session_state.zoom_level = 13

# --- 3. סרגל צד ---
with st.sidebar:
    st.title("🚍 SmartBus 6.0")
    st.caption("מערכת אינטראקטיבית לניהול נסיעות")
    
    mode = st.radio("בחר מצב:", ["🗺️ חקור מפה ותחנות", "📍 תכנון מסלול (רגיל)"])
    
    st.divider()

    # מצב תכנון מסלול
    if mode == "📍 תכנון מסלול (רגיל)":
        origin = st.text_input("מוצא", "תחנה מרכזית נתניה")
        destination = st.text_input("יעד", "עזריאלי תל אביב")
        line_filter = st.text_input("סנן לפי קו (למשל 601)", "")
        
        time_option = st.selectbox("זמן:", ["עכשיו", "עתידי"])
        check_time = datetime.now(ISRAEL_TZ)
        if time_option == "עתידי":
            d = st.date_input("תאריך")
            t = st.time_input("שעה")
            check_time = ISRAEL_TZ.localize(datetime.combine(d, t))
            
        search_btn = st.button("הצג מסלול", type="primary")

    # מצב חקור מפה (סורק את האזור שלך)
    else:
        location_query = st.text_input("לאיזה אזור לקפוץ?", "דיזנגוף סנטר, תל אביב")
        if st.button("קפוץ לאזור 🚀"):
            geocode = gmaps.geocode(location_query)
            if geocode:
                loc = geocode[0]['geometry']['location']
                st.session_state.map_center = [loc['lat'], loc['lng']]
                st.session_state.zoom_level = 16
                st.rerun() # רענון כדי לעדכן את המפה

    st.divider()
    
    # --- פאנל פרטי תחנה (מופיע רק כשלוחצים על תחנה) ---
    if st.session_state.selected_station:
        st.success(f"🚏 תחנה נבחרת: {st.session_state.selected_station['name']}")
        st.markdown(f"**כתובת:** {st.session_state.selected_station.get('vicinity', 'לא ידוע')}")
        
        # כאן היינו מחברים API של משרד התחבורה לזמן אמת
        # כרגע נציג כפתור לניווט מהיר
        if st.button("נווט לתחנה זו 🏁"):
             # כאן אפשר להוסיף לוגיקה שתעביר את התחנה לשדה היעד
             st.info("הכתובת הועתקה ללוח (סימולציה)")

# --- 4. המפה והלוגיקה ---
st.subheader("מפה חיה 🗺️")

# הכנת המפה הבסיסית
m = folium.Map(location=st.session_state.map_center, zoom_start=st.session_state.zoom_level)

# הוספת שכבת פקקים
folium.TileLayer(
    'https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}',
    attr='Google Traffic',
    name='Traffic',
    overlay=True
).add_to(m)

# לוגיקה למצב "תכנון מסלול" - ציור קווים
if mode == "📍 תכנון מסלול (רגיל)" and 'search_btn' in locals() and search_btn:
    try:
        req_timestamp = int(check_time.timestamp())
        directions = gmaps.directions(
            origin, destination,
            mode="transit", transit_mode="bus",
            departure_time=req_timestamp, language='he'
        )
        if directions:
            leg = directions[0]['legs'][0]
            start_loc = leg['start_location']
            st.session_state.map_center = [start_loc['lat'], start_loc['lng']]
            
            # הצגת נתונים למעלה
            col1, col2 = st.columns(2)
            col1.metric("זמן", leg['duration']['text'])
            col2.metric("מרחק", leg['distance']['text'])

            # ציור המסלול
            for step in leg['steps']:
                points = polyline.decode(step['polyline']['points'])
                color = "blue"
                weight = 5
                tooltip = "הליכה/אחר"
                
                if step['travel_mode'] == 'TRANSIT':
                    line_name = step['transit_details']['line']['short_name']
                    
                    # סינון לפי קו (אם המשתמש ביקש קו ספציפי)
                    if line_filter and line_filter not in line_name:
                        color = "gray" # קו לא רלוונטי יהיה אפור
                        weight = 2
                        opacity = 0.3
                    else:
                        color = "red" # הקו שלנו
                        weight = 7
                        opacity = 0.8
                        tooltip = f"קו {line_name}"

                folium.PolyLine(points, color=color, weight=weight, opacity=0.8, tooltip=tooltip).add_to(m)
                
            # מרקרים
            folium.Marker([leg['start_location']['lat'], leg['start_location']['lng']], icon=folium.Icon(color='green')).add_to(m)
            folium.Marker([leg['end_location']['lat'], leg['end_location']['lng']], icon=folium.Icon(color='red')).add_to(m)

    except Exception as e:
        st.error(f"שגיאה בחיפוש: {e}")

# לוגיקה למצב "חקור מפה" - הצגת כל התחנות באזור
if mode == "🗺️ חקור מפה ותחנות":
    # מחפש תחנות סביב מרכז המפה הנוכחי
    lat, lng = st.session_state.map_center
    try:
        places = gmaps.places_nearby(location=(lat, lng), radius=500, type='transit_station')
        for p in places.get('results', []):
            loc = p['geometry']['location']
            
            # יצירת המרקר
            # שימו לב: אנחנו לא שמים Popup רגיל, אלא נותנים ל-Streamlit לתפוס את הלחיצה
            folium.Marker(
                [loc['lat'], loc['lng']],
                tooltip=p['name'],
                icon=folium.Icon(color='blue', icon='bus', prefix='fa')
            ).add_to(m)
            
            # שמירת מידע בזיכרון קטן כדי שנוכל לשלוף אותו בלחיצה (טריק מתקדם)
            # זה קצת מורכב למימוש מלא ללא Database, אז נסתמך על השם
            
    except Exception as e:
        pass

# --- 5. הצגת המפה האינטראקטיבית ---
# זה החלק הקריטי: הפקודה st_folium מחזירה מידע על איפה לחצת!
output = st_folium(m, width=1000, height=500)

# --- 6. עיבוד הלחיצה ---
if output['last_object_clicked']:
    clicked_lat = output['last_object_clicked']['lat']
    clicked_lng = output['last_object_clicked']['lng']
    
    # בודקים איזו תחנה נמצאת במיקום הזה (בקירוב)
    # זה טריק כי המפה לא מחזירה את שם התחנה ישירות, רק קואורדינטות
    # אז אנחנו עושים Reverse Geocoding קטן או מחפשים ברשימה שלנו
    
    # חיפוש זריז של מה יש בנקודה הזו
    # (בגרסה מלאה היינו משווים מול רשימת התחנות שטענו)
    st.session_state.selected_station = {
        "name": f"תחנה בנ.צ {clicked_lat:.4f}, {clicked_lng:.4f}",
        "vicinity": "לחץ שוב לפרטים נוספים (דרוש חיבור API מלא)"
    }
    
    # הערה: כדי לקבל את השם האמיתי בלחיצה, צריך להשתמש ב-FeatureGroup ולשמור ID
    # אבל זה מסבך מאוד את הקוד. כרגע זה מדגים את העקרון.
