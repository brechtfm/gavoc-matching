"""
GAVOC <-> GLOB Match Review Tool
================================

A Streamlit app for reviewing candidate matches between historical place
names in the GAVOC dataset and the GLOB dataset.

Run with:
    streamlit run app.py

Expected input files (CSV), can be uploaded in the sidebar or left as the
bundled samples:

- gavoc_matches.csv   : glob_id, external_id, similarity_score, method, overlap
- gavoc_overview.csv  : external_id, pref_label, label, longitude, latitude,
                        point_coord, coord_source, coord_source_page,
                        coord_remarks, place_type, pp_uri, pp_type_label,
                        attestation_id
- glob_overview.csv   : glob_id, label, pref_label, latitude, longitude, wkt,
                        place_type_uri, place_type
"""

import os
import io
import pandas as pd
import streamlit as st
import pydeck as pdk

st.set_page_config(page_title="GAVOC \u2194 GLOB Match Review", layout="wide")

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DECISIONS_PATH = os.path.join(APP_DIR, "review_decisions.csv")

DEFAULT_MATCHES = os.path.join(APP_DIR, "gavoc_matches.csv")
DEFAULT_GAVOC = os.path.join(APP_DIR, "gavoc_overview.csv")
DEFAULT_GLOB = os.path.join(APP_DIR, "glob_overview.csv")

GAVOC_COLOR = [0, 120, 220]   # blue
GLOB_COLOR = [220, 60, 40]    # red

DECISION_OPTIONS = ["pending", "match", "reject", "child"]
DECISION_LABELS = {
    "pending": "\u23f3 Pending",
    "match": "\u2705 Match",
    "reject": "\u274c Not a match",
    "child": "🪆 Child",
}


# --------------------------------------------------------------------------
# Data loading
# --------------------------------------------------------------------------

@st.cache_data
def load_csv(file_or_path):
    return pd.read_csv(file_or_path)


def to_float(val):
    """Safely coerce coordinate-like values to float, else None."""
    try:
        if val is None:
            return None
        s = str(val).strip()
        if s == "" or s == "-":
            return None
        return float(s)
    except (ValueError, TypeError):
        return None


def split_pipe(val):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return []
    s = str(val).strip()
    if not s or s == "-":
        return []
    return [p.strip() for p in s.split("|") if p.strip()]


def load_decisions():
    if os.path.exists(DECISIONS_PATH):
        try:
            df = pd.read_csv(DECISIONS_PATH, dtype=str)
            df = df.fillna("")
            records = {}
            for _, row in df.iterrows():
                key = (row["external_id"], row["glob_id"])
                records[key] = {"decision": row.get("decision", "pending"),
                                 "note": row.get("note", "")}
            return records
        except Exception:
            return {}
    return {}


def save_decisions():
    rows = []
    for (external_id, glob_id), val in st.session_state.decisions.items():
        rows.append({
            "external_id": external_id,
            "glob_id": glob_id,
            "decision": val.get("decision", "pending"),
            "note": val.get("note", ""),
        })
    df = pd.DataFrame(rows, columns=["external_id", "glob_id", "decision", "note"])
    df.to_csv(DECISIONS_PATH, index=False)


def set_decision(external_id, glob_id, decision=None, note=None):
    key = (external_id, glob_id)
    current = st.session_state.decisions.get(key, {"decision": "pending", "note": ""})
    if decision is not None:
        current["decision"] = decision
    if note is not None:
        current["note"] = note
    st.session_state.decisions[key] = current
    save_decisions()


def get_decision(external_id, glob_id):
    return st.session_state.decisions.get((external_id, glob_id), {"decision": "pending", "note": ""})


# --------------------------------------------------------------------------
# Sidebar: data sources
# --------------------------------------------------------------------------

st.sidebar.title("Data sources")
st.sidebar.caption("Upload your full CSVs here, or leave blank to use the bundled samples.")

up_matches = st.sidebar.file_uploader("gavoc_matches.csv", type="csv", key="up_matches")
up_gavoc = st.sidebar.file_uploader("gavoc_overview.csv", type="csv", key="up_gavoc")
up_glob = st.sidebar.file_uploader("glob_overview.csv", type="csv", key="up_glob")

