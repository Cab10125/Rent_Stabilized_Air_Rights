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
st.set_page_config(page_title="NYC Air Rights Explorer", layout="wide")
st.title("NYC Air Rights Explorer")

# -----------------------------
# Session state
# -----------------------------
if "selected_bbl" not in st.session_state:
    st.session_state.selected_bbl = None

if "map_center" not in st.session_state:
    st.session_state.map_center = {"lat": 40.7549, "lon": -73.9840, "zoom": 12}

if "use_map_filter" not in st.session_state:
    st.session_state.use_map_filter = False

# =============================
# Global Search (TOP)
# =============================
search_mode = st.selectbox("Search by", ["Address", "ZIP Code", "Borough"])

search_query = st.text_input("Search", placeholder="Type to search…")

st.caption(
    "Tip: Please use borough abbreviations for borough search: "
    "MN: Manhattan, BX: Bronx, BK: Brooklyn, QN: Queen"
)

# -----------------------------
# Helper functions
# -----------------------------
def safe_get(row, key, default="N/A"):
    try:
        v = row.get(key)
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return default
        if isinstance(v, str) and v.strip() == "":
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

def fmt_float(x, nd=2):
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "N/A"
    try:
        return f"{float(x):.{nd}f}"
    except Exception:
        return "N/A"

def fmt_height_ft(x):
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "N/A"
    try:
        return f"{int(round(float(x)))} ft"
    except Exception:
        return "N/A"

def fmt_area_sqft(x):
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "N/A"
    try:
        return f"{int(round(float(x))):,} sq ft"
    except Exception:
        # Some fields may already contain units (e.g., "123 sq ft")
        try:
            s = str(x).strip()
            return s if s else "N/A"
        except Exception:
            return "N/A"

def fmt_percent_label(pct):
    if pct is None or (isinstance(pct, float) and pd.isna(pct)):
        return "N/A"
    try:
        return f"{float(pct):.0f}%"
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

        <div style="margin: 6px 0 12px 0;">
            <div class="info-label" style="font-size: 13px;">{label}</div>
            <div class="info-value" style="font-size: 15px; font-weight: 500;">{value}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

def get_geojson_center(geom_geojson):
    """
    Return (lat, lon) center from GeoJSON string.
    Supports Polygon and MultiPolygon.
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

def zoom_to_delta(zoom):
    """
    Approximate bbox half-size (degrees) from zoom.
    Streamlit PyDeck selection does not expose live viewport bounds,
    so we approximate "visible area" using the stored center + zoom.
    """
    try:
        z = float(zoom)
    except Exception:
        z = 12.0
    # Tuned defaults: at zoom=12 => ~0.04 degrees; zoom up => smaller; zoom down => larger
    base = 0.04
    delta = base / (2 ** (z - 12))
    return float(np.clip(delta, 0.002, 0.25))

def impact_bucket_color(pct):
    """
    Color rules:
      1% - 30%   => green
      30% - 60%  => yellow
      60% - 100% => orange
      100% - 150%=> light red
      150%+      => deep red
    Missing => gray
    """
    if pct is None or (isinstance(pct, float) and pd.isna(pct)):
        return [200, 200, 200]

    try:
        p = float(pct)
    except Exception:
        return [200, 200, 200]

    if p < 1:
        return [200, 200, 200]
    if p < 30:
        return [0, 170, 0]
    if p < 60:
        return [255, 215, 0]
    if p < 100:
        return [255, 165, 0]
    if p < 150:
        return [255, 120, 120]
    return [180, 0, 0]

def get_fill_color(row):
    # Highlight selected property with cyan to avoid conflicting with "low impact = green"
    if st.session_state.selected_bbl is not None and str(row.get("BBL")) == str(st.session_state.selected_bbl):
        return [0, 200, 255]
    return row.get("colorRGB", [200, 200, 200])

def normalize_borough_query(q):
    q = (q or "").strip().upper()
    mapping = {"MN": "MANHATTAN", "BX": "BRONX", "BK": "BROOKLYN", "QN": "QUEEN"}
    return mapping.get(q, q)

def parse_zip_list(q):
    if not q:
        return []
    # Accept comma/space/semicolon separated
    parts = [p.strip() for p in q.replace(";", ",").replace(" ", ",").split(",")]
    zips = []
    for p in parts:
        if p.isdigit() and len(p) in (4, 5):
            zips.append(p.zfill(5))
    return sorted(list(set(zips)))

# -----------------------------
# Load data
# -----------------------------
@st.cache_data(show_spinner=True)
def load_data():
    engine = create_engine(os.environ["DATABASE_URL"])

    # Alias the percent column to avoid any DBAPI placeholder confusion
    query = """
        SELECT
            "BBL_10" AS bbl,
            "Borough_x" AS borough,
            "Address_x" AS address,
            "Zipcode" AS zipcode,

            "New Units" AS new_units,
            "% of New Units Impact" AS impact_ratio,
            "New Floors" AS new_floors,
            "New Building Height" AS new_building_height,
            "Air Rights" AS air_rights,

            "Residential Area" AS residential_area,
            "Commercial Area" AS commercial_area,
            "Units Residential" AS units_residential,
            "Units Commercial" AS units_commercial,
            "Units Total" AS units_total,

            "Year Built" AS year_built,
            "ZoneDist1" AS zonedist1,
            "BldgClass" AS bldgclass,
            "OwnerName" AS ownername,

            "Latitude" AS latitude,
            "Longitude" AS longitude,

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

    # Standardize names used in the app
    df = df.rename(
        columns={
            "bbl": "BBL",
            "borough": "Borough",
            "address": "Address",
            "zipcode": "Zipcode",
            "new_units": "New Units",
            "impact_ratio": "% of New Units Impact",
            "new_floors": "New Floors",
            "new_building_height": "New Building Height",
            "air_rights": "Air Rights",
            "residential_area": "Residential Area",
            "commercial_area": "Commercial Area",
            "units_residential": "Units Residential",
            "units_commercial": "Units Commercial",
            "units_total": "Units Total",
            "year_built": "Year Built",
            "zonedist1": "Zoning District 1",
            "bldgclass": "Building Class",
            "ownername": "Owner",
            "latitude": "Latitude",
            "longitude": "Longitude",
        }
    )

    return df

