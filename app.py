import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
import requests
import zipfile
import io
import sqlite3
import os

# --- הגדרות ---
st.set_page_config(page_title="SmartBus SQL", page_icon="💾", layout="wide")
DB_FILE = 'gtfs_israel.db'

# --- שלב 1: פונקציות המנוע (בניית מסד הנתונים) ---
@st.cache_resource(show_spinner=False)
def init_database():
    """
    בודק אם קיים קובץ מסד נתונים.
    אם לא - מוריד את ה-ZIP הממשלתי, וממיר אותו ל-SQL מקומי.
    """
    if os.path.exists(DB_FILE):
        return True # המסד כבר קיים, אפשר להתקדם

    status_text = st.empty()
    progress_bar = st.progress(0)
    
    try:
        # 1. הורדה
        status_text.text("📥 מוריד את מאגר משרד התחבורה (פעם ראשונה בלבד)...")
        url = "https://gtfs.mot.gov.il/gtfsfiles/israel-public-transportation.zip"
        r = requests.get(url)
        z = zipfile.ZipFile(io.BytesIO(r.content))
        progress_bar.progress(30)
        
        # 2. פתיחת חיבור ל-SQL
        conn = sqlite3.connect(DB_FILE)
        
        # 3. המרת הקבצים לטבלאות (רק מה שחשוב)
        # טוענים Routes (קווים)
        status_text.text("⚙️ בונה אינדקס קווים...")
        routes = pd.read_csv(z.open('routes.txt'), usecols=['route_id', 'route_short_name', 'route_long_name'])
        routes.to_sql('routes', conn, if_exists='replace', index=False)
        progress_bar.progress(50)
        
        # טוענים Trips (נסיעות - כדי לקשר בין קו למפה)
        status_text.text("⚙️ מקשר נסיעות...")
        trips = pd.read_csv(z.open('trips.txt'), usecols=['route_id', 'shape_id'])
        # שמירת נסיעה אחת לדוגמה לכל קו (חוסך המון מקום)
        trips = trips.drop_duplicates(subset=['route_id'])
        trips.to_sql('trips', conn, if_exists='replace', index=False)
        progress_bar.progress(70)
        
        # טוענים Shapes (הציור על המפה - החלק הכבד)
        status_text.text("⚙️ מסרטט מפות (זה לוקח רגע)...")
        # קוראים בבלוקים כדי לא לקרוס
        chunksize = 100000
        for chunk in pd.read_csv(z.open('shapes.txt'), chunksize=chunksize):
            chunk.to_sql('shapes', conn, if_exists='append', index=False)
        progress_bar.progress(90)
        
        # 4. יצירת אינדקסים (זה הסוד למהירות!)
        status_text.text("⚡ מייצר אינדקסים לחיפוש מהיר...")
        conn.execute("CREATE INDEX idx_route_name ON routes(route_short_name)")
        conn.execute("CREATE INDEX idx_shape_id ON shapes(shape_id)")
        conn.close()
        
        progress_bar.progress(100)
        status_text.success("✅ מסד הנתונים מוכן!")
        return True

    except Exception as e:
        st.error(f"שגיאה בבניית המסד: {e}")
        return False

# --- שלב 2: פונקציות שליפה (SQL Queries) ---
def get_routes_by_number(line_number):
    conn = sqlite3.connect(DB_FILE)
    query = "SELECT * FROM routes WHERE route_short_name = ?"
    df = pd.read_sql_query(query, conn, params=(line_number,))
    conn.close()
    return df

def get_shape_points(route_id):
    conn = sqlite3.connect(DB_FILE)
    # א. מוצאים את ה-shape_id של הקו
    trip_query = "SELECT shape_id FROM trips WHERE route_id = ?"
    trip_df = pd.read_sql_query(trip_query, conn, params=(route_id,))
    
    if trip_df.empty:
        conn.close()
        return []
    
    shape_id = trip_df.iloc[0]['shape_id']
    
    # ב. שולפים את הנקודות לפי הסדר
    shape_query = "SELECT shape_pt_lat, shape_pt_lon FROM shapes WHERE shape_id = ? ORDER BY shape_pt_sequence"
    shape_df = pd.read_sql_query(shape_query, conn, params=(shape_id,))
    conn.close()
    
    # המרה לרשימה של (lat, lon)
    return list(zip(shape_df['shape_pt_lat'], shape_df['shape_pt_lon']))

# --- הממשק (UI) ---
st.title("🚍 SmartBus Pro - חיפוש מבוסס SQL")

# הפעלת המנוע
if init_database():
    
    col_search, col_map = st.columns([1, 2])
    
    with col_search:
        st.subheader("🔎 חיפוש קו")
        # חיפוש חופשי
        line_input = st.text_input("הכנס מספר קו (למשל 480, 5, 1)", "")
        
        if line_input:
            # שליפה מהירה מה-SQL
            results = get_routes_by_number(line_input)
            
            if not results.empty:
                st.success(f"נמצאו {len(results)} מסלולים לקו {line_input}")
                
                # בחירת כיוון ספציפי
                route_dict = {f"{row['route_long_name']}": row['route_id'] for idx, row in results.iterrows()}
                selected_desc = st.radio("בחר מסלול:", list(route_dict.keys()))
                
                if st.button("הצג מסלול ופקקים 🚦"):
                    selected_id = route_dict[selected_desc]
                    
                    # שליפת המסלול מה-SQL
                    with st.spinner('שולף נתוני מפה...'):
                        path_points = get_shape_points(selected_id)
                    
                    if path_points:
                        # שמירה ב-Session State כדי שהמפה לא תיעלם
                        st.session_state['current_path'] = path_points
                        st.session_state['current_title'] = f"קו {line_input}: {selected_desc}"
                    else:
                        st.warning("לא נמצא שרטוט מפה לקו זה.")
            else:
                st.warning("הקו לא נמצא במאגר.")

    with col_map:
        # הצגת המפה אם יש נתונים
        if 'current_path' in st.session_state:
            path = st.session_state['current_path']
            title = st.session_state.get('current_title', '')
            
            # מרכוז המפה
            mid_node = path[len(path)//2]
            m = folium.Map(location=mid_node, zoom_start=12)
            
            # שכבת פקקים
            folium.TileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}', attr='Google Traffic', name='Traffic', overlay=True).add_to(m)
            
            # הוספת הקו
            folium.PolyLine(path, color="red", weight=6, opacity=0.8, tooltip=title).add_to(m)
            
            # התחלה וסוף
            folium.Marker(path[0], icon=folium.Icon(color='green', icon='play'), tooltip="מוצא").add_to(m)
            folium.Marker(path[-1], icon=folium.Icon(color='red', icon='stop'), tooltip="יעד").add_to(m)
            
            st.info(f"מציג: {title}")
            st_folium(m, height=600, width="100%")
