import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
import os
import numpy as np
import pydeck as pdk
import json

# -----------------------------
# Page config
# -----------------------------
st.set_page_config(
    page_title="NYC Air Rights Explorer",
    layout="wide"
)

# -----------------------------
# Session state init
# -----------------------------
if "selected_bbl" not in st.session_state:
    st.session_state.selected_bbl = None

if "map_center" not in st.session_state:
    st.session_state.map_center = {"lat": 40.7549, "lon": -73.9840, "zoom": 12}

if "auto_center_done" not in st.session_state:
    st.session_state.auto_center_done = False

st.title("NYC Air Rights Explorer")

# =============================
# Helper functions
# =============================
def safe_get(row, key, default="N/A"):
    try:
        v = row.get(key)
        if v is None or (isinstance(v, float) and pd.isna(v)) or (isinstance(v, str) and v.strip() == ""):
            return default
        return v
    except Exception:
        return default

def fmt_int(x):
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "N/A"
    try:
        return f"{int(round(float(x))):,}"
    except Exception:
        return "N/A"

def fmt_height(x):
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "N/A"
    try:
        return f"{int(round(float(x)))} ft"
    except Exception:
        return "N/A"

def fmt_area_with_unit(x):
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "N/A"
    return str(x)

def fmt_area_number(x):
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "N/A"
    try:
        return f"{int(round(float(x))):,} sq ft"
    except Exception:
        return "N/A"

def fmt_percent_from_frac(frac):
    if frac is None or (isinstance(frac, float) and pd.isna(frac)):
        return "N/A"
    try:
        return f"{int(round(float(frac) * 100))}%"
    except Exception:
        return "N/A"

