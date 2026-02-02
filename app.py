import os
import json
import numpy as np
import pandas as pd
import streamlit as st
import pydeck as pdk
from sqlalchemy import create_engine, text

# -----------------------------
# Page config
# -----------------------------
st.set_page_config(page_title="NYC Air Rights Explorer", layout="wide")

# -----------------------------
# Global CSS
# -----------------------------
st.markdown(
    """
    <style>
    /* Make the icon-only Locate button compact and square-ish */
    button[kind="secondary"] {
        padding: 0.2rem 0.45rem !important;
        min-height: 32px !important;
        min-width: 32px !important;
        border-radius: 8px !important;
        line-height: 1 !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
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

if "show_single" not in st.session_state:
    st.session_state.show_single = False  # True -> right panel shows only the selected property

if "map_center_initialized" not in st.session_state:
    st.session_state.map_center_initialized = False

# -----------------------------
# Title
# -----------------------------
st.title("NYC Air Rights Explorer")

# =============================
# Global Search (TOP)
# =============================
search_mode = st.selectbox("Search by", ["Address", "ZIP Code(s)", "Borough"])
search_query = st.text_input("Search", placeholder="Type to search…")

st.caption(
    "Tip: Use borough abbreviations for Borough search (MN: Manhattan, BX: Bronx, BK: Brooklyn, QN: Queens)."
)

# -----------------------------
# Helper functions
# -----------------------------
def to_num(x, default=np.nan):
    try:
        if x is None or (isinstance(x, float) and np.isnan(x)):
            return default
        return float(x)
    except Exception:
        return default


def fmt_int(x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "N/A"
    try:
        return f"{int(round(float(x))):,}"
    except Exception:
        return "N/A"


def fmt_height(x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "N/A"
    try:
        return f"{int(round(float(x)))} ft"
    except Exception:
        return "N/A"


def fmt_area_sqft(x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "N/A"
    try:
        return f"{int(round(float(x))):,} sq ft"
    except Exception:
        return "N/A"


def fmt_percent_from_ratio(ratio):
    """
    ratio is stored like 0.92 -> display "92%"
    """
    if ratio is None or (isinstance(ratio, float) and np.isnan(ratio)):
        return "N/A"
    try:
        return f"{int(round(float(ratio) * 100))}%"
    except Exception:
        return "N/A"


def info_row(label, value):
    if value is None or (isinstance(value, float) and np.isnan(value)):
        value = "N/A"

    st.markdown(
        f"""
        <div style="margin: 6px 0 14px 0;">
            <div style="font-size: 13px; color: #6b7280;">
                {label}
            </div>
            <div style="font-size: 16px; font-weight: 500; color: inherit;">
                {value}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def impact_to_color_rgb(impact_pct):
    """
    Color rules (based on percent):
      1–30   -> green
      30–60  -> yellow
      60–100 -> orange
      100–150-> light red
      150+   -> deep red
    """
    x = to_num(impact_pct, default=np.nan)
    if np.isnan(x):
        return [200, 200, 200]

    if x < 1:
        return [200, 200, 200]
    if 1 <= x < 30:
        return [34, 197, 94]      # green
    if 30 <= x < 60:
        return [250, 204, 21]     # yellow
    if 60 <= x < 100:
        return [249, 115, 22]     # orange
    if 100 <= x < 150:
        return [248, 113, 113]    # light red
    return [220, 38, 38]          # deep red


def get_geojson_center(geom_geojson):
    """
    Return (lat, lon) from a GeoJSON string (Polygon or MultiPolygon).
    """
    try:
        geom = json.loads(geom_geojson)
        if geom.get("type") == "Polygon":
            lon, lat = geom["coordinates"][0][0]
            return lat, lon
        if geom.get("type") == "MultiPolygon":
            lon, lat = geom["coordinates"][0][0][0]
            return lat, lon
    except Exception:
        return None
    return None


def get_geojson_bounds(geom_geojson):
    """
    Return (min_lon, min_lat, max_lon, max_lat) from a GeoJSON string.
    This is used to approximate a fit-to-bounds view for multi-zip searches.
    """
    try:
        geom = json.loads(geom_geojson)
        coords = []

        def collect_points(obj):
            if isinstance(obj, (list, tuple)):
                # Point: [lon, lat]
                if len(obj) == 2 and isinstance(obj[0], (int, float)) and isinstance(obj[1], (int, float)):
                    coords.append((obj[0], obj[1]))
                else:
                    for it in obj:
                        collect_points(it)

        collect_points(geom.get("coordinates", []))
        if not coords:
            return None

        lons = [c[0] for c in coords]
        lats = [c[1] for c in coords]
        return (min(lons), min(lats), max(lons), max(lats))
    except Exception:
        return None


def bbox_to_zoom(lon_span, lat_span):
    """
    Heuristic zoom chooser based on span.
    """
    span = max(lon_span, lat_span)
    if span > 0.30:
        return 10
    if span > 0.15:
        return 11
    if span > 0.08:
        return 12
    if span > 0.04:
        return 13
    if span > 0.02:
        return 14
    return 15


def select_property(row):
    """
    Centralized selection behavior:
    - set selected_bbl
    - show single-property view on right
    - move map center to the selected geometry
    """
    st.session_state.selected_bbl = str(row.get("BBL", "N/A"))
    st.session_state.show_single = True

    gj = row.get("geom_geojson")
    if gj:
        center = get_geojson_center(gj)
        if center:
            st.session_state.map_center = {"lat": center[0], "lon": center[1], "zoom": 16}


def select_property_by_bbl(df, bbl_value):
    """
    Select property by BBL value from df.
    """
    if bbl_value is None:
        return
    bbl_value = str(bbl_value)

    hit = df[df["BBL"].astype(str) == bbl_value]
    if len(hit) == 0:
        return
    select_property(hit.iloc[0].to_dict())


# -----------------------------
# Load data (PostGIS -> DataFrame)
# -----------------------------
@st.cache_data(show_spinner=True)
def load_data():
    db_url = os.environ["DATABASE_URL"]
    engine = create_engine(db_url)

    # NOTE: Use sqlalchemy.text + explicit connection to avoid the immutabledict error.
    query = text(
        """
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

            "Residential Area",
            "Commercial Area",
            "Units Residential",
            "Units Commercial",
            "Units Total",

            "Year Built",
            "FAR Built",
            "FAR Residential",
            "FAR Commercial",

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
    )

    with engine.connect() as conn:
        df = pd.read_sql(query, conn)

    # Rename to front-end names
    df = df.rename(
        columns={
            "BBL_10": "BBL",
            "Borough_x": "Borough",
            "Address_x": "Address",

            "ZoneDist1": "Zoning District 1",
            "BldgClass": "Building Class",
            "OwnerName": "Owner",

            "# of Floors": "Number of Existing Floors",
        }
    )

    return df


gdf = load_data()

# -----------------------------
# Data preparation
# -----------------------------
# Numeric conversions
gdf["New Units"] = pd.to_numeric(gdf["New Units"], errors="coerce").fillna(0)
gdf["New Floors"] = pd.to_numeric(gdf["New Floors"], errors="coerce")
gdf["New Building Height"] = pd.to_numeric(gdf["New Building Height"], errors="coerce")
gdf["Number of Existing Floors"] = pd.to_numeric(gdf["Number of Existing Floors"], errors="coerce")

# Impact ratio -> percent
gdf["ImpactRatio"] = pd.to_numeric(gdf["% of New Units Impact"], errors="coerce")
gdf["ImpactPct"] = (gdf["ImpactRatio"] * 100.0).replace([np.inf, -np.inf], np.nan)

# Fill color based on ImpactPct
gdf["colorRGB"] = gdf["ImpactPct"].apply(impact_to_color_rgb)

# Default map focus: center near the highest-impact property (only once)
if not st.session_state.map_center_initialized and len(gdf) > 0:
    top_row = gdf.sort_values("ImpactPct", ascending=False).iloc[0]
    center = get_geojson_center(top_row.get("geom_geojson"))
    if center:
        st.session_state.map_center = {"lat": center[0], "lon": center[1], "zoom": 15}
    st.session_state.map_center_initialized = True

# -----------------------------
# Apply search filters
# -----------------------------
filtered = gdf.copy()

if search_query:
    q = search_query.strip()

    if search_mode == "Address":
        filtered = filtered[filtered["Address"].astype(str).str.lower().str.contains(q.lower(), na=False)]

    elif search_mode == "Borough":
        # Expect abbreviations (MN/BX/BK/QN)
        filtered = filtered[filtered["Borough"].astype(str).str.upper().str.contains(q.upper(), na=False)]

    elif search_mode == "ZIP Code(s)":
        # Multi-zip supported: "10012, 10025 10038"
        tokens = (
            q.replace(",", " ")
            .replace(";", " ")
            .split()
        )
        zips = [t.strip() for t in tokens if t.strip()]
        zips = [z.zfill(5) for z in zips if z.isdigit() and len(z) <= 5]

        if zips:
            filtered = filtered[filtered["Zipcode"].astype(str).str.zfill(5).isin(zips)]

            # Auto-zoom to the minimum bounds containing the selected ZIP(s)
            bounds = []
            for gj in filtered["geom_geojson"].dropna().tolist():
                b = get_geojson_bounds(gj)
                if b:
                    bounds.append(b)

            if bounds:
                min_lon = min(b[0] for b in bounds)
                min_lat = min(b[1] for b in bounds)
                max_lon = max(b[2] for b in bounds)
                max_lat = max(b[3] for b in bounds)

                center_lat = (min_lat + max_lat) / 2.0
                center_lon = (min_lon + max_lon) / 2.0
                zoom = bbox_to_zoom(max_lon - min_lon, max_lat - min_lat)

                st.session_state.map_center = {"lat": center_lat, "lon": center_lon, "zoom": zoom}

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
        st.session_state.show_single = False

    # Prepare GeoJSON FeatureCollection for pydeck
    interactive_df = filtered.copy()

    # Add tooltip-safe fields (no spaces)
    interactive_df["AddressName"] = interactive_df["Address"].fillna("N/A")
    interactive_df["Zip5"] = interactive_df["Zipcode"].astype(str).str.zfill(5).fillna("N/A")
    interactive_df["BoroughName"] = interactive_df["Borough"].fillna("N/A")
    interactive_df["BBLstr"] = interactive_df["BBL"].astype(str).fillna("N/A")

    interactive_df["ImpactPctInt"] = pd.to_numeric(interactive_df["ImpactPct"], errors="coerce")
    interactive_df["ImpactPctLabel"] = interactive_df["ImpactPctInt"].apply(lambda x: "N/A" if pd.isna(x) else f"{int(round(x))}%")

    interactive_df["NewUnitsNum"] = pd.to_numeric(interactive_df["New Units"], errors="coerce").fillna(0).astype(int)
    interactive_df["NewFloorsNum"] = pd.to_numeric(interactive_df["New Floors"], errors="coerce")
    interactive_df["NewHeightNum"] = pd.to_numeric(interactive_df["New Building Height"], errors="coerce")

    interactive_df["ExistingFloorsNum"] = pd.to_numeric(interactive_df["Number of Existing Floors"], errors="coerce")
    interactive_df["OwnerTooltip"] = interactive_df["Owner"].fillna("N/A").astype(str)

    # Selected building highlight: force green outline/fill via colorRGB override
    def color_with_selection(row):
        if st.session_state.selected_bbl is not None and str(row.get("BBL", "")) == str(st.session_state.selected_bbl):
            return [0, 200, 0]
        return row.get("colorRGB", [200, 200, 200])

    interactive_df["colorRGB"] = interactive_df.apply(color_with_selection, axis=1)

    features = []
    for _, r in interactive_df.iterrows():
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
            {BoroughName}, NY {Zip5}<br/>
            <hr/>
            <b>BBL:</b> {BBLstr}<br/>
            <b>% Impact:</b> {ImpactPctLabel}<br/>
            <b>New Units:</b> {NewUnitsNum}<br/>
            <b>New Floors:</b> {NewFloorsNum}<br/>
            <b>New Building Height:</b> {NewHeightNum}<br/>
            <b>Existing Floors:</b> {ExistingFloorsNum}<br/>
            <b>Owner:</b> {OwnerTooltip}
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

    # IMPORTANT: Use on_select to capture map clicks (same behavior as Locate button)
    event = st.pydeck_chart(
        deck,
        use_container_width=True,
        on_select="rerun",
        selection_mode="single-object",
    )

    # If user clicked a building on the map, switch right panel to single-property mode
    try:
        if event and event.selection and event.selection.get("objects"):
            obj = event.selection["objects"][0]
            props = obj.get("properties", {})
            clicked_bbl = props.get("BBL") or props.get("BBLstr")
            if clicked_bbl:
                select_property_by_bbl(gdf, clicked_bbl)
                st.rerun()
    except Exception:
        pass

# =============================
# Right: Property List
# =============================
with col_list:
    st.subheader("Property List")

    # Restore from single view back to top 10
    if st.session_state.show_single:
        if st.button("Show Top 10 properties"):
            st.session_state.show_single = False
            st.session_state.use_map_filter = False
            st.rerun()

    # Build list dataframe
    list_df = filtered.copy()

    # Optional "map area" filter (simple bbox around current center)
    if st.session_state.use_map_filter:
        lat = st.session_state.map_center["lat"]
        lon = st.session_state.map_center["lon"]
        delta = 0.02  # heuristic window
        list_df = list_df[
            list_df["geom_geojson"].notna()
        ].copy()

        centers = list_df["geom_geojson"].apply(get_geojson_center)
        list_df["__lat"] = centers.apply(lambda x: x[0] if x else np.nan)
        list_df["__lon"] = centers.apply(lambda x: x[1] if x else np.nan)

        list_df = list_df[
            list_df["__lat"].between(lat - delta, lat + delta) &
            list_df["__lon"].between(lon - delta, lon + delta)
        ].drop(columns=["__lat", "__lon"], errors="ignore")

    # If in single-property mode, show only selected one
    if st.session_state.show_single and st.session_state.selected_bbl is not None:
        list_df = gdf[gdf["BBL"].astype(str) == str(st.session_state.selected_bbl)].copy()

    # Sort by impact
    list_df = list_df.sort_values("ImpactPct", ascending=False)

    top_n = 10 if not st.session_state.show_single else 1
    list_df = list_df.head(top_n)

    st.caption(f"Top {len(list_df)} properties by % Impact")

    # -----------------------------
    # Detail renderer (TWO columns)
    # -----------------------------
    DETAIL_FIELDS = [
        ("BBL", "BBL", "text"),
        ("Borough", "Borough", "text"),
        ("Zipcode", "ZIP Code", "text"),
        ("Address", "Address", "text"),

        ("New Units", "New Units", "int"),
        ("% of New Units Impact", "% Impact", "pct_ratio"),
        ("New Floors", "New Floors", "int"),
        ("New Building Height", "New Building Height", "height"),
        ("Air Rights", "Air Rights", "text"),

        ("Number of Existing Floors", "Number of Existing Floors", "int"),

        ("Residential Area", "Residential Area", "area"),
        ("Commercial Area", "Commercial Area", "area"),
        ("Units Residential", "Units Residential", "int"),
        ("Units Commercial", "Units Commercial", "int"),
        ("Units Total", "Units Total", "int"),

        ("Year Built", "Year Built", "int"),
        ("Zoning District 1", "Zoning District 1", "text"),
        ("Building Class", "Building Class", "text"),
        ("Owner", "Owner", "text"),

        ("FAR Built", "FAR Built", "num"),
        ("FAR Residential", "FAR Residential", "num"),
        ("FAR Commercial", "FAR Commercial", "num"),
    ]


    def render_detail_two_columns(row):
        left, right = st.columns(2)
        pairs = DETAIL_FIELDS

        for i, (col, label, kind) in enumerate(pairs):
            val = row.get(col, None)

            if kind == "int":
                shown = fmt_int(val)
            elif kind == "height":
                shown = fmt_height(val)
            elif kind == "area":
                shown = fmt_area_sqft(val)
            elif kind == "pct_ratio":
                shown = fmt_percent_from_ratio(val)
            elif kind == "num":
                if val is None or (isinstance(val, float) and np.isnan(val)):
                    shown = "N/A"
                else:
                    try:
                        shown = f"{float(val):.2f}".rstrip("0").rstrip(".")
                    except Exception:
                        shown = "N/A"
            else:
                shown = "N/A" if val is None or (isinstance(val, float) and np.isnan(val)) else str(val)

            target = left if i % 2 == 0 else right
            with target:
                info_row(label, shown)


    # -----------------------------
    # Render list
    # -----------------------------
    if len(list_df) == 0:
        st.info("No properties found.")
    else:
        # Two-column layout for the list of properties (each tile is one address)
        list_cols = st.columns(2)

        for idx, r in list_df.iterrows():
            addr = str(r.get("Address", "N/A"))
            zc = str(r.get("Zipcode", "N/A")).zfill(5)
            impact_label = fmt_percent_from_ratio(r.get("% of New Units Impact"))

            bbl = str(r.get("BBL", "N/A"))

            # Alternate tiles between the two columns
            tile = list_cols[idx % 2]

            with tile:
                container = st.container(border=True)

                with container:
                    header_cols = st.columns([8, 1])

                    with header_cols[0]:
                        st.markdown(
                            f"**{addr}**  \nNew York, NY {zc}  \n% Impact: {impact_label}"
                        )

                    with header_cols[1]:
                        st.markdown(
                            "<div style='display:flex; justify-content:flex-end;'>",
                            unsafe_allow_html=True,
                        )
                        if st.button("📍", key=f"locate_{bbl}", help="Locate on map"):
                            select_property(r.to_dict())
                            st.rerun()
                        st.markdown("</div>", unsafe_allow_html=True)

                    # Expander label should not repeat the address
                    with st.expander("Details"):
                        render_detail_two_columns(r)
