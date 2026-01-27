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

if "detail_mode" not in st.session_state:
    st.session_state.detail_mode = False

if "map_center" not in st.session_state:
    st.session_state.map_center = {
        "lat": 40.7549,
        "lon": -73.9840,
        "zoom": 13
    }
    
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
            zipcode,
    
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
    
            -- more info fields
            lotarea,
            landuse,
            "community district",
            "city council district",
            policeprct,
            healthcenterdistrict,
            schooldist,
            firecomp,
            sanitdistrict,
            taxmap,
    
            -- geometry (ONLY ONCE)
            ST_AsGeoJSON(
              ST_Transform(
                ST_CollectionExtract(ST_MakeValid(geom), 3),
                4326
              )
            ) AS geom_geojson

    
        FROM gdf_merged
        WHERE geom IS NOT NULL
          AND NOT ST_IsEmpty(geom)
          AND ST_IsValid(geom)

    """


    df = pd.read_sql(query, engine)


    # Key: map the columns on web dataset to the orignal columns
    df = df.rename(columns={
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

    return df


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
view_state = pdk.ViewState(
    latitude=st.session_state.map_center["lat"],
    longitude=st.session_state.map_center["lon"],
    zoom=st.session_state.map_center["zoom"],
    pitch=0
)

# =============================
# Main Layout
# =============================
ratio = st.slider(
    "Layout balance (Map ↔ Info)",
    min_value=2,
    max_value=8,
    value=6,
    step=1
)

col_map, col_list = st.columns([ratio, 10 - ratio])


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

    gdf_map["AddressName"] = gdf_map["Address"].fillna("N/A")
    gdf_map["ZipCode"] = gdf_map["zipcode"].astype(str).fillna("N/A")
    gdf_map["BoroughName"] = gdf_map["Borough"].fillna("N/A")

    
    # Ensure BBL_10 is not empty
    if "BBL_10" in gdf_map.columns:
        gdf_map["BBL_10"] = gdf_map["BBL_10"].astype(str).fillna("N/A")
    
    # Convert to GeoJSON format
    # Use to_json() to ensure all properties are properly serialized
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
        initial_view_state=view_state,
        tooltip={
            "html": """
            <b>{AddressName}</b><br/>
            {BoroughName}, NY zipcode}<br/>
            <hr/>
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

    # =============================
    # # Search → Map interaction
    # # =============================
    # if search_query and len(list_df) > 0:
    #     try:
    #         centroid = list_df.geometry.centroid.iloc[0]
    
    #         st.session_state.map_center = {
    #             "lat": centroid.y,
    #             "lon": centroid.x,
    #             "zoom": 14 if len(list_df) > 1 else 16
    #         }
    #     except Exception:
    #         pass

    
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

                # # ---- map focus when this property is opened ----
                # geom = row.get("geom")
                
                # if geom is not None:
                #     try:
                #         centroid = geom.centroid
                #         st.session_state.map_center = {
                #             "lat": centroid.y,
                #             "lon": centroid.x,
                #             "zoom": 16
                #         }
                #     except Exception:
                #         pass

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
                # ===== formatting helpers（放在这里）=====
                def fmt_int(x):
                    return "N/A" if x is None else f"{int(round(x)):,}"
                
                def fmt_area(x):
                    return "N/A" if x is None else f"{int(x):,} sq ft"
                
                def fmt_height(x):
                    return "N/A" if x is None else f"{int(x)} ft"

            # ===== Core Information =====
                st.markdown("**Core Information**")

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
                    info_row("New Units", fmt_int(row["New Units"]))
                    info_row("New Floors", fmt_int(row["New Floors"]))
                    info_row("New Building Height", fmt_height(row["New Building Height"]))
                    info_row("BBL", row["BBL_10"])
                    info_row("Borough", row["Borough"])

                with col2:
                    info_row("Residential Area", fmt_area(row["Residential Area"]))
                    info_row("Commercial Area", fmt_area(row["Commercial Area"]))
                    info_row("Year Built", fmt_int(row["Year Built"]))
                    info_row("Air Rights", "Yes")
                    info_row(
                        "Zoning District",
                        safe_get(row, "Zoning District 1", "N/A")
                    )
                    
                    info_row(
                        "Building Class",
                        safe_get(row, "Building Class", "N/A")
                    )
                    
                    info_row(
                        "Owner",
                        safe_get(row, "Owner", "N/A")
                    )

                st.markdown("---")

                # =========================
                # Part 2: MORE – meaningful fields only
                # =========================
                
                MORE_INFO_FIELDS = {
                    "lotarea": "Lot Area (sq ft)",
                    "landuse": "Land Use",
                    "community district": "Community District",
                    "city council district": "City Council District",
                    "policeprct": "Police Precinct",
                    "healthcenterdistrict": "Health Center District",
                    "schooldist": "School District",
                    "firecomp": "Fire Company",
                    "sanitdistrict": "Sanitation District",
                    "taxmap": "Tax Map",
                }
                
                show_more = st.checkbox(
                    "More (additional property details)",
                    key=f"more_{bbl}"
                )

                st.session_state.detail_mode = show_more
                
                if show_more:
                    st.session_state.detail_mode = True
                
                    for col, label in MORE_INFO_FIELDS.items():
                        val = row.get(col)
                
                        if pd.isna(val) or str(val).strip() == "":
                            continue
                
                        info_row(label, val)
