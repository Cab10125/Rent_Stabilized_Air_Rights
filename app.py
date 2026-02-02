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

if "view_mode" not in st.session_state:
    # "top10" or "single"
    st.session_state.view_mode = "top10"

if "near_center" not in st.session_state:
    # used for "Top 10 near this property"
    st.session_state.near_center = None

# -----------------------------
# Global Search (TOP)
# -----------------------------
search_mode = st.selectbox("Search by", ["Address", "ZIP Code", "Borough"])
st.caption("Tip: Use borough abbreviations. MN: Manhattan, BX: Bronx, BK: Brooklyn, QN: Queen")
search_query = st.text_input("Search", placeholder="Type to search…")

# -----------------------------
# Helper functions
# -----------------------------
def safe_get(row, key, default="N/A"):
    try:
        value = row.get(key)
        if pd.isna(value) or value is None:
            return default
        return value
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

def fmt_height(x):
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "N/A"
    try:
        return f"{int(round(float(x)))} ft"
    except Exception:
        return "N/A"

def fmt_area_sqft(x):
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "N/A"
    # If already a string like "24726 sqft", keep it.
    if isinstance(x, str):
        s = x.strip()
        return s if s else "N/A"
    try:
        return f"{int(round(float(x))):,} sq ft"
    except Exception:
        return "N/A"

def fmt_percent_from_ratio(ratio):
    # ratio: 0.92 means 92%
    if ratio is None or (isinstance(ratio, float) and pd.isna(ratio)):
        return "N/A"
    try:
        return f"{int(round(float(ratio) * 100))}%"
    except Exception:
        return "N/A"

def get_geojson_center(geom_geojson):
    """
    Return (lat, lon) focus point from GeoJSON string.
    Supports Polygon and MultiPolygon.
    Uses first coordinate as a stable proxy for focus.
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

def impact_to_color(impact_ratio):
    """
    Color buckets:
      1% - 30%    -> green
      30% - 60%   -> yellow
      60% - 100%  -> orange
      100% - 150% -> light red
      150%+       -> deep red
    impact_ratio is stored as 0.92 for 92%.
    """
    try:
        pct = float(impact_ratio) * 100.0
    except Exception:
        return [200, 200, 200]  # fallback gray

    if pct < 1:
        return [200, 200, 200]
    if pct < 30:
        return [0, 170, 0]        # green
    if pct < 60:
        return [245, 200, 0]      # yellow
    if pct < 100:
        return [255, 140, 0]      # orange
    if pct < 150:
        return [255, 90, 90]      # light red
    return [180, 0, 0]            # deep red

def get_color_with_selection(row):
    # Selected building -> highlight
    if st.session_state.selected_bbl is not None:
        if str(row.get("BBL", "")) == str(st.session_state.selected_bbl):
            return [0, 120, 255]  # highlight blue
    return row.get("impactColor", [200, 200, 200])

def info_row(label, value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        value = "N/A"
    st.markdown(
        f"""
        <div style="margin: 6px 0 14px 0;">
            <div style="font-size: 13px; color: #6b7280;">
                {label}
            </div>
            <div style="font-size: 16px; font-weight: 600; color: #111827;">
                {value}
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

def render_detail_two_columns(row):
    """
    Render all details in two columns.
    """
    fields = [
        ("Address", safe_get(row, "Address")),
        ("Borough", safe_get(row, "Borough")),
        ("Zipcode", safe_get(row, "Zipcode")),
        ("BBL", safe_get(row, "BBL")),

        ("% Impact", fmt_percent_from_ratio(safe_get(row, "% of New Units Impact", None))),
        ("New Units", fmt_int(safe_get(row, "New Units", None))),
        ("New Floors", fmt_float(safe_get(row, "New Floors", None), nd=2)),
        ("New Building Height", fmt_height(safe_get(row, "New Building Height", None))),
        ("Air Rights", fmt_area_sqft(safe_get(row, "Air Rights", None))),

        ("Residential Area", fmt_area_sqft(safe_get(row, "Residential Area", None))),
        ("Commercial Area", fmt_area_sqft(safe_get(row, "Commercial Area", None))),
        ("Units Residential", fmt_int(safe_get(row, "Units Residential", None))),
        ("Units Commercial", fmt_int(safe_get(row, "Units Commercial", None))),
        ("Units Total", fmt_int(safe_get(row, "Units Total", None))),

        ("Year Built", fmt_int(safe_get(row, "Year Built", None))),
        ("Zoning District 1", safe_get(row, "Zoning District 1")),
        ("Building Class", safe_get(row, "Building Class")),

        # Requested: keep these two at the bottom
        ("Existing Number of Floors", fmt_int(safe_get(row, "Existing Number of Floors", None))),
        ("Owner", safe_get(row, "Owner")),
    ]

    col1, col2 = st.columns(2)
    for i, (label, value) in enumerate(fields):
        with (col1 if i % 2 == 0 else col2):
            info_row(label, value)