gdf = load_data()

# -----------------------------
# Data preparation
# -----------------------------
# Numeric conversions
gdf["New Units"] = pd.to_numeric(gdf["New Units"], errors="coerce")
gdf["New Floors"] = pd.to_numeric(gdf["New Floors"], errors="coerce")
gdf["New Building Height"] = pd.to_numeric(gdf["New Building Height"], errors="coerce")
gdf["Latitude"] = pd.to_numeric(gdf["Latitude"], errors="coerce")
gdf["Longitude"] = pd.to_numeric(gdf["Longitude"], errors="coerce")

# Impact ratio: 0.92 means 92%
gdf["% of New Units Impact"] = pd.to_numeric(gdf["% of New Units Impact"], errors="coerce")
gdf["ImpactPct"] = gdf["% of New Units Impact"] * 100.0
gdf["ImpactPctLabel"] = gdf["ImpactPct"].apply(fmt_percent_label)

# Colors based on ImpactPct bucket
gdf["colorRGB"] = gdf["ImpactPct"].apply(impact_bucket_color)

# Make sure ID fields are strings
gdf["BBL"] = gdf["BBL"].astype(str)
gdf["Zipcode"] = gdf["Zipcode"].astype(str).str.zfill(5)
gdf["Borough"] = gdf["Borough"].astype(str)
gdf["Address"] = gdf["Address"].astype(str)

# -----------------------------
# Default map focus: highest impact area
# -----------------------------
if gdf["ImpactPct"].notna().any():
    top_row = gdf.sort_values("ImpactPct", ascending=False).iloc[0]
    if pd.notna(top_row.get("Latitude")) and pd.notna(top_row.get("Longitude")):
        st.session_state.map_center = {
            "lat": float(top_row["Latitude"]),
            "lon": float(top_row["Longitude"]),
            "zoom": 14,
        }

# -----------------------------
# Apply search filters
# -----------------------------
filtered_gdf = gdf.copy()

