
import streamlit as st
import googlemaps
from datetime import datetime, timedelta
import pytz
import folium
import polyline
import streamlit.components.v1 as components

# הגדרת מפתח סודית
try:
    api_key = st.secrets["GOOGLE_API_KEY"]
except:
    st.error("Missing Google API Key in Secrets")
    st.stop()

gmaps = googlemaps.Client(key=api_key)
ISRAEL_TZ = pytz.timezone('Asia/Jerusalem')

st.set_page_config(page_title="SmartBus Israel", page_icon="🚌", layout="wide")
st.title("🚌 SmartBus Israel")

with st.sidebar:
    st.header("לאן נוסעים?")
    origin = st.text_input("מוצא", "תחנה מרכזית נתניה")
    destination = st.text_input("יעד", "עזריאלי תל אביב")
    time_option = st.selectbox("זמן יציאה", ["עכשיו", "בחר שעה"])
    check_time = datetime.now(ISRAEL_TZ)
    if time_option == "בחר שעה":
        d = st.date_input("תאריך", datetime.now().date())
        t = st.time_input("שעה", datetime.now().time())
        check_time = ISRAEL_TZ.localize(datetime.combine(d, t))
    search_btn = st.button("בדוק מסלול 🚀", type="primary")

if search_btn:
    with st.spinner('בודק עומסים...'):
        try:
            req_timestamp = int(check_time.timestamp())
            directions = gmaps.directions(origin, destination, mode="transit", transit_mode="bus", departure_time=req_timestamp, language='he')
            
            if not directions:
                st.error("לא נמצא מסלול.")
            else:
                leg = directions[0]['legs'][0]
                c1, c2, c3 = st.columns(3)
                c1.metric("זמן", leg['duration']['text'])
                c2.metric("הגעה", leg['arrival_time']['text'])
                c3.metric("מרחק", leg['distance']['text'])
                
                start_loc = [leg['start_location']['lat'], leg['start_location']['lng']]
                m = folium.Map(location=start_loc, zoom_start=13)
                folium.Marker(start_loc, icon=folium.Icon(color='green', icon='play'), tooltip="מוצא").add_to(m)
                folium.Marker([leg['end_location']['lat'], leg['end_location']['lng']], icon=folium.Icon(color='red', icon='stop'), tooltip="יעד").add_to(m)

                for step in leg['steps']:
                    points = polyline.decode(step['polyline']['points'])
                    if step['travel_mode'] == 'WALKING':
                        folium.PolyLine(points, color="blue", weight=3, opacity=0.5, dash_array='5, 10').add_to(m)
                    elif step['travel_mode'] == 'TRANSIT':
                        details = step['transit_details']
                        color = "green"
                        try:
                            seg_start = f"{details['departure_stop']['location']['lat']},{details['departure_stop']['location']['lng']}"
                            seg_end = f"{details['arrival_stop']['location']['lat']},{details['arrival_stop']['location']['lng']}"
                            seg_time = datetime.fromtimestamp(details['departure_time']['value'])
                            traf = gmaps.directions(seg_start, seg_end, mode="driving", departure_time=seg_time)
                            if traf:
                                dur = traf[0]['legs'][0]['duration']['value']
                                traf_dur = traf[0]['legs'][0].get('duration_in_traffic', {}).get('value', dur)
                                if (traf_dur - dur) / 60 > 5: color = "red"
                        except: pass
                        folium.PolyLine(points, color=color, weight=6, opacity=0.8).add_to(m)

                components.html(m._repr_html_(), height=500)
                with st.expander("פירוט"):
                    for step in leg['steps']: st.write(f"{step['html_instructions']} ({step['duration']['text']})", unsafe_allow_html=True)
        except Exception as e: st.error(f"Error: {e}")