def select_property(row):
    """
    Single source of truth for selecting a property.
    Both map click and Locate button call this.
    """
    bbl = str(safe_get(row, "BBL", None))
    if not bbl or bbl in ("None", "N/A"):
        return

    st.session_state.selected_bbl = bbl
    st.session_state.view_mode = "single"

    center = get_geojson_center(row.get("geom_geojson"))
    if center:
        st.session_state.map_center = {"lat": center[0], "lon": center[1], "zoom": 16}

def extract_clicked_bbl(selection):
    """
    Streamlit selection schema can vary by version.
    This function tries multiple patterns to locate clicked feature properties.
    """
    if not selection or not isinstance(selection, dict):
        return None

    objs = selection.get("objects")
    if objs is None:
        return None

    # Case A: objects is a list
    if isinstance(objs, list) and len(objs) > 0:
        obj0 = objs[0]
        props = obj0.get("properties", obj0)
        return props.get("BBL")

    # Case B: objects is a dict keyed by layer id
    if isinstance(objs, dict):
        for _, v in objs.items():
            if isinstance(v, list) and len(v) > 0:
                obj0 = v[0]
                props = obj0.get("properties", obj0)
                if "BBL" in props:
                    return props.get("BBL")

    return None

def zoom_from_span(span):
    """
    Heuristic zoom based on lat/lon span.
    """
    if span <= 0.01:
        return 16
    if span <= 0.03:
        return 15
    if span <= 0.06:
        return 14
    if span <= 0.12:
        return 13
    if span <= 0.25:
        return 12
    return 11

def set_map_view_from_points(df_points, fallback_zoom=12):
    """
    Set map center/zoom based on Latitude/Longitude bounds.
    """
    if df_points is None or len(df_points) == 0:
        return

    if "Latitude" not in df_points.columns or "Longitude" not in df_points.columns:
        return

    lat = pd.to_numeric(df_points["Latitude"], errors="coerce")
    lon = pd.to_numeric(df_points["Longitude"], errors="coerce")
    dfv = pd.DataFrame({"lat": lat, "lon": lon}).dropna()
    if len(dfv) == 0:
        return

    lat_min, lat_max = dfv["lat"].min(), dfv["lat"].max()
    lon_min, lon_max = dfv["lon"].min(), dfv["lon"].max()
    center_lat = (lat_min + lat_max) / 2.0
    center_lon = (lon_min + lon_max) / 2.0

    span = max(abs(lat_max - lat_min), abs(lon_max - lon_min))
    z = zoom_from_span(span) if span > 0 else fallback_zoom

    st.session_state.map_center = {"lat": float(center_lat), "lon": float(center_lon), "zoom": int(z)}

def parse_zip_tokens(q):
    """
    Extract zip tokens from input.
    Accepts comma/space separated values.
    """
    raw = q.replace(",", " ").split()
    tokens = []
    for t in raw:
        t = "".join([c for c in t if c.isdigit()])
        if len(t) == 5:
            tokens.append(t)
    return list(dict.fromkeys(tokens))

def parse_borough_tokens(q):
    """
    Extract borough tokens from input.
    """
    raw = q.replace(",", " ").split()
    tokens = [t.strip().upper() for t in raw if t.strip()]
    return list(dict.fromkeys(tokens))

def resolve_zip_fallbacks(requested_zips, available_zips_sorted):
    """
    Fallback for missing ZIPs:
    choose closest by numeric difference among available zips.
    Returns (resolved, replaced_map).
    """
    replaced = {}
    resolved = []

    if len(available_zips_sorted) == 0:
        return [], {z: None for z in requested_zips}

    avail_int = np.array([int(z) for z in available_zips_sorted], dtype=int)

    for z in requested_zips:
        zi = int(z)
        idx = int(np.argmin(np.abs(avail_int - zi)))
        best = available_zips_sorted[idx]
        if best != z:
            replaced[z] = best
        resolved.append(best)

    # unique preserve order
    resolved = list(dict.fromkeys(resolved))
    return resolved, replaced

