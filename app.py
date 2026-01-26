import streamlit as st
import pandas as pd
import geopandas as gpd
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

st.title("NYC Air Rights Explorer")

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

def format_number(value, default=0):
    """Format number, return default value if missing"""
    try:
        if pd.isna(value) or value is None:
            return default
        num = float(value)
        if num == int(num):
            return int(num)
        return round(num, 2)
    except (ValueError, TypeError):
        return default

def info_row(label, value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        value = "N/A"

    st.markdown(
        f"""
        <style>
        /* Light theme */
        [data-theme="light"] .info-label {{
            color: #6b7280;
        }}
        [data-theme="light"] .info-value {{
            color: #111827;
        }}

        /* Dark theme */
        [data-theme="dark"] .info-label {{
            color: #9ca3af;
        }}
        [data-theme="dark"] .info-value {{
            color: #e5e7eb;
        }}
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


def first_non_null(row, cols, default="N/A"):
    """Return the first non-empty value among cols."""
    for c in cols:
        if c in row.index:
            v = row[c]
            if pd.notna(v) and str(v).strip() != "":
                return v
    return default


# -----------------------------
# Load data
# -----------------------------
@st.cache_data(show_spinner=True)
def load_data():
    engine = create_engine(
        os.environ["DATABASE_URL"]
    )


    query = """
        SELECT
            bbl_10,
            borough_x,
            address_x,
        
            -- core metrics
            "new units",
            "new floors",
            "new building height",
        
            -- areas
            resarea,
            comarea,
        
            -- attributes
            yearbuilt,
            zonedist1,
            bldgclass,
            ownername,
        
            new_units_capped,
            geom
        FROM gdf_merged
        WHERE geom IS NOT NULL

    """

    gdf = gpd.read_postgis(
        query,
        engine,
        geom_col="geom"
    )

    # Key: map the columns on web dataset to the orignal columns
    gdf = gdf.rename(columns={
        "bbl_10": "BBL_10",
        "borough_x": "Borough",
        "address_x": "Address",
    
        "new units": "New Units",
        "new floors": "New Floors",
        "new building height": "New Building Height",
    
        "resarea": "Residential Area",
        "comarea": "Commercial Area",
        "yearbuilt": "Year Built",
        "zonedist1": "Zoning District 1",
        "bldgclass": "Building Class",
        "ownername": "Owner"
    })

    return gdf


gdf = load_data()

# -----------------------------
# Data preparation
# -----------------------------
# Ensure New Units is numeric type
gdf["New Units"] = pd.to_numeric(gdf["New Units"], errors="coerce").fillna(0)

# Fill missing values (for display)
fill_cols = ["Residential Area", "Commercial Area", "New Floors", 
             "New Building Height", "Year Built"]
for col in fill_cols:
    if col in gdf.columns:
        if gdf[col].dtype in ['float64', 'int64']:
            gdf[col] = gdf[col].fillna(0)
        else:
            gdf[col] = gdf[col].fillna("N/A")

# Color classification rules: 8 intervals
def assign_bucket(units):
    """Assign New Units to corresponding bucket"""
    units = float(units) if not pd.isna(units) else 0
    
    if units == 0:
        return "0"
    elif 1 <= units <= 5:
        return "1–5"
    elif 5 < units <= 20:
        return "5–20"
    elif 20 < units <= 50:
        return "20–50"
    elif 50 < units <= 100:
        return "50–100"
    elif 100 < units <= 300:
        return "100–300"
    elif 300 < units <= 500:
        return "300–500"
    else:  # units > 500
        return "500+"

gdf["units_bucket"] = gdf["New Units"].apply(assign_bucket)

# Color mapping (NYC style, similar to urban planning/FAR maps)
COLOR_MAP = {
    "0": [200, 200, 200],        # Gray - no available space
    "1–5": [255, 245, 204],      # Light yellow
    "5–20": [255, 225, 170],     # Yellow-orange
    "20–50": [255, 195, 120],    # Orange
    "50–100": [255, 160, 90],    # Deep orange
    "100–300": [240, 120, 70],   # Orange-red
    "300–500": [220, 80, 60],    # Red-orange
    "500+": [180, 40, 40],       # Deep red (capped color)
}

# Add color to GeoDataFrame (for map display)
gdf["color"] = gdf["units_bucket"].apply(
    lambda x: COLOR_MAP.get(x, [200, 200, 200]) + [180]  # Add transparency
)

# Ensure all tooltip required fields exist and are not NaN
tooltip_fields = {
    "BBL_10": "N/A",
    "New Units": 0,
    "New Floors": 0,
    "New Building Height": 0
}

for field, default in tooltip_fields.items():
    if field not in gdf.columns:
        gdf[field] = default
    else:
        gdf[field] = gdf[field].fillna(default)

# -----------------------------
# Default view: Manhattan Midtown
# -----------------------------
DEFAULT_VIEW = pdk.ViewState(
    latitude=40.7549,
    longitude=-73.9840,
    zoom=14,
    pitch=0
)

# -----------------------------
# Main Layout: Two columns
# -----------------------------
col_map, col_list = st.columns([2.2, 1])

# =============================
# Left: Interactive Map
# =============================
with col_map:
    st.subheader("Interactive Map")
    
    # Prepare map data: ensure color is in properties, RGB array format (without transparency)
    gdf_map = gdf.copy()
    
    # Add colorRGB field (RGB array, without transparency)
    # pydeck requires RGB array format [r, g, b]
    def get_color_rgb(bucket):
        """Get RGB color array based on units_bucket"""
        color_map = {
            "0": [200, 200, 200],
            "1–5": [255, 245, 204],
            "5–20": [255, 225, 170],
            "20–50": [255, 195, 120],
            "50–100": [255, 160, 90],
            "100–300": [240, 120, 70],
            "300–500": [220, 80, 60],
            "500+": [180, 40, 40],
        }
        return color_map.get(bucket, [200, 200, 200])
    
    gdf_map["colorRGB"] = gdf_map["units_bucket"].apply(get_color_rgb)
    
    # For tooltip to work correctly, add fields without spaces (pydeck tooltip limitation)
    # Also ensure values are properly formatted as numbers or strings
    if "New Units" in gdf_map.columns:
        gdf_map["NewUnits"] = pd.to_numeric(gdf_map["New Units"], errors="coerce").fillna(0)
    if "New Floors" in gdf_map.columns:
        gdf_map["NewFloors"] = pd.to_numeric(gdf_map["New Floors"], errors="coerce").fillna(0)
    if "New Building Height" in gdf_map.columns:
        gdf_map["NewBuildingHeight"] = pd.to_numeric(gdf_map["New Building Height"], errors="coerce").fillna(0)
    
    # Ensure BBL_10 is not empty
    if "BBL_10" in gdf_map.columns:
        gdf_map["BBL_10"] = gdf_map["BBL_10"].astype(str).fillna("N/A")
    
    # Convert to GeoJSON format
    # Use to_json() to ensure all properties are properly serialized
    geo_json_str = gdf_map.to_json()
    geo_data = json.loads(geo_json_str)
    
    # Map layer
    # pydeck GeoJsonLayer uses "properties.fieldName" to access fields in properties
    layer = pdk.Layer(
        "GeoJsonLayer",
        data=geo_data,
        pickable=True,
        stroked=True,
        filled=True,
        get_fill_color="properties.colorRGB",
        get_line_color=[255, 255, 255, 200],
        line_width_min_pixels=1,
        get_elevation=0,
        extruded=False,
    )
    
    # Tooltip configuration
    # Use bracket notation for field names with spaces
    deck = pdk.Deck(
        layers=[layer],
        initial_view_state=DEFAULT_VIEW,
        tooltip={
            "html": """
            <b>BBL:</b> {BBL_10}<br/>
            <b>New Units:</b> {NewUnits}<br/>
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
search_mode = st.selectbox(
    "Search by",
    ["Address", "ZIP Code", "Borough"]
)

search_query = st.text_input(
    "Search",
    placeholder="Type to search…"
)

with col_list:
    st.subheader("Property List")
    
    # Sort by New Units descending, get Top 10
    filtered_gdf = gdf.copy()

    if search_query:
        q = search_query.lower()
    
        if search_mode == "Address":
            filtered_gdf = filtered_gdf[
                filtered_gdf["Address"].str.lower().str.contains(q, na=False)
            ]
    
        elif search_mode == "ZIP Code":
            filtered_gdf = filtered_gdf[
                filtered_gdf["zipcode"].astype(str).str.contains(q, na=False)
            ]
    
        elif search_mode == "Borough":
            filtered_gdf = filtered_gdf[
                filtered_gdf["Borough"].str.lower().str.contains(q, na=False)
            ]
    
    list_df = (
        filtered_gdf
        .sort_values("New Units", ascending=False)
        .head(10)
    )

    
    st.caption(f"Top {len(list_df)} properties by New Units (Midtown Manhattan)")
    
    if len(list_df) == 0:
        st.info("No properties found in this area.")
    else:
        for idx, row in list_df.iterrows():
            # Collapsed state: title and subtitle
            bbl = safe_get(row, "BBL_10", "N/A")
            
            # Try to get address
            address_fields = ["Address", "Street Name", "House Number", "Street"]
            address = None
            for field in address_fields:
                if field in row.index:
                    addr_val = safe_get(row, field, None)
                    if addr_val and addr_val != "N/A":
                        address = str(addr_val)
                        break
            
            # If no address, use BBL
            title = address if address else f"BBL {bbl}"
            
            # Subtitle: New York, NY + ZIP
            zip_code = safe_get(row, "ZIP Code", None)
            if not zip_code or zip_code == "N/A":
                zip_code = safe_get(row, "ZIP", None)
            if not zip_code or zip_code == "N/A":
                # Fallback to borough
                borough = safe_get(row, "Borough", None)
                if borough and borough != "N/A":
                    subtitle = f"New York, NY - {borough}"
                else:
                    subtitle = "New York, NY"
            else:
                subtitle = f"New York, NY {zip_code}"
            
            # Expandable card
            with st.expander(f"**{title}**\n\n{subtitle}"):

            # ==== Display Config ====
                LABEL_MAP = {
                    "Borough #": "Borough Number",
                    "Block #": "Block Number",
                    "Lot #": "Lot Number",
                    "# of Floors": "Number of Floors",
                }

                CORE_FIELDS = [
                    "New Units",
                    "New Floors",
                    "New Building Height",
                    "Air Rights",
                    "BBL_10",
                    "Borough",
                    "Address",
                    "Zoning District 1",
                    "Building Class",
                    "Owner",
                ]
                
            # =========================
            # Part 1: Core summary information (BRIEF)
            # =========================
                st.markdown("### Core Information")

                # ---- Core values ----
                new_units = format_number(safe_get(row, "New Units", 0))
                new_floors = format_number(safe_get(row, "New Floors", 0))
                new_height = format_number(safe_get(row, "New Building Height", 0))

                year_built = safe_get(row, "Year Built", "N/A")
                if year_built != "N/A" and year_built != 0:
                    year_built = format_number(year_built, "N/A")

                res_area = format_number(safe_get(row, "Residential Area", 0))
                comm_area = format_number(safe_get(row, "Commercial Area", 0))

                air_rights = "Yes"

                # ---- Zoning / Special (apply rules) ----
                zoning = first_non_null(
                    row,
                    ["Zoning District 1", "Zoning District 2", "Zoning District 3", "Zoning District 4"],
                    default="N/A"
                )
                special_district = safe_get(row, "Special District 1", "N/A")

                building_class = safe_get(row, "Building Class", "N/A")
                owner = safe_get(row, "Owner", "N/A")

                # ---- Layout ----
                col1, col2 = st.columns(2)

                with col1:
                    info_row("New Units", new_units)
                    info_row("New Floors", new_floors)
                    info_row("New Building Height", new_height)
                    info_row("Year Built", year_built)

                with col2:
                    info_row("Residential Area", res_area)
                    info_row("Commercial Area", comm_area)
                    info_row("Air Rights", air_rights)

                info_row("Zoning District", zoning)
                info_row("Special District", special_district)
                info_row("Building Class", building_class)
                info_row("Owner", owner)

                st.markdown("---")

                # =========================
                # Part 2: MORE – all remaining fields
                # =========================
                show_more = st.checkbox(
                    "More (all other columns)",
                    key=f"more_{bbl}"
                )

                if show_more:

                    SKIP_COLS = {
                        "geometry", "color", "colorRGB", "units_bucket",
                        "Zoning District 1", "Zoning District 2", "Zoning District 3", "Zoning District 4",
                        "Special District 1", "Special District 2",
                        "New Units", "New Floors", "New Building Height",
                        "Residential Area", "Commercial Area", "Air Rights",
                        "Year Built", "Building Class", "Owner"
                    }

                    LABEL_MAP = {
                        "Borough #": "Borough Number",
                        "Block #": "Block Number",
                        "Lot #": "Lot Number",
                        "# of Floors": "Number of Floors",
                    }

                    for col in row.index:
                        if col in SKIP_COLS:
                            continue

                        value = row[col]
                        if pd.isna(value) or str(value).strip() == "":
                            continue

                        if isinstance(value, (int, float)):
                            value = format_number(value, "N/A")

                        label = LABEL_MAP.get(col, col.replace("#", "Number"))
                        info_row(label, value)
