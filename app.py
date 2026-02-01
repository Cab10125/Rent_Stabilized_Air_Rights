import streamlit as st
import pandas as pd
from sqlalchemy import create_engine
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
# Session state defaults
# -----------------------------
if "selected_bbl" not in st.session_state:
    st.session_state.selected_bbl = None

if "map_center" not in st.session_state:
    st.session_state.map_center = {
        "lat": 40.7549,
        "lon": -73.9840,
        "zoom": 12
    }

st.title("NYC Air Rights Explorer")

# =============================
# Global Search (TOP)
# =============================
search_mode = st.selectbox(
    "Search by",
    ["Address", "ZIP Code", "Borough"]
)

search_query = st.text_input(
    "Search",
    placeholder="Type to search…"
)

# -----------------------------
# Helper functions
# -----------------------------
def safe_get(row, key, default="N/A"):
    """Safely get field value, handle missing cases"""
    try:
        value = row.get(key)
        if pd.isna(value) or value is None:
            return default
        return value
    except (KeyError, AttributeError):
        return default

def fmt_int(x):
    try:
        if x is None or (isinstance(x, float) and pd.isna(x)):
            return "N/A"
        return f"{int(round(float(x))):,}"
    except Exception:
        return "N/A"

def fmt_float(x, ndigits=2):
    try:
        if x is None or (isinstance(x, float) and pd.isna(x)):
            return "N/A"
        return f"{round(float(x), ndigits)}"
    except Exception:
        return "N/A"

def fmt_height(x):
    try:
        if x is None or (isinstance(x, float) and pd.isna(x)):
            return "N/A"
        return f"{int(round(float(x)))} ft"
    except Exception:
        return "N/A"

def fmt_area_sqft(x):
    try:
        if x is None or (isinstance(x, float) and pd.isna(x)):
            return "N/A"
        return f"{int(round(float(x))):,} sq ft"
    except Exception:
        return "N/A"

def fmt_zip(z):
    try:
        if z is None or (isinstance(z, float) and pd.isna(z)):
            return "N/A"
        return str(int(z)).zfill(5) if str(z).isdigit() else str(z).zfill(5)
    except Exception:
        return "N/A"

def fmt_impact_pct(x):
    """
    data is ratio (0.92 means 92%)
    display as percent string, keep data unchanged
    """
    try:
        if x is None or (isinstance(x, float) and pd.isna(x)):
            return "N/A"
        v = float(x) * 100
        return f"{v:.1f}%"
    except Exception:
        return "N/A"