if search_query:
    q = search_query.strip()

    if search_mode == "Address":
        filtered_gdf = filtered_gdf[
            filtered_gdf["Address"].str.lower().str.contains(q.lower(), na=False)
        ]

    elif search_mode == "Borough":
        bq = normalize_borough_query(q)
        filtered_gdf = filtered_gdf[
            filtered_gdf["Borough"].str.upper().str.contains(bq.upper(), na=False)
        ]

    elif search_mode == "ZIP Code":
        zips = parse_zip_list(q)
        if zips:
            filtered_gdf = filtered_gdf[filtered_gdf["Zipcode"].isin(zips)]

            # Zoom the map to cover selected ZIPs (approx by bounding box of points)
            pts = filtered_gdf.dropna(subset=["Latitude", "Longitude"])
            if len(pts) > 0:
                lat_min, lat_max = pts["Latitude"].min(), pts["Latitude"].max()
                lon_min, lon_max = pts["Longitude"].min(), pts["Longitude"].max()
                lat_c = float((lat_min + lat_max) / 2)
                lon_c = float((lon_min + lon_max) / 2)

                span = max(float(lat_max - lat_min), float(lon_max - lon_min))
                # Heuristic zoom from span
                if span < 0.01:
                    zoom = 15
                elif span < 0.03:
                    zoom = 14
                elif span < 0.07:
                    zoom = 13
                else:
                    zoom = 12

                st.session_state.map_center = {"lat": lat_c, "lon": lon_c, "zoom": zoom}

# -----------------------------
# Layout
# -----------------------------
col_map, col_list = st.columns([5, 5])

