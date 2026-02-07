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
# Minimal UI styles
# -----------------------------
st.markdown(
    """
    <style>
      .card-title{
        font-size: 22px;
        font-weight: 800;
        color: #111827;
        line-height: 1.1;
        margin: 2px 0 6px 0;
      }
      .card-sub{
        font-size: 16px;
        color: #374151;
        margin: 0 0 8px 0;
      }
      .card-metric{
        font-size: 16px;
        color: #111827;
        margin: 0 0 4px 0;
      }
      div[data-testid="stExpander"] > details{
        border-radius: 12px;
      }
    </style>
    """,
    unsafe_allow_html=True
)

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
    st.session_state.view_mode = "top10"  # "top10" or "single"

if "near_center" not in st.session_state:
    st.session_state.near_center = None

# =============================
# Global Search (TOP)
# =============================
search_mode = st.selectbox("Search by", ["Address", "ZIP Code", "Borough"])
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

def fmt_percent_auto(x):
    """
    Robust percent formatter:
    - if 0~1.5 -> treat as ratio (0.25 -> 25%)
    - if >1.5 -> treat as already percent (25 -> 25%)
    """
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "N/A"
    try:
        v = float(x)
        if v <= 1.5:
            return f"{int(round(v * 100))}%"
        return f"{int(round(v))}%"
    except Exception:
        return "N/A"

def get_geojson_center(geom_geojson):
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
    try:
        pct = float(impact_ratio) * 100.0
    except Exception:
        return [200, 200, 200]

    if pct < 1:
        return [200, 200, 200]
    if pct < 30:
        return [0, 170, 0]
    if pct < 60:
        return [245, 200, 0]
    if pct < 100:
        return [255, 140, 0]
    if pct < 150:
        return [255, 90, 90]
    return [180, 0, 0]

def get_color_with_selection(row):
    if st.session_state.selected_bbl is not None:
        if str(row.get("BBL", "")) == str(st.session_state.selected_bbl):
            return [0, 120, 255]
    return row.get("impactColor", [200, 200, 200])

def info_row(label, value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        value = "N/A"
    st.markdown(
        f"""
        <div style="margin: 6px 0 14px 0;">
            <div style="font-size: 13px; color: #6b7280;">{label}</div>
            <div style="font-size: 16px; font-weight: 600; color: #111827;">{value}</div>
        </div>
        """,
        unsafe_allow_html=True
    )

def render_detail_two_columns(row):
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

        # NEW: add these two into Details
        ("Stabilized Units Number", fmt_int(safe_get(row, "Stabilized Units Number", None))),
        ("% stabilized", fmt_percent_auto(safe_get(row, "% stabilized", None))),

        ("Year Built", fmt_int(safe_get(row, "Year Built", None))),
        ("Zoning District 1", safe_get(row, "Zoning District 1")),
        ("Building Class", safe_get(row, "Building Class")),

        ("Existing Number of Floors", fmt_int(safe_get(row, "Existing Number of Floors", None))),
        ("Owner", safe_get(row, "Owner")),
    ]

    col1, col2 = st.columns(2)
    for i, (label, value) in enumerate(fields):
        with (col1 if i % 2 == 0 else col2):
            info_row(label, value)

def select_property(row):
    bbl = str(safe_get(row, "BBL", None))
    if not bbl or bbl in ["None", "N/A"]:
        return

    st.session_state.selected_bbl = bbl
    st.session_state.view_mode = "single"

    center = get_geojson_center(row.get("geom_geojson"))
    if center:
        st.session_state.map_center = {"lat": center[0], "lon": center[1], "zoom": 16}

def extract_clicked_bbl(selection):
    if not selection or not isinstance(selection, dict):
        return None

    objs = selection.get("objects")
    if objs is None:
        return None

    if isinstance(objs, list) and len(objs) > 0:
        obj0 = objs[0]
        props = obj0.get("properties", obj0)
        return props.get("BBL")

    if isinstance(objs, dict):
        for _, v in objs.items():
            if isinstance(v, list) and len(v) > 0:
                obj0 = v[0]
                props = obj0.get("properties", obj0)
                if "BBL" in props:
                    return props.get("BBL")

    return None

def locate_icon_link(bbl: str) -> str:
    return f"""
    <div style="display:flex; justify-content:flex-end; align-items:flex-start;">
      <a href="?locate={bbl}"
         title="Locate on map"
         style="
            width:32px; height:32px;
            display:flex; align-items:center; justify-content:center;
            border:1px solid #e5e7eb;
            border-radius:8px;
            background:#ffffff;
            text-decoration:none;
            box-shadow:0 1px 0 rgba(0,0,0,0.02);
         ">
        <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
          <path fill="#d22"
                d="M12 22s7-7.2 7-12.2C19 6 16.1 3 12 3S5 6 5 9.8C5 14.8 12 22 12 22z"/>
          <circle cx="12" cy="9.8" r="2.5" fill="#ffffff"/>
          <circle cx="12" cy="9.8" r="1.2" fill="#d22"/>
        </svg>
      </a>
    </div>
    """

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

            -- NEW columns (exact DB names)
            "stabilized units" AS stabilized_units,
            "% stabilized" AS pct_stabilized,

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
            "stabilized_units": "Stabilized Units Number",
            "pct_stabilized": "% stabilized",
            "latitude": "Latitude",
            "longitude": "Longitude",
        }
    )

    return df