try:
    matches_df = load_csv(up_matches) if up_matches is not None else load_csv(DEFAULT_MATCHES)
    gavoc_df = load_csv(up_gavoc) if up_gavoc is not None else load_csv(DEFAULT_GAVOC)
    glob_df = load_csv(up_glob) if up_glob is not None else load_csv(DEFAULT_GLOB)
except FileNotFoundError:
    st.error("Could not find sample CSVs and none were uploaded. Please upload the three CSV files.")
    st.stop()

# Normalize types
matches_df["external_id"] = matches_df["external_id"].astype(str)
matches_df["glob_id"] = matches_df["glob_id"].astype(str)
gavoc_df["external_id"] = gavoc_df["external_id"].astype(str)
glob_df["glob_id"] = glob_df["glob_id"].astype(str)

gavoc_by_id = gavoc_df.set_index("external_id", drop=False)
glob_by_id = glob_df.set_index("glob_id", drop=False)

if "decisions" not in st.session_state:
    st.session_state.decisions = load_decisions()

# --------------------------------------------------------------------------
# Sidebar: navigation
# --------------------------------------------------------------------------

external_ids = sorted(matches_df["external_id"].unique().tolist())
total_entries = len(external_ids)


def entry_is_reviewed(ext_id):
    glob_ids = matches_df.loc[matches_df["external_id"] == ext_id, "glob_id"].tolist()
    if not glob_ids:
        return False
    return all(get_decision(ext_id, gid)["decision"] != "pending" for gid in glob_ids)


reviewed_count = sum(1 for e in external_ids if entry_is_reviewed(e))

st.sidebar.markdown("---")
st.sidebar.title("Navigation")
st.sidebar.progress(reviewed_count / total_entries if total_entries else 0)
st.sidebar.caption(f"{reviewed_count} / {total_entries} gavoc entries fully reviewed")

filter_mode = st.sidebar.radio(
    "Show entries",
    ["All", "Unreviewed only", "Reviewed only"],
    index=0,
)

if filter_mode == "Unreviewed only":
    nav_ids = [e for e in external_ids if not entry_is_reviewed(e)]
elif filter_mode == "Reviewed only":
    nav_ids = [e for e in external_ids if entry_is_reviewed(e)]
else:
    nav_ids = external_ids

if not nav_ids:
    st.sidebar.info("No entries match this filter.")
    st.stop()

if "current_index" not in st.session_state:
    st.session_state.current_index = 0


def label_for(ext_id):
    mark = "\u2705" if entry_is_reviewed(ext_id) else "\u23f3"
    pref = gavoc_by_id.loc[ext_id, "pref_label"] if ext_id in gavoc_by_id.index else "?"
    return f"{mark} {ext_id} \u2014 {pref}"


current_ext_id = st.sidebar.selectbox(
    "Jump to gavoc entry",
    nav_ids,
    index=min(st.session_state.current_index, len(nav_ids) - 1),
    format_func=label_for,
)
st.session_state.current_index = nav_ids.index(current_ext_id)

nav_cols = st.sidebar.columns(2)
if nav_cols[0].button("\u2190 Previous", use_container_width=True):
    st.session_state.current_index = max(0, st.session_state.current_index - 1)
    st.rerun()
if nav_cols[1].button("Next \u2192", use_container_width=True):
    st.session_state.current_index = min(len(nav_ids) - 1, st.session_state.current_index + 1)
    st.rerun()

current_ext_id = nav_ids[st.session_state.current_index]

st.sidebar.markdown("---")
with open(DECISIONS_PATH, "rb") if os.path.exists(DECISIONS_PATH) else io.BytesIO(b"") as f:
    st.sidebar.download_button(
        "\u2b07\ufe0f Download review decisions (CSV)",
        data=f.read(),
        file_name="review_decisions.csv",
        mime="text/csv",
        use_container_width=True,
    )