# =============================
# Left: Interactive Map
# =============================
with col_map:
    st.subheader("Interactive Map")

    if st.button("Update Top 10 from current map view"):
        st.session_state.use_map_filter = True

    # Prepare GeoJSON features
    features = []
    for _, r in filtered_gdf.iterrows():
        gj = r.get("geom_geojson")
        if not gj or pd.isna(gj):
            continue
        try:
            geom_obj = json.loads(gj)
        except Exception:
            continue

        props = r.drop(labels=["geom_geojson"]).to_dict()

        # Add tooltip-friendly aliases
        props["AddressName"] = props.get("Address", "N/A")
        props["BoroughName"] = props.get("Borough", "N/A")
        props["ZipcodeLabel"] = props.get("Zipcode", "N/A")
        props["BBLLabel"] = props.get("BBL", "N/A")
        props["NewUnitsVal"] = 0 if pd.isna(props.get("New Units")) else float(props.get("New Units"))
        props["NewFloorsVal"] = 0 if pd.isna(props.get("New Floors")) else float(props.get("New Floors"))
        props["NewHeightVal"] = 0 if pd.isna(props.get("New Building Height")) else float(props.get("New Building Height"))
        props["ImpactPctLabel"] = props.get("ImpactPctLabel", "N/A")

        # Ensure map fill color exists and is valid
        props["colorRGB"] = get_fill_color(props)

        features.append({"type": "Feature", "geometry": geom_obj, "properties": props})

    geo_data = {"type": "FeatureCollection", "features": features}

    view_state = pdk.ViewState(
        latitude=st.session_state.map_center["lat"],
        longitude=st.session_state.map_center["lon"],
        zoom=st.session_state.map_center["zoom"],
        pitch=0,
        controller=True,
    )

    layer = pdk.Layer(
        "GeoJsonLayer",
        id="buildings",  # required for selection statefulness
        data=geo_data,
        pickable=True,
        stroked=True,
        filled=True,
        auto_highlight=True,
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
            {BoroughName}, NY {ZipcodeLabel}<br/>
            <hr/>
            <b>BBL:</b> {BBLLabel}<br/>
            <b>% Impact:</b> {ImpactPctLabel}<br/>
            <b>New Units:</b> {NewUnitsVal}<br/>
            <b>New Floors:</b> {NewFloorsVal}<br/>
            <b>New Building Height:</b> {NewHeightVal}
            """,
            "style": {
                "backgroundColor": "black",
                "color": "white",
                "fontSize": "12px",
                "padding": "6px",
            },
        },
        map_style="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
    )

    # Enable click selection on the map (Streamlit reruns and returns PydeckState)
    event = st.pydeck_chart(
        deck,
        use_container_width=True,
        on_select="rerun",
        selection_mode="single-object",
        key="airrights_map",
    )

    # Map click -> select property -> expand on right
    try:
        objs = event.selection.objects.get("buildings", [])
        if objs:
            # Streamlit returns metadata dicts; for GeoJsonLayer it includes the selected feature as "object"
            # We defensively check multiple possible shapes.
            obj0 = objs[0]
            feature = obj0.get("object", {}) or obj0
            props = feature.get("properties", {}) if isinstance(feature, dict) else {}
            picked_bbl = props.get("BBL") or props.get("BBLLabel")
            if picked_bbl:
                st.session_state.selected_bbl = str(picked_bbl)

                # Center map around the selected geometry if possible
                geom = feature.get("geometry")
                if geom:
                    # Use the first coordinate as a lightweight center fallback
                    if geom.get("type") == "Polygon":
                        lon, lat = geom["coordinates"][0][0]
                        st.session_state.map_center = {"lat": lat, "lon": lon, "zoom": 16}
                    elif geom.get("type") == "MultiPolygon":
                        lon, lat = geom["coordinates"][0][0][0]
                        st.session_state.map_center = {"lat": lat, "lon": lon, "zoom": 16}
    except Exception:
        pass

# =============================
# Right: Property List
# =============================
with col_list:
    st.subheader("Property List")

    # Optional: filter by approximate current map view
    list_source = filtered_gdf.copy()
    if st.session_state.use_map_filter:
        lat_c = st.session_state.map_center["lat"]
        lon_c = st.session_state.map_center["lon"]
        dz = zoom_to_delta(st.session_state.map_center["zoom"])

        list_source = list_source.dropna(subset=["Latitude", "Longitude"])
        list_source = list_source[
            (list_source["Latitude"].between(lat_c - dz, lat_c + dz))
            & (list_source["Longitude"].between(lon_c - dz, lon_c + dz))
        ]

    # Sort by ImpactPct desc, take top 10
    list_df = (
        list_source.sort_values("ImpactPct", ascending=False)
        .head(10)
        .copy()
    )

    st.caption(f"Top {len(list_df)} properties by % Impact")

    if len(list_df) == 0:
        st.info("No properties found.")
    else:
        # Two-column compact layout
        grid_cols = st.columns(2)

        for i, (_, row) in enumerate(list_df.iterrows()):
            with grid_cols[i % 2]:
                bbl = safe_get(row, "BBL", "N/A")
                address = safe_get(row, "Address", None)
                title = address if address and address != "N/A" else f"BBL {bbl}"

                zipcode = safe_get(row, "Zipcode", "N/A")
                subtitle = f"New York, NY {zipcode}"

                # Header row: title + locate button
                head_cols = st.columns([4, 1])
                with head_cols[0]:
                    st.markdown(f"**{title}**  \n{subtitle}")
                with head_cols[1]:
                    locate = st.button("📍 Locate", key=f"locate_{bbl}")

                if locate:
                    st.session_state.selected_bbl = str(bbl)
                    # Re-center map using lat/lon if available
                    lat = row.get("Latitude")
                    lon = row.get("Longitude")
                    if pd.notna(lat) and pd.notna(lon):
                        st.session_state.map_center = {"lat": float(lat), "lon": float(lon), "zoom": 16}
                    st.rerun()

                # Auto-expand when selected
                expanded_now = (st.session_state.selected_bbl is not None and str(bbl) == str(st.session_state.selected_bbl))

                with st.expander(f"{title} | {subtitle}", expanded=expanded_now):
                    # Display all fields in one pass (no "Core" vs "More")

                    info_row("BBL", safe_get(row, "BBL"))
                    info_row("Address", safe_get(row, "Address"))
                    info_row("Borough", safe_get(row, "Borough"))
                    info_row("Zipcode", safe_get(row, "Zipcode"))

                    info_row("% of New Units Impact", fmt_percent_label(row.get("ImpactPct")))
                    info_row("New Units", fmt_int(row.get("New Units")))
                    info_row("New Floors", fmt_int(row.get("New Floors")))
                    info_row("New Building Height", fmt_height_ft(row.get("New Building Height")))

                    # Air Rights should appear right after New Building Height
                    # Keep as-is if it already contains units like "sq ft"
                    info_row("Air Rights", safe_get(row, "Air Rights", "N/A"))

                    info_row("Residential Area", fmt_area_sqft(row.get("Residential Area")))
                    info_row("Commercial Area", fmt_area_sqft(row.get("Commercial Area")))
                    info_row("Units Residential", fmt_int(row.get("Units Residential")))
                    info_row("Units Commercial", fmt_int(row.get("Units Commercial")))
                    info_row("Units Total", fmt_int(row.get("Units Total")))

                    info_row("Year Built", fmt_int(row.get("Year Built")))
                    info_row("Zoning District 1", safe_get(row, "Zoning District 1", "N/A"))
                    info_row("Building Class", safe_get(row, "Building Class", "N/A"))
                    info_row("Owner", safe_get(row, "Owner", "N/A"))
