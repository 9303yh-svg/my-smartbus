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

st.title("🚍 SmartBus Pro - המפה המלאה")
st.markdown("### מסלולים, תחנות ועומסי תנועה בזמן אמת")

# --- סרגל צד ---
with st.sidebar:
    st.header("לאן נוסעים?")
    origin = st.text_input("מוצא", "תחנה מרכזית נתניה")
    destination = st.text_input("יעד", "עזריאלי תל אביב")
    
    st.divider()
    st.subheader("הגדרות מפה")
    show_traffic_layer = st.checkbox("הצג שכבת פקקים (כמו Waze)", value=True)
    show_nearby_stops = st.checkbox("הצג תחנות אוטובוס באזור המוצא", value=False)
    
    st.divider()
    time_option = st.selectbox("זמן יציאה", ["עכשיו", "בחר שעה"])
    check_time = datetime.now(ISRAEL_TZ)
    
    if time_option == "בחר שעה":
        d = st.date_input("תאריך", datetime.now().date())
        t = st.time_input("שעה", datetime.now().time())
        check_time = ISRAEL_TZ.localize(datetime.combine(d, t))
    
    search_btn = st.button("הצג מפה 🚀", type="primary")

# --- הלוגיקה ---
if search_btn:
    with st.spinner('מנתח נתונים, סורק תחנות ועומסים...'):
        try:
            req_timestamp = int(check_time.timestamp())
            
            # 1. חיפוש מסלול
            directions = gmaps.directions(
                origin, destination,
                mode="transit", transit_mode="bus",
                departure_time=req_timestamp, language='he'
            )
            
            if not directions:
                st.error("לא נמצא מסלול.")
            else:
                leg = directions[0]['legs'][0]
                
                # מטריקות
                c1, c2, c3 = st.columns(3)
                c1.metric("⏱️ זמן כולל", leg['duration']['text'])
                c2.metric("🏁 שעת הגעה", leg['arrival_time']['text'])
                c3.metric("📏 מרחק", leg['distance']['text'])
                
                # --- בניית המפה ---
                start_lat = leg['start_location']['lat']
                start_lng = leg['start_location']['lng']
                m = folium.Map(location=[start_lat, start_lng], zoom_start=15)
                
                # 🚦 תוספת 1: שכבת פקקים של גוגל (Google Traffic Layer)
                if show_traffic_layer:
                    folium.TileLayer(
                        tiles='https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}',
                        attr='Google Traffic',
                        name='Traffic',
                        overlay=True,
                        control=True
                    ).add_to(m)

                # 🚏 תוספת 2: חיפוש תחנות קרובות (באמצעות Places API)
                if show_nearby_stops:
                    try:
                        places = gmaps.places_nearby(location=(start_lat, start_lng), radius=500, type='transit_station')
                        for place in places.get('results', []):
                            loc = place['geometry']['location']
                            name = place['name']
                            folium.Marker(
                                [loc['lat'], loc['lng']],
                                popup=name,
                                tooltip=f"🚏 {name}",
                                icon=folium.Icon(color='blue', icon='bus', prefix='fa')
                            ).add_to(m)
                    except Exception as e:
                        st.warning(f"לא ניתן לטעון תחנות קרובות (אולי ה-API לא מאופשר): {e}")

                # סימון מוצא ויעד
                folium.Marker([start_lat, start_lng], tooltip="מוצא", icon=folium.Icon(color='green', icon='play')).add_to(m)
                folium.Marker([leg['end_location']['lat'], leg['end_location']['lng']], tooltip="יעד", icon=folium.Icon(color='red', icon='stop')).add_to(m)

                # ציור המסלול
                for step in leg['steps']:
                    points = polyline.decode(step['polyline']['points'])
                    
                    if step['travel_mode'] == 'WALKING':
                        folium.PolyLine(points, color="#3388ff", weight=4, opacity=0.6, dash_array='5, 10', tooltip="הליכה").add_to(m)
                    
                    elif step['travel_mode'] == 'TRANSIT':
                        details = step['transit_details']
                        line_name = details['line']['short_name']
                        
                        # הוספת מרקר לכל תחנה שהאוטובוס עובר בה (אם קיים במידע)
                        dept_stop = details['departure_stop']
                        arr_stop = details['arrival_stop']
                        
                        # בדיקת פקקים על המסלול הספציפי
                        seg_start = f"{dept_stop['location']['lat']},{dept_stop['location']['lng']}"
                        seg_end = f"{arr_stop['location']['lat']},{arr_stop['location']['lng']}"
                        seg_time = datetime.fromtimestamp(details['departure_time']['value'])
                        
                        color = "green"
                        desc = "זורם"
                        
                        try:
                            traf = gmaps.directions(seg_start, seg_end, mode="driving", departure_time=seg_time)
                            if traf:
                                t_leg = traf[0]['legs'][0]
                                norm = t_leg['duration']['value']
                                act = t_leg.get('duration_in_traffic', {}).get('value', norm)
                                delay = (act - norm) / 60
                                
                                if delay > 12:
                                    color = "red"
                                    desc = f"עומס כבד (+{int(delay)} דק')"
                                elif delay > 5:
                                    color = "orange"
                                    desc = f"עומס (+{int(delay)} דק')"
                        except:
                            pass

                        folium.PolyLine(
                            points, 
                            color=color, 
                            weight=6, 
                            opacity=0.8, 
                            tooltip=f"קו {line_name}: {desc}"
                        ).add_to(m)

                # הצגת המפה
                map_html = m._repr_html_()
                components.html(map_html, height=600)
                
                with st.expander("📝 פירוט מסלול מלא"):
                    for step in leg['steps']:
                        instr = step['html_instructions']
                        dur = step['duration']['text']
                        st.write(f"- {dur}: {instr}", unsafe_allow_html=True)

        except Exception as e:
            st.error(f"שגיאה: {e}")
