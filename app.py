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
    # 你说 Air Rights 自带 sqft（字符串），这里不强转，只原样展示即可
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
    # frac=0.92 -> "92%"
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
    Uses first coordinate as a cheap stable center reference.
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
    Polygon / MultiPolygon.
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
    # span: degrees
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
    Bucket rules:
      1-30% green
      30-60% yellow
      60-100% orange
      100-150% light red
      >150% deep red
      0 or missing => gray
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
        return [0, 170, 0]         # green
    if pct < 60:
        return [255, 210, 0]       # yellow
    if pct < 100:
        return [255, 140, 0]       # orange
    if pct < 150:
        return [255, 120, 120]     # light red
    return [180, 30, 30]           # deep red

def color_with_selection(row):
    # 选中用蓝色，避免和低impact绿色混在一起
    if st.session_state.selected_bbl is not None and str(row.get("BBL")) == str(st.session_state.selected_bbl):
        return [0, 120, 255]
    return row.get("colorRGB", [200, 200, 200])


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
# impact: keep numeric fraction for sort & colors
IMPACT_COL = "% of New Units Impact"  # 你确认的列名
gdf[IMPACT_COL] = pd.to_numeric(gdf[IMPACT_COL], errors="coerce").fillna(0)

# ensure zipcode is string 5 digits
if "Zipcode" in gdf.columns:
    gdf["Zipcode"] = gdf["Zipcode"].astype(str).str.replace(".0", "", regex=False).str.zfill(5)

# add display-friendly tooltip fields (no spaces)
gdf["ImpactPct"] = (gdf[IMPACT_COL] * 100).round(0).astype(int)  # numeric pct
gdf["ImpactPctStr"] = gdf["ImpactPct"].astype(str) + "%"

# colors
gdf["colorRGB"] = gdf[IMPACT_COL].apply(color_from_impact_frac)

# =============================
# Search / filter UI
# =============================
search_mode = st.selectbox("Search by", ["Address", "ZIP Code", "Borough"])

# ZIP multi-select when ZIP Code mode
selected_zips = []
search_query = ""

if search_mode == "ZIP Code":
    zip_options = sorted([z for z in gdf["Zipcode"].dropna().unique() if str(z).strip() != ""])
    selected_zips = st.multiselect(
        "Select ZIP Codes (multi-select)",
        options=zip_options
    )
else:
    search_query = st.text_input("Search", placeholder="Type to search…")


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
        filtered_gdf = filtered_gdf[filtered_gdf["Borough"].astype(str).str.lower().str.contains(q, na=False)]


# =============================
# Auto-center logic (default focus)
# =============================
# 目标：
# - 如果用户选了 ZIP：地图自动 zoom 到 ZIP 过滤后的 bbox
# - 否则：第一次打开自动 zoom 到 impact 最高的那栋附近
#
# 注意：为了避免无限改 session_state，我们只在需要时更新一次 auto_center_done
#
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

# Case A: ZIP multi-select -> fit bounds
if search_mode == "ZIP Code" and selected_zips and len(filtered_gdf) > 0:
    bbox = compute_bbox_for_df(filtered_gdf)
    if bbox:
        minlon, minlat, maxlon, maxlat = bbox
        center_lon = (minlon + maxlon) / 2
