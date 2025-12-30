import streamlit as st
import googlemaps
from datetime import datetime
import pytz
import folium
import polyline
from streamlit_folium import st_folium

# --- הגדרות מערכת ---
try:
    api_key = st.secrets["GOOGLE_API_KEY"]
except:
    st.error("⚠️ הגדרת מפתח חסרה.")
    st.stop()

gmaps = googlemaps.Client(key=api_key)
ISRAEL_TZ = pytz.timezone('Asia/Jerusalem')

st.set_page_config(page_title="SmartBus App", page_icon="🚍", layout="centered", initial_sidebar_state="collapsed")

# --- עיצוב מותאם למובייל (CSS) ---
st.markdown("""
    <style>
    /* העלמת אלמנטים מיותרים של סטרימליט */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    
    /* כפתורים גדולים ללחיצה נוחה בטלפון */
    .stButton>button {
        width: 100%;
        height: 3em;
        border-radius: 12px;
        font-weight: bold;
        font-size: 18px;
    }
    
    /* עיצוב כרטיסי מידע */
    .metric-card {
        background-color: #f0f2f6;
        padding: 15px;
        border-radius: 10px;
        text-align: center;
        margin-bottom: 10px;
    }
    </style>
""", unsafe_allow_html=True)

# --- ניהול מצב (Session) ---
if 'map_center' not in st.session_state:
    st.session_state.map_center = [32.0853, 34.7818]
if 'zoom' not in st.session_state:
    st.session_state.zoom = 13

# --- כותרת ראשית ---
st.title("🚍 SmartBus")

# --- תפריט ניווט מהיר (Tabs) ---
tab1, tab2, tab3 = st.tabs(["🏠 מסלול", "🚌 קווים", "📍 סביבה"])

# === לשונית 1: תכנון מסלול ===
with tab1:
    col_a, col_b = st.columns(2)
    with col_a:
        origin = st.text_input("מוצא", "המיקום שלי", key="nav_origin")
    with col_b:
        dest = st.text_input("יעד", "עזריאלי תל אביב", key="nav_dest")
    
    if st.button("נווט עכשיו 🚀", key="btn_nav"):
        with st.spinner('מחשב מסלול...'):
            try:
                # טיפול ב"המיקום שלי" ידרוש בעתיד JS, כרגע נשתמש בברירת מחדל אם לא שינו
                actual_origin = "תחנה מרכזית נתניה" if origin == "המיקום שלי" else origin
                
                directions = gmaps.directions(
                    actual_origin, dest,
                    mode="transit", transit_mode="bus",
                    departure_time=datetime.now(), language='he'
                )
                
                if directions:
                    leg = directions[0]['legs'][0]
                    
                    # הצגת נתונים בכרטיס מעוצב
                    st.success(f"⏱️ זמן נסיעה: {leg['duration']['text']}")
                    st.info(f"🚍 יציאה מהתחנה: {leg['departure_time']['text']}")
                    
                    # מפה
                    start = leg['start_location']
                    m = folium.Map(location=[start['lat'], start['lng']], zoom_start=14)
                    folium.TileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}', attr='Traffic', name='Traffic', overlay=True).add_to(m)
                    
                    # ציור מסלול
                    points = polyline.decode(directions[0]['overview_polyline']['points'])
                    folium.PolyLine(points, color="blue", weight=6, opacity=0.7).add_to(m)
                    
                    # מרקרים
                    folium.Marker([start['lat'], start['lng']], icon=folium.Icon(color='green', icon='play')).add_to(m)
                    folium.Marker([leg['end_location']['lat'], leg['end_location']['lng']], icon=folium.Icon(color='red', icon='stop')).add_to(m)
                    
                    st_folium(m, height=400, width="100%")
                    
                    # פירוט שלבים
                    with st.expander("הוראות נסיעה מפורטות"):
                        for step in leg['steps']:
                            st.write(f"• {step['html_instructions']}", unsafe_allow_html=True)
                else:
                    st.error("לא נמצא מסלול")
            except Exception as e:
                st.error(f"שגיאה: {e}")

# === לשונית 2: חוקר קווים (מיני מוביט) ===
with tab2:
    st.info("🔎 איתור מסלול של קו ספציפי")
    line_num = st.text_input("מספר קו (למשל 910)", "")
    line_dir = st.text_input("נוסע אל...", "תל אביב")
    
    if st.button("הצג קו על המפה 🗺️", key="btn_line"):
        if line_num and line_dir:
            with st.spinner(f'מחפש את מסלול קו {line_num}...'):
                try:
                    # טריק: חיפוש מסלול כללי לכיוון היעד, וסינון התוצאות
                    # בגרסה מתקדמת נתחבר למשרד התחבורה. כרגע זה "מנחש" את הקו.
                    directions = gmaps.directions(
                        f"קו {line_num}", line_dir, # חיפוש חופשי
                        mode="transit", transit_mode="bus", language='he'
                    )
                    
                    if directions:
                        leg = directions[0]['legs'][0]
                        m = folium.Map(location=[leg['start_location']['lat'], leg['start_location']['lng']], zoom_start=12)
                        folium.TileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}', attr='Traffic', overlay=True).add_to(m)
                        
                        points = polyline.decode(directions[0]['overview_polyline']['points'])
                        folium.PolyLine(points, color="red", weight=6, opacity=0.8, tooltip=f"מסלול משוער קו {line_num}").add_to(m)
                        
                        st.success(f"נמצא מסלול לקו {line_num} לכיוון {line_dir}")
                        st_folium(m, height=400, width="100%")
                    else:
                        st.warning("לא נמצא מסלול מדויק לקו זה. נסה לציין עיר יעד.")
                except Exception as e:
                    st.error("שגיאה בחיפוש הקו")
        else:
            st.warning("נא להזין מספר קו ויעד")

# === לשונית 3: סורק סביבה ===
with tab3:
    st.caption("מה קורה סביבי עכשיו?")
    user_loc = st.text_input("איפה אתה?", "דיזנגוף סנטר", key="env_loc")
    
    if st.button("סרוק אזור 📡", key="btn_env"):
        geocode = gmaps.geocode(user_loc)
        if geocode:
            loc = geocode[0]['geometry']['location']
            m = folium.Map(location=[loc['lat'], loc['lng']], zoom_start=16)
            folium.TileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}', attr='Traffic', overlay=True).add_to(m)
            
            # אני
            folium.Marker([loc['lat'], loc['lng']], popup="אתה כאן", icon=folium.Icon(color='red', icon='user')).add_to(m)
            
            # תחנות
            places = gmaps.places_nearby(location=(loc['lat'], loc['lng']), radius=400, type='transit_station')
            for p in places.get('results', []):
                ploc = p['geometry']['location']
                folium.Marker(
                    [ploc['lat'], ploc['lng']],
                    tooltip=f"🚏 {p['name']}",
                    icon=folium.Icon(color='blue', icon='bus', prefix='fa')
                ).add_to(m)
                
            st_folium(m, height=400, width="100%")
            st.success(f"נמצאו {len(places.get('results', []))} תחנות באזור")