def filter_for_zip_mode(df, requested_zips):
    """
    Apply ZIP filter with fallback rules:
    - If ZIP not found, fallback to closest available ZIP (numeric).
    - If ZIP found but all properties are gray (<1%), fallback to closest ZIP that has at least one colored property.
    Returns (filtered_df, notices).
    """
    notices = []

    df = df.copy()
    df["ZipcodeStr"] = df["Zipcode"].astype(str).str.zfill(5)
    df["ImpactRatio"] = pd.to_numeric(df["% of New Units Impact"], errors="coerce").fillna(0)

    available_zips = sorted(df["ZipcodeStr"].dropna().unique().tolist())
    colored_zips = sorted(
        df.loc[df["ImpactRatio"] >= 0.01, "ZipcodeStr"].dropna().unique().tolist()
    )

    # First: resolve missing zips against all available zips
    resolved_zips, replaced_missing = resolve_zip_fallbacks(requested_zips, available_zips)

    # Second: for each resolved zip, if it has no colored property, fallback to closest colored zip
    final_zips = []
    replaced_nocolor = {}

    colored_int = np.array([int(z) for z in colored_zips], dtype=int) if len(colored_zips) > 0 else np.array([], dtype=int)
    for z in resolved_zips:
        in_zip = df[df["ZipcodeStr"] == z]
        if len(in_zip) == 0:
            continue
        if (in_zip["ImpactRatio"] >= 0.01).any():
            final_zips.append(z)
        else:
            if len(colored_zips) == 0:
                final_zips.append(z)
            else:
                zi = int(z)
                idx = int(np.argmin(np.abs(colored_int - zi)))
                best = colored_zips[idx]
                replaced_nocolor[z] = best
                final_zips.append(best)

    final_zips = list(dict.fromkeys(final_zips))

    if len(replaced_missing) > 0:
        items = [f"{k} → {v}" for k, v in replaced_missing.items() if v is not None]
        if len(items) > 0:
            notices.append("ZIP not found. Using closest available ZIP instead: " + ", ".join(items))

    if len(replaced_nocolor) > 0:
        items = [f"{k} → {v}" for k, v in replaced_nocolor.items()]
        if len(items) > 0:
            notices.append("ZIP found but no colored properties (<1%). Using closest ZIP with colored properties: " + ", ".join(items))

    if len(final_zips) == 0:
        return df.iloc[0:0], notices

    filtered = df[df["ZipcodeStr"].isin(final_zips)].copy()
    return filtered, notices

def filter_for_borough_mode(df, borough_tokens):
    """
    Borough abbreviations: MN, BX, BK, QN.
    Assumes dataset Borough is abbreviation already (as shown in tooltip).
    """
    df = df.copy()
    df["BoroughStr"] = df["Borough"].astype(str).str.upper()

    valid = {"MN", "BX", "BK", "QN"}
    picked = [b for b in borough_tokens if b in valid]
    if len(picked) == 0:
        # Fallback to substring match if user typed full text
        q = " ".join(borough_tokens).strip().lower()
        return df[df["Borough"].fillna("").str.lower().str.contains(q, na=False)].copy(), []

    filtered = df[df["BoroughStr"].isin(picked)].copy()
    return filtered, []

# -----------------------------
# Load data
# -----------------------------
@st.cache_data(show_spinner=True)
def load_data():
    engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)

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

            "# of Floors" AS existing_floors,

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

    with engine.connect() as conn:
        df = pd.read_sql_query(text(query), conn)

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
            "existing_floors": "Existing Number of Floors",
            "latitude": "Latitude",
            "longitude": "Longitude",
        }
    )

    return df

gdf = load_data()

# Ensure correct dtypes
gdf["New Units"] = pd.to_numeric(gdf["New Units"], errors="coerce").fillna(0)
gdf["% of New Units Impact"] = pd.to_numeric(gdf["% of New Units Impact"], errors="coerce").fillna(0)
gdf["Existing Number of Floors"] = pd.to_numeric(gdf["Existing Number of Floors"], errors="coerce")

# Precompute color by impact
gdf["impactColor"] = gdf["% of New Units Impact"].apply(impact_to_color)

# -----------------------------
# Build a shared filtered view (used by BOTH map and list) in Top10 mode
# -----------------------------
active_df = gdf.copy()
search_notices = []