gdf = load_data()

# -----------------------------
# Handle locate click via query params
# -----------------------------
def _get_query_param(name: str):
    try:
        return st.query_params.get(name)
    except Exception:
        qp = st.experimental_get_query_params()
        v = qp.get(name, [None])
        return v[0] if isinstance(v, list) else v

def _clear_query_params():
    try:
        st.query_params.clear()
    except Exception:
        st.experimental_set_query_params()

locate_bbl = _get_query_param("locate")
if locate_bbl:
    locate_bbl = str(locate_bbl)
    match = gdf[gdf["BBL"].astype(str) == locate_bbl]
    if len(match) > 0:
        select_property(match.iloc[0])
    _clear_query_params()
    st.rerun()

# Ensure correct dtypes
gdf["New Units"] = pd.to_numeric(gdf["New Units"], errors="coerce").fillna(0)
gdf["% of New Units Impact"] = pd.to_numeric(gdf["% of New Units Impact"], errors="coerce").fillna(0)
gdf["Existing Number of Floors"] = pd.to_numeric(gdf["Existing Number of Floors"], errors="coerce")

# NEW dtypes
if "Stabilized Units Number" in gdf.columns:
    gdf["Stabilized Units Number"] = pd.to_numeric(gdf["Stabilized Units Number"], errors="coerce").fillna(0)
if "% stabilized" in gdf.columns:
    gdf["% stabilized"] = pd.to_numeric(gdf["% stabilized"], errors="coerce")

# Precompute color by impact
gdf["impactColor"] = gdf["% of New Units Impact"].apply(impact_to_color)

# Default focus: highest impact property
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

    gdf_map = gdf.copy()
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

    # NEW tooltip numbers
    gdf_map["StabilizedUnitsNum"] = pd.to_numeric(
        gdf_map.get("Stabilized Units Number", 0), errors="coerce"
    ).fillna(0).astype(int)

    # Residential units = your existing "Units Residential"
    gdf_map["ResidentialUnitsNum"] = pd.to_numeric(
        gdf_map.get("Units Residential", np.nan), errors="coerce"
    ).fillna(np.nan)

    gdf_map["OwnerNameStr"] = gdf_map.get("Owner", "N/A").fillna("N/A")
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
            <b>Stabilized Units Number:</b> {StabilizedUnitsNum}<br/>
            <b>Residential Units:</b> {ResidentialUnitsNum}<br/>
            <b>Owner:</b> {OwnerNameStr}
            """,
            "style": {
                "backgroundColor": "black",
                "color": "white",
                "fontSize": "12px",
                "padding": "8px",
            },
        },
        map_style="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
    )

    chart = st.pydeck_chart(
        deck,
        use_container_width=True,
        on_select="rerun",
        selection_mode="single-object",
    )

    try:
        sel = getattr(chart, "selection", None)
        clicked_bbl = extract_clicked_bbl(sel)
        if clicked_bbl is not None:
            clicked_bbl = str(clicked_bbl)
            match = gdf[gdf["BBL"].astype(str) == clicked_bbl]
            if len(match) > 0:
                select_property(match.iloc[0])
    except Exception:
        pass

# =============================
# Right: Property list / Single property
# =============================
with col_list:
    st.subheader("Property List")

    if st.session_state.view_mode == "single" and st.session_state.selected_bbl is not None:
        sel_bbl = str(st.session_state.selected_bbl)
        match = gdf[gdf["BBL"].astype(str) == sel_bbl]

        if len(match) == 0:
            st.warning("Selected property was not found in the dataset.")
        else:
            row = match.iloc[0]
            title = safe_get(row, "Address", f"BBL {sel_bbl}")
            zipcode = safe_get(row, "Zipcode", "N/A")

            st.markdown(f'<div class="card-title">{title}</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="card-sub">New York, NY {zipcode}</div>', unsafe_allow_html=True)

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
        filtered = gdf.copy()

        if search_query:
            q = search_query.strip().lower()

            if search_mode == "Address":
                filtered = filtered[filtered["Address"].fillna("").str.lower().str.contains(q, na=False)]

            elif search_mode == "ZIP Code":
                tokens = [t for t in q.replace(",", " ").split() if t]
                if len(tokens) > 0:
                    z = filtered["Zipcode"].astype(str).str.zfill(5)
                    filtered = filtered[z.isin([t.zfill(5) for t in tokens])]

            elif search_mode == "Borough":
                filtered = filtered[filtered["Borough"].fillna("").str.lower().str.contains(q, na=False)]

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
                header_cols = st.columns([14, 1.2])

                with header_cols[0]:
                    st.markdown(f'<div class="card-title">{addr}</div>', unsafe_allow_html=True)
                    st.markdown(f'<div class="card-sub">New York, NY {zc}</div>', unsafe_allow_html=True)
                    st.markdown(f'<div class="card-metric">% Impact: {impact}</div>', unsafe_allow_html=True)

                with header_cols[1]:
                    st.markdown(locate_icon_link(bbl), unsafe_allow_html=True)

                with st.expander("Details"):
                    render_detail_two_columns(r)