def info_row(label, value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        value = "N/A"

    st.markdown(
        f"""
        <style>
        [data-theme="light"] .info-label {{ color: #6b7280; }}
        [data-theme="light"] .info-value {{ color: #111827; }}
        [data-theme="dark"] .info-label {{ color: #9ca3af; }}
        [data-theme="dark"] .info-value {{ color: #e5e7eb; }}
        </style>

        <div style="margin: 6px 0 14px 0;">
            <div class="info-label" style="font-size: 13px;">
                {label}
            </div>
            <div class="info-value" style="font-size: 16px; font-weight: 500;">
                {value}
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

def get_geojson_center(geom_geojson):
    """
    Return (lat, lon) from GeoJSON string.
    Uses first coordinate (fast). Works for Polygon / MultiPolygon.
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

# -----------------------------
# Color mapping by Impact (green -> red)
# -----------------------------
def impact_to_color(value):
    """
    Map impact ratio (0–1) to RGB:
    low -> green, high -> red
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = 0.0

    v = max(0.0, min(1.0, v))
    r = int(200 * v)
    g = int(200 * (1 - v))
    b = 0
    return [r, g, b]

def get_color_with_selection(row):
    # Selected building -> blue highlight (avoid conflict with green scale)
    if st.session_state.selected_bbl == row.get("BBL"):
        return [0, 120, 255]
    return impact_to_color(row.get("impact_ratio", 0))

# -----------------------------
# Load data
# -----------------------------
@st.cache_data(show_spinner=True)
def load_data():
    engine = create_engine(os.environ["DATABASE_URL"])

    # IMPORTANT: % column name is exactly: % of New Units Impact
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

            "Residential Area",
            "Commercial Area",
            "Units Residential",
            "Units Commercial",
            "Units Total",

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

    df = pd.read_sql(query, engine)

    # rename to frontend-friendly column names
    df = df.rename(columns={
        "BBL_10": "BBL",
        "Borough_x": "Borough",
        "Address_x": "Address",
        "ZoneDist1": "Zoning District 1",
        "BldgClass": "Building Class",
        "OwnerName": "Owner",
    })

    return df

gdf = load_data()

# -----------------------------
# Data preparation
# -----------------------------
# Ensure numeric fields
for col in ["New Units", "New Floors", "New Building Height",
            "Residential Area", "Commercial Area",
            "Units Residential", "Units Commercial", "Units Total",
            "Year Built"]:
    if col in gdf.columns:
        gdf[col] = pd.to_numeric(gdf[col], errors="coerce")

# Impact ratio (0-1), keep original column unchanged
impact_col = "% of New Units Impact"
if impact_col in gdf.columns:
    gdf["impact_ratio"] = pd.to_numeric(gdf[impact_col], errors="coerce").fillna(0)
else:
    gdf["impact_ratio"] = 0.0

# Fill missing string fields
for col in ["BBL", "Borough", "Address", "Zoning District 1", "Building Class", "Owner", "Air Rights", "Zipcode"]:
    if col in gdf.columns:
        gdf[col] = gdf[col].fillna("N/A")

# -----------------------------
# Default view: Manhattan Midtown
# -----------------------------
view_state = pdk.ViewState(
    latitude=st.session_state.map_center["lat"],
    longitude=st.session_state.map_center["lon"],
    zoom=st.session_state.map_center["zoom"],
    pitch=0
)

# =============================
# Main Layout
# =============================
col_map, col_list = st.columns([5, 5])

# =============================
# Left: Interactive Map
# =============================
with col_map:
    st.subheader("Interactive Map")

    gdf_map = gdf.copy()

    # Map color by impact + selection highlight
    gdf_map["colorRGB"] = gdf_map.apply(get_color_with_selection, axis=1)

    # Tooltip-friendly fields (no spaces)
    gdf_map["NewUnits"] = pd.to_numeric(gdf_map.get("New Units", 0), errors="coerce").fillna(0)
    gdf_map["NewFloors"] = pd.to_numeric(gdf_map.get("New Floors", 0), errors="coerce").fillna(0)
    gdf_map["NewBuildingHeight"] = pd.to_numeric(gdf_map.get("New Building Height", 0), errors="coerce").fillna(0)

    # % impact displayed as percent (0.92 -> 92%)
    gdf_map["ImpactPct"] = (pd.to_numeric(gdf_map.get(impact_col, 0), errors="coerce").fillna(0) * 100).round(1)

    # Address / Borough / Zipcode for tooltip
    gdf_map["AddressName"] = gdf_map.get("Address", "N/A").fillna("N/A")
    gdf_map["BoroughName"] = gdf_map.get("Borough", "N/A").fillna("N/A")
    gdf_map["ZipcodeStr"] = gdf_map.get("Zipcode", "N/A").apply(fmt_zip)

    # Ensure BBL string
    gdf_map["BBL"] = gdf_map.get("BBL", "N/A").astype(str).fillna("N/A")

    # Convert to GeoJSON FeatureCollection
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
        features.append({
            "type": "Feature",
            "geometry": geom_obj,
            "properties": props
        })

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
            <b>BBL:</b> {BBL}<br/>
            <b>New Units:</b> {NewUnits}<br/>
            <b>% of Impact:</b> {ImpactPct}%<br/>
            <b>New Floors:</b> {NewFloors}<br/>
            <b>New Building Height:</b> {NewBuildingHeight}
            """,
            "style": {
                "backgroundColor": "black",
                "color": "white",
                "fontSize": "12px",
                "padding": "5px"
            }
        },
        map_style="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
    )

    st.pydeck_chart(deck)

