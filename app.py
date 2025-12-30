import streamlit as st
import googlemaps
from datetime import datetime, timedelta
import pytz
import folium
import polyline
import streamlit.components.v1 as components

# --- הגדרת המפתח בצורה מאובטחת ---
try:
    api_key = st.secrets["GOOGLE_API_KEY"]
except:
    st.error("חסר מפתח API בהגדרות השרת")
    st.stop()

gmaps = googlemaps.Client(key=api_key)
ISRAEL_TZ = pytz.timezone('Asia/Jerusalem')

st.set_page_config(page_title="SmartBus Pro", page_icon="🚍", layout="wide")

# --- כותרת ---
st.title("🚍 SmartBus Pro")

# --- סרגל צד ראשי ---
with st.sidebar:
    st.header("מערכת שליטה")
    # בחירת מצב עבודה
    mode = st.radio("בחר פעולה:", ["🗺️ תכנון מסלול (רגיל)", "🕵️‍♂️ חוקר קווים ספציפי"])
    st.divider()

    # משתנים משותפים
    origin = st.text_input("מוצא", "תחנה מרכזית נתניה")
    destination = st.text_input("יעד", "עזריאלי תל אביב")
    
    # הגדרות זמן
    time_option = st.selectbox("זמן נסיעה", ["עכשיו", "בחר שעה עתידית"])
    check_time = datetime.now(ISRAEL_TZ)
    if time_option == "בחר שעה עתידית":
        d = st.date_input("תאריך", datetime.now().date())
        t = st.time_input("שעה", datetime.now().time())
        check_time = ISRAEL_TZ.localize(datetime.combine(d, t))

    # כפתור חיפוש
    btn_label = "חפש מסלול 🚀" if mode == "🗺️ תכנון מסלול (רגיל)" else "הצג מסלול קו 🕵️‍♂️"
    search_btn = st.button(btn_label, type="primary")

    st.divider()
    st.caption("פותח ע''י SmartBus AI")