if st.session_state.view_mode == "top10" and search_query.strip():
    q = search_query.strip()

    if search_mode == "Borough":
        borough_tokens = parse_borough_tokens(q)
        active_df, notes = filter_for_borough_mode(active_df, borough_tokens)
        search_notices.extend(notes)
        # Zoom map to this borough
        set_map_view_from_points(active_df, fallback_zoom=12)

    elif search_mode == "ZIP Code":
        zips = parse_zip_tokens(q)
        active_df, notes = filter_for_zip_mode(active_df, zips)
        search_notices.extend(notes)
        # Zoom map to the selected zip(s)
        set_map_view_from_points(active_df, fallback_zoom=12)

    elif search_mode == "Address":
        # Keep full map, but list filter will apply later.
        # Optionally focus map on first match.
        matches = active_df[active_df["Address"].fillna("").str.lower().str.contains(q.lower(), na=False)]
        if len(matches) > 0:
            # Focus around matched points (not filtering the entire map)
            set_map_view_from_points(matches, fallback_zoom=15)

# Default focus (only if user did not search and did not select)
if st.session_state.view_mode == "top10" and (not search_query.strip()):
    try:
        top_idx = gdf["% of New Units Impact"].astype(float).idxmax()
        top_row = gdf.loc[top_idx]
        top_center = get_geojson_center(top_row.get("geom_geojson"))
        if top_center:
            if st.session_state.map_center == {"lat": 40.7549, "lon": -73.9840, "zoom": 12}:
                st.session_state.map_center = {"lat": top_center[0], "lon": top_center[1], "zoom": 15}
    except Exception:
        pass

# -----------------------------
# Main layout
# -----------------------------
col_map, col_list = st.columns([5, 5])