# =============================
# Right: Property List
# =============================
with col_list:
    st.subheader("Property List")

    filtered_gdf = gdf.copy()

    if search_query:
        q = search_query.lower()

        if search_mode == "Address":
            filtered_gdf = filtered_gdf[
                filtered_gdf["Address"].astype(str).str.lower().str.contains(q, na=False)
            ]

        elif search_mode == "ZIP Code":
            filtered_gdf = filtered_gdf[
                filtered_gdf["Zipcode"].astype(str).str.contains(q, na=False)
            ]

        elif search_mode == "Borough":
            filtered_gdf = filtered_gdf[
                filtered_gdf["Borough"].astype(str).str.lower().str.contains(q, na=False)
            ]

    # keep your original list ranking: Top 10 by New Units
    list_df = (
        filtered_gdf
        .sort_values("New Units", ascending=False)
        .head(10)
    )

    st.caption(f"Top {len(list_df)} properties by New Units (Midtown Manhattan)")

    if len(list_df) == 0:
        st.info("No properties found.")
    else:
        for _, row in list_df.iterrows():
            bbl = safe_get(row, "BBL", "N/A")
            address = safe_get(row, "Address", None)
            title = address if address and address != "N/A" else f"BBL {bbl}"

            borough = safe_get(row, "Borough", "N/A")
            zipcode = fmt_zip(safe_get(row, "Zipcode", None))
            subtitle = f"New York, NY {zipcode}" if zipcode != "N/A" else f"New York, NY - {borough}"

            # Title + locate button
            row_cols = st.columns([4, 1])
            with row_cols[0]:
                st.markdown(f"**{title}**  \n{subtitle}")

            with row_cols[1]:
                locate = st.button("📍 Locate", key=f"locate_{bbl}")

            if locate:
                st.session_state.selected_bbl = bbl

                geom_geojson = row.get("geom_geojson")
                if geom_geojson:
                    center = get_geojson_center(geom_geojson)
                    if center:
                        st.session_state.map_center = {
                            "lat": center[0],
                            "lon": center[1],
                            "zoom": 16
                        }
                st.rerun()

            # Expandable detail: show all required fields in one place
            with st.expander(f"**{title}**\n\n{subtitle}"):

                # Order is based on your requirement:
                # Air Rights is placed right after New Building Height
                fields_in_order = [
                    ("BBL", "BBL", None),
                    ("Address", "Address", None),
                    ("Borough", "Borough", None),
                    ("Zipcode", "Zipcode", lambda v: fmt_zip(v)),
                    ("New Units", "New Units", lambda v: fmt_int(v)),
                    ("% of New Units Impact", "% of New Units Impact", lambda v: fmt_impact_pct(v)),
                    ("New Floors", "New Floors", lambda v: fmt_float(v, 2)),
                    ("New Building Height", "New Building Height", lambda v: fmt_height(v)),
                    ("Air Rights", "Air Rights", None),  # keep original string (may include "sqft")
                    ("Residential Area", "Residential Area", lambda v: fmt_area_sqft(v)),
                    ("Commercial Area", "Commercial Area", lambda v: fmt_area_sqft(v)),
                    ("Units Residential", "Units Residential", lambda v: fmt_int(v)),
                    ("Units Commercial", "Units Commercial", lambda v: fmt_int(v)),
                    ("Units Total", "Units Total", lambda v: fmt_int(v)),
                    ("Year Built", "Year Built", lambda v: fmt_int(v)),
                    ("Zoning District 1", "Zoning District 1", None),
                    ("Building Class", "Building Class", None),
                    ("Owner", "Owner", None),
                ]

                # Two-column layout for readability
                left, right = st.columns(2)
                half = int(np.ceil