# --- לוגיקה ראשית ---
if search_btn:
    # מצב א': תכנון מסלול רגיל (מה שיש לנו עד עכשיו)
    if mode == "🗺️ תכנון מסלול (רגיל)":
        st.subheader(f"מסלול מומלץ: {origin} ⬅️ {destination}")
        with st.spinner('מחשב מסלול אופטימלי...'):
            try:
                req_timestamp = int(check_time.timestamp())
                directions = gmaps.directions(
                    origin, destination,
                    mode="transit", transit_mode="bus",
                    departure_time=req_timestamp, language='he'
                )
                
                if not directions:
                    st.error("לא נמצא מסלול.")
                else:
                    leg = directions[0]['legs'][0]
                    
                    # נתונים
                    c1, c2, c3 = st.columns(3)
                    c1.metric("זמן כולל", leg['duration']['text'])
                    c2.metric("הגעה", leg['arrival_time']['text'])
                    c3.metric("מרחק", leg['distance']['text'])
                    
                    # מפה
                    start_loc = [leg['start_location']['lat'], leg['start_location']['lng']]
                    m = folium.Map(location=start_loc, zoom_start=13)
                    
                    # שכבת פקקים כללית
                    folium.TileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}', attr='Google Traffic', name='Traffic').add_to(m)

                    folium.Marker(start_loc, tooltip="מוצא", icon=folium.Icon(color='green', icon='play')).add_to(m)
                    folium.Marker([leg['end_location']['lat'], leg['end_location']['lng']], tooltip="יעד", icon=folium.Icon(color='red', icon='stop')).add_to(m)

                    # ציור
                    for step in leg['steps']:
                        points = polyline.decode(step['polyline']['points'])
                        color = "blue"
                        weight = 4
                        tooltip = "הליכה"
                        
                        if step['travel_mode'] == 'TRANSIT':
                            line_name = step['transit_details']['line']['short_name']
                            color = "black" # ברירת מחדל לאוטובוס
                            weight = 6
                            tooltip = f"קו {line_name}"
                            
                            # בדיקת פקקים ספציפית למקטע
                            try:
                                dept = step['transit_details']['departure_stop']['location']
                                arr = step['transit_details']['arrival_stop']['location']
                                dept_t = step['transit_details']['departure_time']['value']
                                
                                traf = gmaps.directions(f"{dept['lat']},{dept['lng']}", f"{arr['lat']},{arr['lng']}", mode="driving", departure_time=datetime.fromtimestamp(dept_t))
                                if traf:
                                    t_leg = traf[0]['legs'][0]
                                    norm = t_leg['duration']['value']
                                    act = t_leg.get('duration_in_traffic', {}).get('value', norm)
                                    delay = (act - norm) / 60
                                    
                                    if delay > 10: color = "red"; tooltip += f" (פקק כבד +{int(delay)} דק')"
                                    elif delay > 4: color = "orange"; tooltip += f" (עומס +{int(delay)} דק')"
                                    else: color = "green"; tooltip += " (פנוי)"
                            except: pass
                            
                        folium.PolyLine(points, color=color, weight=weight, opacity=0.8, tooltip=tooltip).add_to(m)

                    components.html(m._repr_html_(), height=500)
                    
                    with st.expander("פירוט שלבים"):
                         for step in leg['steps']:
                            st.write(f"{step['html_instructions']} ({step['duration']['text']})", unsafe_allow_html=True)

            except Exception as e:
                st.error(f"שגיאה: {e}")

    # מצב ב': חוקר הקווים (חדש!)
    elif mode == "🕵️‍♂️ חוקר קווים ספציפי":
        st.subheader("ניתוח מסלול של קו ספציפי")
        st.info("💡 במצב זה המערכת תחפש את הנתיב הטוב ביותר בין הנקודות ותציג את עומסי התנועה המדויקים עליו.")
        
        with st.spinner('מנתח את תוואי השטח והעומסים...'):
            try:
                req_timestamp = int(check_time.timestamp())
                # כאן אנחנו מבקשים מסלול נהיגה אבל על תוואי של תחבורה ציבורית כדי לראות את הפקק המדויק
                directions = gmaps.directions(
                    origin, destination,
                    mode="driving", # בודקים כרכב כדי לקבל מידע על פקקים
                    departure_time=req_timestamp,
                    language='he',
                    traffic_model="best_guess"
                )

                if directions:
                    leg = directions[0]['legs'][0]
                    
                    # חישוב עיכובים
                    normal_duration = leg['duration']['value']
                    traffic_duration = leg.get('duration_in_traffic', {}).get('value', normal_duration)
                    delay_minutes = (traffic_duration - normal_duration) / 60
                    
                    # הצגת נתונים בולטים
                    col1, col2 = st.columns(2)
                    col1.metric("זמן נסיעה משוער", leg['duration_in_traffic']['text'])
                    
                    status_color = "green"
                    status_text = "הדרך פנויה ✅"
                    if delay_minutes > 15:
                        status_color = "red"
                        status_text = f"פקק כבד (+{int(delay_minutes)} דק') 🔥"
                    elif delay_minutes > 5:
                        status_color = "orange"
                        status_text = f"עומס בינוני (+{int(delay_minutes)} דק') ⚠️"
                    
                    col2.markdown(f"### {status_text}")

                    # מפה
                    m = folium.Map(location=[leg['start_location']['lat'], leg['start_location']['lng']], zoom_start=14)
                    
                    # שכבת פקקים
                    folium.TileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}', attr='Google Traffic', name='Traffic').add_to(m)
                    
                    # ציור המסלול בצבע העומס
                    points = polyline.decode(directions[0]['overview_polyline']['points'])
                    folium.PolyLine(points, color=status_color, weight=8, opacity=0.7, tooltip=status_text).add_to(m)
                    
                    # מרקרים
                    folium.Marker([leg['start_location']['lat'], leg['start_location']['lng']], popup="התחלה", icon=folium.Icon(color='green')).add_to(m)
                    folium.Marker([leg['end_location']['lat'], leg['end_location']['lng']], popup="סוף", icon=folium.Icon(color='red')).add_to(m)

                    components.html(m._repr_html_(), height=500)
                else:
                    st.error("לא נמצא מסלול כביש בין הנקודות.")
            except Exception as e:
                st.error(f"שגיאה: {e}")