# --------------------------------------------------------------------------
# Main: gavoc entry header
# --------------------------------------------------------------------------

st.title("GAVOC \u2194 GLOB Match Review")

if current_ext_id not in gavoc_by_id.index:
    st.error(f"external_id {current_ext_id} not found in gavoc_overview data.")
    st.stop()

gavoc_row = gavoc_by_id.loc[current_ext_id]
gavoc_lon = to_float(gavoc_row.get("longitude"))
gavoc_lat = to_float(gavoc_row.get("latitude"))
gavoc_aliases = split_pipe(gavoc_row.get("label"))

entry_matches = matches_df[matches_df["external_id"] == current_ext_id].copy()
entry_matches = entry_matches.sort_values("similarity_score", ascending=False)

# The GAVOC entry stays pinned in the left column while the candidate list
# scrolls independently in a fixed-height container on the right, so the
# label/type info is always visible while reviewing.
gavoc_col, candidates_col = st.columns([1, 2], gap="large")

with gavoc_col:
    st.markdown("#### GAVOC entry")
    st.subheader(f"{gavoc_row.get('pref_label', '(no label)')}")
    st.caption(f"external_id: `{current_ext_id}`")
    if gavoc_aliases:
        st.markdown("**Labels:** " + ", ".join(gavoc_aliases))
    st.markdown(f"**Place type:** {gavoc_row.get('place_type', '\u2013') or '\u2013'}")
    st.markdown(f"**PP type label:** {gavoc_row.get('pp_type_label', '\u2013') or '\u2013'}")
    st.markdown(f"**# candidate matches:** {len(entry_matches)}")
    if gavoc_lat is not None and gavoc_lon is not None:
        st.markdown(f"**Coordinates:** {gavoc_lat:.4f}, {gavoc_lon:.4f}")
    if gavoc_row.get("coord_remarks"):
        st.caption(f"Coordinate remarks: {gavoc_row.get('coord_remarks')}")
    if gavoc_row.get("coord_source"):
        st.caption(f"Coordinate source: {gavoc_row.get('coord_source')} (p. {gavoc_row.get('coord_source_page', '')})")

    if gavoc_lon is not None and gavoc_lat is not None:
        st.map(pd.DataFrame([{"lat": gavoc_lat, "lon": gavoc_lon}]), size=20, color=GAVOC_COLOR + [200])
    else:
        st.info("No coordinates available for this GAVOC entry.")