def info_row(label, value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        value = "N/A"
    st.markdown(
        f"""
        <div style="margin: 6px 0 14px 0;">
            <div style="font-size: 13px; color: #9ca3af;">
                {label}
            </div>
            <div style="font-size: 16px; font-weight: 500; color: #e5e7eb;">
                {value}
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

def get_geojson_center(geom_geojson):
    """
    Return (lat, lon) center from GeoJSON string.
    Supports Polygon and MultiPolygon.
    Uses the first coordinate as a stable reference point.
    """
    try:
        geom = json.loads(geom_geojson)
        if geom["type"] == "Polygon":
            lon, lat = geom["coordinates"][0][0]
            return lat, lon
        if geom["type"] == "MultiPolygon":
            lon, lat = geom["coordinates"][0][0][0]
            return lat, lon
    except Exception:
        return None

def geojson_bounds(geom_geojson):
    """
    Return (min_lon, min_lat, max_lon, max_lat) from GeoJSON string.
    Supports Polygon and MultiPolygon.
    """
    try:
        geom = json.loads(geom_geojson)
        coords_list = []

        if geom["type"] == "Polygon":
            rings = geom["coordinates"]
            for ring in rings:
                coords_list.extend(ring)

        elif geom["type"] == "MultiPolygon":
            polys = geom["coordinates"]
            for poly in polys:
                for ring in poly:
                    coords_list.extend(ring)
        else:
            return None

        lons = [c[0] for c in coords_list]
        lats = [c[1] for c in coords_list]
        return min(lons), min(lats), max(lons), max(lats)
    except Exception:
        return None

def zoom_from_span(span):
    if span < 0.005:
        return 16
    if span < 0.01:
        return 15
    if span < 0.02:
        return 14
    if span < 0.05:
        return 13
    if span < 0.10:
        return 12
    return 11

def color_from_impact_frac(frac):
    """
    frac: 0.92 means 92%

    Bucket rules (percent):
      1-30   -> green
      30-60  -> yellow
      60-100 -> orange
      100-150 -> light red
      >150   -> deep red
      0 or missing -> gray
    """
    try:
        if frac is None or (isinstance(frac, float) and pd.isna(frac)):
            return [200, 200, 200]
        pct = float(frac) * 100.0
    except Exception:
        return [200, 200, 200]

    if pct <= 0:
        return [200, 200, 200]
    if pct < 30:
        return [0, 170, 0]
    if pct < 60:
        return [255, 210, 0]
    if pct < 100:
        return [255, 140, 0]
    if pct < 150:
        return [255, 120, 120]
    return [180, 30, 30]

def color_with_selection(row):
    if st.session_state.selected_bbl is not None and str(row.get("BBL")) == str(st.session_state.selected_bbl):
        return [0, 120, 255]
    return row.get("colorRGB", [200, 200, 200])

def normalize_borough_input(s):
    if s is None:
        return ""
    return str(s).strip().upper()

# =============================
# Load data
# =============================
@st.cache_data(show_spinner=True)
def load_data():
    db_url = os.environ["DATABASE_URL"]
    engine = create_engine(db_url, pool_pre_ping=True)

    query = """
        SELECT
            "BBL_10",
            "Borough_x",
            "Address_x",
            "Zipcode",

            "New Units",
            "% of New Units Impact",
            "New Floors",
            "New Building Height",
            "Air Rights",

            "# of Floors",
            "Units Residential",
            "Units Commercial",
            "Units Total",

            "FAR Built",
            "FAR Residential",
            "FAR Commercial",

            "Year Built",
            "ZoneDist1",
            "BldgClass",
            "OwnerName",

            ST_AsGeoJSON(
              ST_Transform(
                ST_CollectionExtract(ST_MakeValid(geometry), 3),
                4326
              )
            ) AS geom_geojson

        FROM gdf_merged
        WHERE geometry IS NOT NULL
          AND NOT ST_IsEmpty(geometry)
          AND ST_IsValid(geometry)
    """

    with engine.connect() as conn:
        df = pd.read_sql_query(text(query), conn)

    df = df.rename(columns={
        "BBL_10": "BBL",
        "Borough_x": "Borough",
        "Address_x": "Address",
        "ZoneDist1": "Zoning District 1",
        "BldgClass": "Building Class",
        "OwnerName": "Owner",
        "# of Floors": "Number of Floors",
    })

    return df

gdf = load_data()

# =============================
# Data preparation
# =============================
IMPACT_COL = "% of New Units Impact"
gdf[IMPACT_COL] = pd.to_numeric(gdf[IMPACT_COL], errors="coerce").fillna(0)

if "Zipcode" in gdf.columns:
    gdf["Zipcode"] = (
        gdf["Zipcode"]
        .astype(str)
        .str.replace(".0", "", regex=False)
        .str.zfill(5)
    )

gdf["ImpactPct"] = (gdf[IMPACT_COL] * 100).round(0).astype("int64")
gdf["ImpactPctStr"] = gdf["ImpactPct"].astype(str) + "%"

gdf["colorRGB"] = gdf[IMPACT_COL].apply(color_from_impact_frac)

# =============================
# Search / filter UI
# =============================
search_mode = st.selectbox("Search by", ["Address", "ZIP Code", "Borough"])

selected_zips = []
search_query = ""

if search_mode == "ZIP Code":
    zip_options = sorted([z for z in gdf["Zipcode"].dropna().unique() if str(z).strip() != ""])
    selected_zips = st.multiselect("Select ZIP Codes (multi-select)", options=zip_options)
else:
    search_query = st.text_input("Search", placeholder="Type to search…")

st.caption(
    "For Borough search, please use abbreviations only: MN = Manhattan, BX = Bronx, BK = Brooklyn, QN = Queens."
)

# =============================
# Filtering logic
# =============================
filtered_gdf = gdf.copy()

if search_mode == "ZIP Code" and selected_zips:
    filtered_gdf = filtered_gdf[filtered_gdf["Zipcode"].isin(selected_zips)]

elif search_query:
    q = search_query.lower().strip()
    if search_mode == "Address":
        filtered_gdf = filtered_gdf[filtered_gdf["Address"].astype(str).str.lower().str.contains(q, na=False)]
    elif search_mode == "Borough":
        # Expect abbreviations (MN/BX/BK/QN); keep contains for tolerance
        qn = normalize_borough_input(q)
        filtered_gdf = filtered_gdf[filtered_gdf["Borough"].astype(str).str.upper().str.contains(qn, na=False)]

# =============================
# Auto-center logic
# =============================
def compute_bbox_for_df(df):
    b = None
    for gj in df["geom_geojson"].dropna():
        bb = geojson_bounds(gj)
        if bb is None:
            continue
        minlon, minlat, maxlon, maxlat = bb
        if b is None:
            b = [minlon, minlat, maxlon, maxlat]
        else:
            b[0] = min(b[0], minlon)
            b[1] = min(b[1], minlat)
            b[2] = max(b[2], maxlon)
            b[3] = max(b[3], maxlat)
    return b

if search_mode == "ZIP Code" and selected_zips and len(filtered_gdf) > 0:
    bbox = compute_bbox_for_df(filtered_gdf)
    if bbox:
        minlon, minlat, maxlon, maxlat = bbox
        center_lon = (minlon + maxlon) / 2
        center_lat = (minlat + maxlat) / 2
        span = max(maxlon - minlon, maxlat - minlat)
        st.session_state.map_center = {
            "lat": center_lat,
            "lon": center_lon,
            "zoom": zoom_from_span(span)
        }

elif (not st.session_state.auto_center_done) and len(gdf) > 0:
    top_row = gdf.sort_values(IMPACT_COL, ascending=False).iloc[0]
    center = get_geojson_center(top_row.get("geom_geojson"))
    if center:
        st.session_state.map_center = {"lat": center[0], "lon": center[1], "zoom": 15}
    st.session_state.auto_center_done = True

# -----------------------------
# ViewState
# -----------------------------
view_state = pdk.ViewState(
    latitude=st.session_state.map_center["lat"],
    longitude=st.session_state.map_center["lon"],
    zoom=st.session_state.map_center["zoom"],
    pitch=0
)

# =============================
# Layout
# =============================
col_map, col_list = st.columns([5, 5])

# =============================
# Left: Map
# =============================
with col_map:
    st.subheader("Interactive Map")

    gdf_map = filtered_gdf.copy()
    gdf_map["colorRGB"] = gdf_map.apply(color_with_selection, axis=1)

    gdf_map["AddressName"] = gdf_map["Address"].fillna("N/A")
    gdf_map["BoroughName"] = gdf_map["Borough"].fillna("N/A")
    gdf_map["ZipcodeStr"] = gdf_map["Zipcode"].fillna("N/A")
    gdf_map["BBLStr"] = gdf_map["BBL"].astype(str).fillna("N/A")

    gdf_map["NewUnits"] = pd.to_numeric(gdf_map["New Units"], errors="coerce").fillna(0).astype(int)
    gdf_map["NewFloors"] = pd.to_numeric(gdf_map["New Floors"], errors="coerce").fillna(0).round(2)
    gdf_map["NewBuildingHeight"] = pd.to_numeric(gdf_map["New Building Height"], errors="coerce").fillna(0).round(0).astype(int)

    if "ImpactPctStr" not in gdf_map.columns:
        gdf_map["ImpactPctStr"] = "N/A"

    features = []
    for _, r in gdf_map.iterrows():
        gj = r.get("geom_geojson")
        if not gj or pd.isna(gj):
            continue
        try:
            geom_obj = json.loads(gj)
        except Exception:
            continue

        props = r.drop(labels=["geom_geojson"]).to_dict()
        features.append({"type": "Feature", "geometry": geom_obj, "properties": props})

    geo_data = {"type": "FeatureCollection", "features": features}

    layer = pdk.Layer(
        "GeoJsonLayer",
        data=geo_data,
        pickable=True,
        stroked=True,
        filled=True,
        get_fill_color="properties.colorRGB",
        get_line_color=[255, 255, 255, 200],
        line_width_min_pixels=1,
        extruded=False,
    )

    deck = pdk.Deck(
        layers=[layer],
        initial_view_state=view_state,
        tooltip={
            "html": """
            <b>{AddressName}</b><br/>
            {BoroughName}, NY {ZipcodeStr}<br/>
            <hr/>
            <b>BBL:</b> {BBLStr}<br/>
            <b>Impact:</b> {ImpactPctStr}<br/>
            <b>New Units:</b> {NewUnits}<br/>
            <b>New Floors:</b> {NewFloors}<br/>
            <b>New Building Height:</b> {NewBuildingHeight}
            """,
            "style": {"backgroundColor": "black", "color": "white", "fontSize": "12px", "padding": "6px"},
        },
        map_style="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
    )

    st.pydeck_chart(deck)

# =============================
# Right: Property List (Top 10 by impact)
# =============================
with col_list:
    st.subheader("Property List")

    list_df = (
        filtered_gdf
        .sort_values(IMPACT_COL, ascending=False)
        .head(10)
    )

    st.caption(f"Top {len(list_df)} properties by Impact (all data)")

    if len(list_df) == 0:
        st.info("No properties found for current filter.")
    else:
        for _, row in list_df.iterrows():
            bbl = safe_get(row, "BBL", "N/A")
            address = safe_get(row, "Address", None)
            title = str(address) if address and address != "N/A" else f"BBL {bbl}"
            subtitle = f"New York, NY {safe_get(row, 'Zipcode', 'N/A')}"

            row_cols = st.columns([4, 1])
            with row_cols[0]:
                st.markdown(f"**{title}**  \n{subtitle}")
            with row_cols[1]:
                locate = st.button("📍 Locate", key=f"locate_{bbl}")

            if locate:
                st.session_state.selected_bbl = str(bbl)
                gj = row.get("geom_geojson")
                if gj:
                    c = get_geojson_center(gj)
                    if c:
                        st.session_state.map_center = {"lat": c[0], "lon": c[1], "zoom": 16}
                st.rerun()

            with st.expander(f"**{title}**\n\n{subtitle}"):
                info_row("Address", safe_get(row, "Address"))
                info_row("Borough", safe_get(row, "Borough"))
                info_row("Zip Code", safe_get(row, "Zipcode"))
                info_row("BBL", safe_get(row, "BBL"))

                info_row("New Units", fmt_int(safe_get(row, "New Units")))
                info_row("Impact", fmt_percent_from_frac(safe_get(row, IMPACT_COL)))
                info_row("New Floors", safe_get(row, "New Floors"))
                info_row("New Building Height", fmt_height(safe_get(row, "New Building Height")))

                info_row("Air Rights", fmt_area_with_unit(safe_get(row, "Air Rights")))

                info_row("Zoning District 1", safe_get(row, "Zoning District 1"))
                info_row("Building Class", safe_get(row, "Building Class"))
                info_row("Owner", safe_get(row, "Owner"))
                info_row("Year Built", fmt_int(safe_get(row, "Year Built")))

                info_row("Number of Floors", fmt_int(safe_get(row, "Number of Floors")))
                info_row("Units Residential", fmt_int(safe_get(row, "Units Residential")))
                info_row("Units Commercial", fmt_int(safe_get(row, "Units Commercial")))
                info_row("Units Total", fmt_int(safe_get(row, "Units Total")))

                info_row("FAR Built", safe_get(row, "FAR Built"))
                info_row("FAR Residential", safe_get(row, "FAR Residential"))
                info_row("FAR Commercial", safe_get(row, "FAR Commercial"))

                info_row("Residential Area", fmt_area_number(safe_get(row, "Residential Area")))
                info_row("Commercial Area", fmt_area_number(safe_get(row, "Commercial Area")))