# =============================
# Left: Map
# =============================
with col_map:
    st.subheader("Interactive Map")

    if st.button("Update Top 10 from current map view"):
        st.session_state.use_map_filter = True
        st.session_state.view_mode = "top10"
        st.session_state.near_center = None

    # Map uses filtered dataset for Borough/ZIP searches in Top10 mode
    map_df = active_df.copy() if st.session_state.view_mode == "top10" else gdf.copy()

    gdf_map = map_df.copy()
    gdf_map["BBL"] = gdf_map["BBL"].astype(str)

    gdf_map["AddressName"] = gdf_map["Address"].fillna("N/A")
    gdf_map["BoroughName"] = gdf_map["Borough"].fillna("N/A")
    gdf_map["ZipcodeStr"] = gdf_map["Zipcode"].astype(str).str.zfill(5)

    gdf_map["ImpactPctStr"] = gdf_map["% of New Units Impact"].apply(fmt_percent_from_ratio)
    gdf_map["NewUnitsNum"] = pd.to_numeric(gdf_map["New Units"], errors="coerce").fillna(0).astype(int)
    gdf_map["NewFloorsNum"] = pd.to_numeric(gdf_map["New Floors"], errors="coerce").fillna(0)
    gdf_map["NewHeightNum"] = pd.to_numeric(gdf_map["New Building Height"], errors="coerce").fillna(0)

    gdf_map["ExistingFloorsNum"] = pd.to_numeric(
        gdf_map["Existing Number of Floors"], errors="coerce"
    ).fillna(np.nan)
    gdf_map["OwnerNameStr"] = gdf_map["Owner"].fillna("N/A")

    gdf_map["fillColor"] = gdf_map.apply(get_color_with_selection, axis=1)

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

    view_state = pdk.ViewState(
        latitude=st.session_state.map_center["lat"],
        longitude=st.session_state.map_center["lon"],
        zoom=st.session_state.map_center["zoom"],
        pitch=0,
    )

    layer = pdk.Layer(
        "GeoJsonLayer",
        data=geo_data,
        id="buildings",
        pickable=True,
        stroked=True,
        filled=True,
        get_fill_color="properties.fillColor",
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
            <b>% Impact:</b> {ImpactPctStr}<br/>
            <b>New Units:</b> {NewUnitsNum}<br/>
            <b>New Floors:</b> {NewFloorsNum}<br/>
            <b>New Building Height:</b> {NewHeightNum}<br/>
            <b>Existing Number of Floors:</b> {ExistingFloorsNum}<br/>
            <b>Owner:</b> {OwnerNameStr}
            """,
            "style": {"backgroundColor": "black", "color": "white", "fontSize": "12px", "padding": "8px"},
        },
        map_style="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
    )

    chart = st.pydeck_chart(
        deck,
        use_container_width=True,
        on_select="rerun",
        selection_mode="single-object",
    )

    # Make map click behave exactly like Locate
    try:
        sel = getattr(chart, "selection", None)
        clicked_bbl = extract_clicked_bbl(sel)
        if clicked_bbl is not None:
            clicked_bbl = str(clicked_bbl)
            match = gdf[gdf["BBL"].astype(str) == clicked_bbl]
            if len(match) > 0:
                select_property(match.iloc[0])
                st.rerun()
    except Exception:
        pass

# =============================
# Right: Property list / Single property
# =============================
with col_list:
    st.subheader("Property List")

    # CSS: Stabilize the "Locate on map" button to avoid weird spacing
    # Target only locate buttons by title attribute.
    st.markdown(
        """
        <style>
        button[title="Locate on map"] {
            width: 34px !important;
            height: 34px !important;
            min-width: 34px !important;
            padding: 0 !important;
            margin: 0 !important;
            border-radius: 8px !important;

            display: inline-flex !important;
            align-items: center !important;
            justify-content: center !important;

            font-size: 18px !important;
            line-height: 1 !important;
        }

        button[title="Locate on map"] span {
            display: inline-flex !important;
            align-items: center !important;
            justify-content: center !important;
            width: 100% !important;
            height: 100% !important;
            margin: 0 !important;
            padding: 0 !important;
        }
        </style>
        """,
        unsafe_allow_html=True
    )

    # Show notices for ZIP fallbacks
    for msg in search_notices:
        st.warning(msg)

    if st.session_state.view_mode == "single" and st.session_state.selected_bbl is not None:
        sel_bbl = str(st.session_state.selected_bbl)
        match = gdf[gdf["BBL"].astype(str) == sel_bbl]

        if len(match) == 0:
            st.warning("Selected property was not found in the dataset.")
        else:
            row = match.iloc[0]
            title = safe_get(row, "Address", f"BBL {sel_bbl}")
            zipcode = safe_get(row, "Zipcode", "N/A")
            st.markdown(f"### {title}")
            st.caption(f"New York, NY {zipcode}")

            b1, b2 = st.columns([1, 1])
            with b1:
                if st.button("Show Top 10 near this property"):
                    center = get_geojson_center(row.get("geom_geojson"))
                    if center:
                        st.session_state.map_center = {"lat": center[0], "lon": center[1], "zoom": 15}
                        st.session_state.near_center = center
                        st.session_state.use_map_filter = True
                    st.session_state.view_mode = "top10"
                    st.rerun()

            with b2:
                if st.button("Back to global Top 10"):
                    st.session_state.view_mode = "top10"
                    st.session_state.use_map_filter = False
                    st.session_state.near_center = None
                    st.session_state.selected_bbl = None
                    st.rerun()

            st.markdown("---")
            render_detail_two_columns(row)

    else:
        # List uses the same filtered dataset as the map (Top10 mode)
        filtered = active_df.copy()

        # Address search should filter list results even if map is not filtered
        if search_query.strip() and search_mode == "Address":
            q = search_query.strip().lower()
            filtered = filtered[filtered["Address"].fillna("").str.lower().str.contains(q, na=False)]

        # Optional "near center" filter
        if st.session_state.use_map_filter and st.session_state.near_center is not None:
            lat0, lon0 = st.session_state.near_center
            if "Latitude" in filtered.columns and "Longitude" in filtered.columns:
                filtered = filtered[
                    filtered["Latitude"].between(lat0 - 0.02, lat0 + 0.02)
                    & filtered["Longitude"].between(lon0 - 0.02, lon0 + 0.02)
                ]

        filtered["% of New Units Impact"] = pd.to_numeric(filtered["% of New Units Impact"], errors="coerce").fillna(0)
        top10 = filtered.sort_values("% of New Units Impact", ascending=False).head(10)

        st.caption(f"Top {len(top10)} properties by % Impact")

        left_col, right_col = st.columns(2)

        for i, (_, r) in enumerate(top10.iterrows()):
            container = left_col if i % 2 == 0 else right_col

            bbl = str(safe_get(r, "BBL", "N/A"))
            addr = safe_get(r, "Address", f"BBL {bbl}")
            zc = safe_get(r, "Zipcode", "N/A")
            impact = fmt_percent_from_ratio(safe_get(r, "% of New Units Impact", None))

            with container:
                # Make the button column narrow to avoid "floating" look
                header_cols = st.columns([12, 0.6], gap="small")

                with header_cols[0]:
                    st.markdown(f"**{addr}**  \nNew York, NY {zc}  \n% Impact: {impact}")

                with header_cols[1]:
                    if st.button("📍", key=f"locate_{bbl}", help="Locate on map"):
                        select_property(r)
                        st.rerun()

                with st.expander("Details"):
                    render_detail_two_columns(r)