with candidates_col:
    st.markdown(f"#### Candidate matches ({len(entry_matches)})")
    st.caption("\U0001f535 blue = GAVOC point \u00b7 \U0001f534 red = GLOB point. Sorted by similarity score, highest first.")

    if entry_matches.empty:
        st.warning("No candidate matches recorded for this entry.")

    # Fixed-height scrollable area: scrolling here does not move the GAVOC
    # panel on the left out of view.
    with st.container(height=750, border=False):
        for _, m in entry_matches.iterrows():
            glob_id = m["glob_id"]
            score = m.get("similarity_score", None)
            method = m.get("method", "")
            overlap = m.get("overlap", "")

            decision_state = get_decision(current_ext_id, glob_id)
            status_badge = DECISION_LABELS.get(decision_state["decision"], decision_state["decision"])

            with st.container(border=True):
                top = st.columns([3, 1])
                with top[0]:
                    st.markdown(f"#### `{glob_id}`  \u2014  {status_badge}")
                with top[1]:
                    if score is not None:
                        st.metric("Similarity", f"{float(score):.1f}")

                info_col, map_col = st.columns([2, 1])

            if glob_id in glob_by_id.index:
                glob_row = glob_by_id.loc[glob_id]
                glob_lon = to_float(glob_row.get("longitude"))
                glob_lat = to_float(glob_row.get("latitude"))
                glob_aliases = split_pipe(glob_row.get("label"))
                glob_place_types = split_pipe(glob_row.get("place_type"))

                with info_col:
                    st.markdown(f"**GLOB pref. label:** {glob_row.get('pref_label', '(none)')}")
                    if glob_aliases:
                        st.markdown("**GLOB aliases:** " + ", ".join(glob_aliases))
                    st.markdown("**GLOB place type(s):** " + (", ".join(glob_place_types) if glob_place_types else "\u2013"))

                    metric_cols = st.columns(3)
                    metric_cols[0].markdown(f"**Overlap:** {overlap}")
                    if glob_lon is not None and glob_lat is not None:
                        metric_cols[1].markdown(f"**GLOB coords:** {glob_lat:.4f}, {glob_lon:.4f}")
                    else:
                        metric_cols[1].markdown("**GLOB coords:** not available")
                    if gavoc_lat is not None and gavoc_lon is not None:
                        metric_cols[2].markdown(f"**GAVOC coords:** {gavoc_lat:.4f}, {gavoc_lon:.4f}")
                    else:
                        metric_cols[2].markdown("**GAVOC coords:** not available")

                    with st.expander("Matching method details"):
                        st.text(str(method))

                with map_col:
                    layers = []
                    points = []
                    if gavoc_lon is not None and gavoc_lat is not None:
                        points.append({"lat": gavoc_lat, "lon": gavoc_lon, "color": GAVOC_COLOR, "label": "GAVOC"})
                    if glob_lon is not None and glob_lat is not None:
                        points.append({"lat": glob_lat, "lon": glob_lon, "color": GLOB_COLOR, "label": "GLOB"})

                    if points:
                        df_points = pd.DataFrame(points)
                        view_lat = df_points["lat"].mean()
                        view_lon = df_points["lon"].mean()

                        scatter = pdk.Layer(
                            "ScatterplotLayer",
                            data=df_points,
                            get_position="[lon, lat]",
                            get_fill_color="color",
                            get_radius=25000,
                            pickable=True,
                        )
                        layers.append(scatter)

                        if len(points) == 2:
                            line_df = pd.DataFrame([{
                                "from_lon": points[0]["lon"], "from_lat": points[0]["lat"],
                                "to_lon": points[1]["lon"], "to_lat": points[1]["lat"],
                            }])
                            line_layer = pdk.Layer(
                                "LineLayer",
                                data=line_df,
                                get_source_position="[from_lon, from_lat]",
                                get_target_position="[to_lon, to_lat]",
                                get_color=[150, 150, 150],
                                get_width=2,
                            )
                            layers.append(line_layer)

                        view_state = pdk.ViewState(latitude=view_lat, longitude=view_lon, zoom=3)
                        st.pydeck_chart(pdk.Deck(
                            layers=layers,
                            initial_view_state=view_state,
                            map_style=None,
                            tooltip={"text": "{label}"},
                        ), use_container_width=True)
                    else:
                        st.info("No coordinates available to plot for either point.")
            else:
                with info_col:
                    st.warning(f"glob_id `{glob_id}` not found in glob_overview data.")

            st.markdown("&nbsp;")
            action_cols = st.columns([1, 1, 1, 3])
            if action_cols[0].button("\u2705 Match", key=f"match_{current_ext_id}_{glob_id}", use_container_width=True):
                set_decision(current_ext_id, glob_id, decision="match")
                st.rerun()
            if action_cols[1].button("\u274c Not a match", key=f"reject_{current_ext_id}_{glob_id}", use_container_width=True):
                set_decision(current_ext_id, glob_id, decision="reject")
                st.rerun()
            if action_cols[2].button("🪆 Child", key=f"child_{current_ext_id}_{glob_id}", use_container_width=True):
                set_decision(current_ext_id, glob_id, decision="child")
                st.rerun()

            note = action_cols[3].text_input(
                "Note (optional)",
                value=decision_state.get("note", ""),
                key=f"note_{current_ext_id}_{glob_id}",
                label_visibility="collapsed",
                placeholder="Add a note about this decision\u2026",
            )
            if note != decision_state.get("note", ""):
                set_decision(current_ext_id, glob_id, note=note)

st.markdown("---")
st.caption(
    "Decisions are saved automatically to `review_decisions.csv` next to this app, "
    "and can be downloaded from the sidebar at any time."
)
