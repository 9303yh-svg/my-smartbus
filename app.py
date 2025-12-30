import streamlit as st
import googlemaps
from datetime import datetime
import pytz
import folium
import polyline
from streamlit_folium import st_folium
from folium import plugins

# --- התחברות לגוגל ---
try:
    api_key = st.secrets["GOOGLE_API_KEY"]
except:
    st.error("⚠️ מפתח API חסר.")
    st.stop()

gmaps = googlemaps.Client(key=api_key)
ISRAEL_TZ = pytz.timezone('Asia/Jerusalem')

st.set_page_config(page_title="SmartBus Stable", page_icon="🚍", layout="centered", initial_sidebar_state="collapsed")

# --- עיצוב למניעת קריסות ושיפור מובייל ---
st.markdown("""
    <style>
    /* הסתרת תפריטים מיותרים */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    
    /* כפתורים יציבים */
    .stButton>button {
        width: 100%;
        height: 3em;
        border-radius: 12px;
        font-size: 18px;
        background-color: #FF4B4B;
        color: white;
    }
    
    /* תיקון לכיוון טקסט בטלפון */
    input {
        direction: rtl;
    }
    </style>
""", unsafe_allow_html=True)

st.title("🚍 SmartBus")

# --- לשוניות ---
tab1, tab2 = st.tabs(["🏠 מסלול וקווים", "📍 המיקום שלי"])

# === לשונית 1: חיפוש יציב (בתוך טופס) ===
with tab1:
    st.info("🔎 חפש מסלול או בדוק קו ספציפי")
    
    # שימוש ב-Form מונע ריענון אוטומטי וקריסות בטלפון!
    with st.form("route_form"):
        col1, col2 = st.columns(2)
        with col1:
            origin = st.text_input("מוצא", "תחנה מרכזית נתניה")
        with col2:
            destination = st.text_input("יעד", "עזריאלי תל אביב")
        
        # אופציה לסינון קו
        line_filter = st.text_input("סינון לפי קו (אופציונלי - למשל 910)", "")
        
        submitted = st.form_submit_button("חפש מסלול ופקקים 🚀")

    if submitted:
        if not origin or not destination:
            st.error("נא להזין מוצא ויעד")
        else:
            with st.spinner('מנתח מסלול...'):
                try:
                    # שלב א: חיפוש המסלול בתחבורה ציבורית
                    directions = gmaps.directions(
                        origin, destination,
                        mode="transit", transit_mode="bus",
                        departure_time=datetime.now(), language='he'
                    )

                    if directions:
                        leg = directions[0]['legs'][0]
                        start_loc = leg['start_location']
                        
                        # שלב ב: יצירת המפה
                        m = folium.Map(location=[start_loc['lat'], start_loc['lng']], zoom_start=13)
                        
                        # תוספת קריטית: כפתור GPS למיקום בזמן אמת
                        plugins.LocateControl(auto_start=False, strings={"title": "הצג את המיקום שלי"}).add_to(m)
                        
                        # שכבת פקקים
                        folium.TileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}', attr='Google Traffic', name='Traffic', overlay=True).add_to(m)

                        # שלב ג: ציור המסלול + טיפול בקו המבוקש
                        found_specific_line = False
                        
                        for step in leg['steps']:
                            points = polyline.decode(step['polyline']['points'])
                            color = "gray"
                            weight = 4
                            opacity = 0.5
                            tooltip = "הליכה/אחר"
                            
                            if step['travel_mode'] == 'TRANSIT':
                                line_name = step['transit_details']['line']['short_name']
                                headsign = step['transit_details']['headsign']
                                
                                # אם המשתמש ביקש קו ספציפי, נבדוק אם זה הקו הזה
                                is_target_line = (line_filter in line_name) if line_filter else True
                                
                                if is_target_line:
                                    if line_filter: found_specific_line = True
                                    color = "blue" # ברירת מחדל
                                    weight = 6
                                    opacity = 0.8
                                    tooltip = f"קו {line_name} לכיוון {headsign}"
                                    
                                    # בדיקת פקקים (רק לקווים הרלוונטיים)
                                    try:
                                        dept = step['transit_details']['departure_stop']['location']
                                        arr = step['transit_details']['arrival_stop']['location']
                                        dept_time = step['transit_details']['departure_time']['value']
                                        
                                        # בדיקת "רכב" על המסלול הזה
                                        traf_chk = gmaps.directions(
                                            f"{dept['lat']},{dept['lng']}",
                                            f"{arr['lat']},{arr['lng']}",
                                            mode="driving",
                                            departure_time=datetime.fromtimestamp(dept_time)
                                        )
                                        if traf_chk:
                                            t_dur = traf_chk[0]['legs'][0].get('duration_in_traffic', {}).get('value', 0)
                                            n_dur = traf_chk[0]['legs'][0]['duration']['value']
                                            delay = (t_dur - n_dur) / 60
                                            
                                            if delay > 10: 
                                                color = "red"
                                                tooltip += f" (פקק כבד +{int(delay)} דק')"
                                            elif delay > 3: 
                                                color = "orange"
                                                tooltip += f" (עומס +{int(delay)} דק')"
                                            else:
                                                color = "green"
                                                tooltip += " (פנוי)"
                                    except:
                                        pass

                            folium.PolyLine(points, color=color, weight=weight, opacity=opacity, tooltip=tooltip).add_to(m)

                        # הצגת תוצאות
                        if line_filter and not found_specific_line:
                            st.warning(f"המסלול נמצא, אך קו {line_filter} אינו חלק מהדרך המהירה ביותר כרגע. מוצג המסלול האופטימלי.")
                        else:
                            st.success(f"נמצא מסלול: {leg['duration']['text']}")

                        # אייקונים
                        folium.Marker([start_loc['lat'], start_loc['lng']], popup="מוצא", icon=folium.Icon(color='green', icon='play')).add_to(m)
                        folium.Marker([leg['end_location']['lat'], leg['end_location']['lng']], popup="יעד", icon=folium.Icon(color='red', icon='stop')).add_to(m)

                        st_folium(m, height=400, width="100%")
                        
                        with st.expander("פירוט מלא של המסלול"):
                            for step in leg['steps']:
                                st.write(step['html_instructions'], unsafe_allow_html=True)

                    else:
                        st.error("לא נמצא מסלול בין היעדים.")
                except Exception as e:
                    st.error(f"שגיאה: {e}")

# === לשונית 2: המיקום שלי (GPS) ===
with tab2:
    st.info("📡 לחץ על הכפתור השחור במפה כדי להתמקד במיקום שלך")
    
    if st.button("טען מפת סביבה"):
        # ברירת מחדל (מרכז הארץ), המשתמש ילחץ על GPS
        m_loc = folium.Map(location=[32.08, 34.78], zoom_start=12)
        
        # כפתור GPS
        plugins.LocateControl(auto_start=True).add_to(m_loc)
        
        # שכבת פקקים
        folium.TileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}', attr='Traffic', overlay=True).add_to(m_loc)
        
        st_folium(m_loc, height=500, width="100%")
