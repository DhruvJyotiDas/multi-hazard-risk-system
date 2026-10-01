"""Central configuration for the Multi-Hazard Risk & Emergency Routing system.

Every tunable (study area, dates, factor ratings, MCDM weights, routing alpha,
thresholds, output paths) lives here -- no other module hardcodes a value.

Study-area context (Wayanad district, Kerala, India)
----------------------------------------------------
Wayanad is a high-relief Western Ghats plateau district: landslide-dominant
(July 2024 Mundakkai-Chooralmala event, 2019 Puthumala/Kavalappara-region
events), flood-prone along Kabini tributaries (2018 and 2019 Kerala floods),
and fire-prone in the dry season (Feb-May) in deciduous forest tracts and
sanctuaries. Monsoon cloud makes optical data unreliable, hence the SAR-based
flood layer.
"""
from __future__ import annotations

import os
from pathlib import Path

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
ROOT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT_DIR / "outputs"
CACHE_DIR = OUTPUT_DIR / "cache"
INDEX_HTML = ROOT_DIR / "index.html"
RISK_STATS_CSV = OUTPUT_DIR / "risk_stats.csv"
WEIGHTS_CSV = OUTPUT_DIR / "mcdm_weights.csv"
ROUTES_GEOJSON = OUTPUT_DIR / "routes.geojson"
ROUTE_METRICS_CSV = OUTPUT_DIR / "route_metrics.csv"
HOTSPOTS_GEOJSON = OUTPUT_DIR / "hotspots.geojson"
VALIDATION_JSON = OUTPUT_DIR / "landslide_validation.json"
GRAPH_CACHE = CACHE_DIR / "wayanad_drive.graphml"
POI_CACHE = CACHE_DIR / "wayanad_pois.geojson"
AOI_CACHE = CACHE_DIR / "aoi.geojson"
OSM_PBF_PATH = CACHE_DIR / "kerala.osm.pbf"

# --------------------------------------------------------------------------- #
# Earth Engine
# --------------------------------------------------------------------------- #
# Set the environment variable GEE_PROJECT (or EARTHENGINE_PROJECT) to your
# Google Cloud project that is registered for Earth Engine.
GEE_PROJECT = os.environ.get("GEE_PROJECT") or os.environ.get("EARTHENGINE_PROJECT")

# --------------------------------------------------------------------------- #
# Study area
# --------------------------------------------------------------------------- #
PLACE_NAME = "Wayanad district, Kerala, India"
# Fallback bounding box (west, south, east, north) used if OSM geocoding fails.
FALLBACK_BBOX = (75.75, 11.45, 76.55, 12.05)
MAP_CENTER = (11.72, 76.08)          # (lat, lon) initial map view
MAP_ZOOM = 10

# Analysis CRS = UTM zone 43N (covers 72-78 deg E, i.e. all of Wayanad)
ANALYSIS_CRS = "EPSG:32643"
ANALYSIS_SCALE = 30                  # m, native SRTM grid
STATS_SCALE = 90                     # m, scale of min/max, quantile and area statistics
EXPORT_SCALE = 100                   # m, raster export grid for routing + web map
EXPORT_CRS = "EPSG:4326"
NORMALIZE_UPPER_PERCENTILE = 99     # min-max upper bound = this percentile (None = strict maximum)
EE_MAX_PIXELS = int(1e10)
EE_TILE_SCALE = 4                    # tileScale for heavy reduceRegion calls

# --------------------------------------------------------------------------- #
# Dataset IDs (all pulled straight from the GEE catalog)
# --------------------------------------------------------------------------- #
DS_S1 = "COPERNICUS/S1_GRD"
DS_S2 = "COPERNICUS/S2_SR_HARMONIZED"
DS_SRTM = "USGS/SRTMGL1_003"
DS_JRC = "JRC/GSW1_4/GlobalSurfaceWater"
DS_FIRMS = "FIRMS"                   # MODIS Collection 6.1 active fires (2000-11 onward)
DS_VIIRS = "NASA/VIIRS/002/VNP14A1"  # VIIRS 1 km daily thermal anomalies
DS_CHIRPS = "UCSB-CHG/CHIRPS/DAILY"
DS_DW = "GOOGLE/DYNAMICWORLD/V1"

# --------------------------------------------------------------------------- #
# Flood layer (Sentinel-1 SAR + Otsu)
# --------------------------------------------------------------------------- #
# Documented flood windows: Aug-2018 and Aug-2019 Kerala floods, and the
# late-July/early-Aug 2024 monsoon burst. Each window yields a flood mask; the
# mean of masks is the flood *frequency* (0..1).
FLOOD_EVENT_WINDOWS = (
    ("2018-08-14", "2018-08-26"),
    ("2019-08-08", "2019-08-20"),
    ("2024-07-28", "2024-08-09"),
)
S1_POLARISATION = "VV"
S1_INSTRUMENT_MODE = "IW"
S1_SPECKLE_RADIUS_M = 30             # focal-median radius for speckle reduction
OTSU_HISTOGRAM_BUCKETS = 255
OTSU_HISTOGRAM_SCALE = 60            # m
OTSU_FALLBACK_DB = -16.0             # used only if the histogram is degenerate
OTSU_WATER_ZONE_MIN_OCCURRENCE = 5     # JRC occurrence (%) defining 'known water' for the Otsu histogram zone
OTSU_WATER_ZONE_RADIUS_M = 1000        # radius around known water in which the Otsu histogram is built
OTSU_VALID_RANGE_DB = (-22.0, -13.0)  # guard rails for the automatic threshold
FLOOD_MAX_SLOPE_DEG = 5.0            # slope > 5 deg => SAR shadow, not flood
FLOOD_PERMANENT_WATER_OCCURRENCE = 50  # JRC occurrence (%) above which water is permanent
FLOOD_SMOOTH_RADIUS_M = 90           # focal mean radius turning frequency into hazard surface
FLOOD_WEIGHT_SAR = 0.7               # blend of SAR flood frequency ...
FLOOD_WEIGHT_JRC = 0.3               # ... and JRC historical (non-permanent) occurrence

# --------------------------------------------------------------------------- #
# Landslide layer (Frequency-Ratio-style, literature-derived class ratings)
# --------------------------------------------------------------------------- #
# Optical/DW inputs end BEFORE the July-2024 event so the validation is not
# contaminated by landslide scars (no label leakage).
NDVI_DATE_RANGE = ("2023-11-01", "2024-04-30")   # dry-season, mostly cloud-free
S2_MAX_CLOUD_PROB = 40                           # max CLOUDY_PIXEL_PERCENTAGE per scene
NDVI_GAP_RADIUS_M = 300                          # neighbourhood used to fill persistent cloud gaps
NDVI_GAP_FILL = 0.60                             # neutral NDVI if the neighbourhood is also empty
RAIN_YEARS = tuple(range(2010, 2024))            # JJAS seasons used for the rainfall factor
MONSOON_MONTHS = (6, 9)                          # SW monsoon: Jun-Sep inclusive

# Class lower-bounds and the FR rating of the class that starts at each bound.
# Ratings are literature-derived (Wang 2020; Rehman 2022; He 2023): FR>1 means the
# class is more landslide-prone than the study-area average.
LANDSLIDE_FACTORS = {
    "slope": {   # degrees (SRTM) -- most diagnostic factor in the Western Ghats
        "edges":   (5, 15, 25, 35, 45),
        "ratings": (0.15, 0.50, 1.10, 1.90, 2.40, 1.80),
    },
    "elevation": {  # metres
        "edges":   (300, 700, 1100, 1500, 2000),
        "ratings": (0.30, 0.80, 1.40, 1.70, 1.20, 0.60),
    },
    "ndvi": {   # sparse vegetation (low NDVI) => higher susceptibility
        "edges":   (0.2, 0.4, 0.6, 0.8),
        "ratings": (2.00, 1.60, 1.10, 0.80, 0.50),
    },
    "rainfall": {  # max seasonal (JJAS) CHIRPS total across RAIN_YEARS, mm
        "edges":   (2000, 2500, 3000, 3500),
        "ratings": (0.50, 0.90, 1.30, 1.70, 2.10),
    },
}
# FR-AHP style factor weights (sum to 1)
LANDSLIDE_FACTOR_WEIGHTS = {"slope": 0.40, "rainfall": 0.25, "ndvi": 0.20, "elevation": 0.15}

# Documented July 2024 Mundakkai-Chooralmala landslide zone.
# SPEC point is the coordinate given in the project brief; VERIFIED point is the
# location reported in published event records (landslides.org / NRSC Charter
# 1029 products: ~11.486 N, 76.156 E). Both are tested; the verified point is
# the headline gate because the brief's 76.23 E lies ~8 km east of the event.
EVENT_NAME = "July 2024 Mundakkai-Chooralmala landslide"
EVENT_POINT_SPEC = (11.49, 76.23)        # (lat, lon) as given in the brief
EVENT_POINT_VERIFIED = (11.486, 76.156)  # (lat, lon) from published event records
EVENT_BUFFER_M = 2000
VALIDATION_MIN_FRACTION = 0.50           # share of zone pixels that must be High/Very High
VALIDATION_PASS_CLASSES = (4, 5)         # High, Very High

# --------------------------------------------------------------------------- #
# Fire layer (MODIS / VIIRS active-fire density)
# --------------------------------------------------------------------------- #
FIRE_YEARS_BACK = 10
FIRE_END_DATE = "2025-12-31"
FIRE_DRY_SEASON_MONTHS = (1, 5)          # Jan-May dry season (peak Feb-Apr)
FIRE_MIN_CONFIDENCE = 30                 # MODIS confidence (%) minimum
VIIRS_MIN_FIREMASK = 7                   # VNP14A1 FireMask: 7 nominal, 8 high confidence
USE_VIIRS = True
FIRE_NATIVE_SCALE = 1000                 # m
FIRE_KERNEL_RADIUS_M = 5000
FIRE_KERNEL_SIGMA_M = 2500
FIRE_VEG_CLASSES = ("trees", "grass", "shrub_and_scrub", "crops")
FIRE_VEG_FLOOR = 0.20                    # minimum fuel weight so density is never zeroed by sparse fuel
DW_DATE_RANGE = ("2023-01-01", "2024-06-30")

# --------------------------------------------------------------------------- #
# Exposure layer
# --------------------------------------------------------------------------- #
EXPOSURE_BUILT_WEIGHT = 0.70
EXPOSURE_ROAD_WEIGHT = 0.30
EXPOSURE_ROAD_MAX_DIST_M = 1500          # distance beyond which road proximity = 0
EXPOSURE_BUILT_SMOOTH_M = 90
ROAD_EE_CLASSES = ("motorway", "trunk", "primary", "secondary", "tertiary",
                   "unclassified", "residential")
ROAD_EE_SIMPLIFY_M = 25
ROAD_EE_MAX_VERTICES = 60000             # vertex cap for the road upload to Earth Engine
ROAD_EE_MAX_SIMPLIFY_STEPS = 5           # tolerance doubles at most this many times before a road class is dropped

# --------------------------------------------------------------------------- #
# MCDM
# --------------------------------------------------------------------------- #
CRITERIA = ("flood", "landslide", "exposure", "fire")
AHP_TARGET_WEIGHTS = {"flood": 0.35, "landslide": 0.30, "exposure": 0.20, "fire": 0.15}
# Saaty pairwise judgements a[i][j] = importance of i over j (order = CRITERIA).
# Calibrated so the principal eigenvector reproduces AHP_TARGET_WEIGHTS to +/-0.005.
AHP_PAIRWISE = (
    (1.0,   1.5,   1.5,   2.0),
    (1/1.5, 1.0,   2.0,   2.0),
    (1/1.5, 1/2.0, 1.0,   1.5),
    (1/2.0, 1/2.0, 1/1.5, 1.0),
)
AHP_MAX_CR = 0.10
AHP_RANDOM_INDEX = {1: 0.0, 2: 0.0, 3: 0.58, 4: 0.90, 5: 1.12, 6: 1.24, 7: 1.32, 8: 1.41, 9: 1.45}
WEIGHT_SAMPLE_PIXELS = 20000             # pixels sampled to derive entropy / CRITIC weights
WEIGHT_SAMPLE_SEED = 42
MIN_WEIGHT_FLOOR = 1e-6
RISK_CLASSIFICATION = "quantile"         # "quantile" or "natural_breaks"
RISK_CLASS_NAMES = ("Very Low", "Low", "Moderate", "High", "Very High")
RISK_CLASS_COLORS = ("#1a9850", "#a6d96a", "#fdae61", "#e34a33", "#7f0000")
RISK_RAMP = ("#1a9850", "#ffff66", "#fdae61", "#e31a1c", "#7f0000")  # green->yellow->orange->red->dark red
HOTSPOT_MIN_AREA_KM2 = 0.05
HOTSPOT_SIMPLIFY_M = 30

# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #
ROUTE_ALPHA = 3.0                        # edge cost = length * (1 + alpha * mean_risk)
ROUTE_NETWORK_TYPE = "drive"
# "pbf": one regional OpenStreetMap extract (fast, reproducible, works behind firewalls that block Overpass);
# "overpass": live Overpass API via OSMnx. The other source is tried automatically if the chosen one fails.
OSM_SOURCE = "pbf"
OSM_PBF_URL = "https://download.openstreetmap.fr/extracts/asia/india/kerala-latest.osm.pbf"
OSM_CLIP_BUFFER_DEG = 0.01               # network/POIs are kept this far (~1 km) beyond the district boundary
ROUTE_EDGE_SAMPLE_STEP_M = 50
N_ORIGINS = 5
SETTLEMENT_BUILT_THRESHOLD = 0.5         # smoothed built-area probability that counts as settlement
SETTLEMENT_MIN_THRESHOLD = 0.10          # threshold is lowered stepwise to this floor if too few clusters
SETTLEMENT_THRESHOLD_STEP = 0.05
SETTLEMENT_MERGE_DILATION_PX = 2         # pixels dilated to merge adjacent buildings into one cluster
SETTLEMENT_MIN_PIXELS = 15               # minimum cluster size (at EXPORT_SCALE)
SETTLEMENT_MIN_SEPARATION_M = 3000       # origins must be this far apart
PLACE_NAME_MAX_DIST_M = 4000
HOSPITAL_TAGS = {"amenity": ["hospital"], "healthcare": ["hospital"]}
HOSPITAL_NAME_EXCLUDE = r"dental|dentist|pharmac|laborator|diagnos|optical|physio|scan cent|medical store"
HOSPITAL_FALLBACK_TAGS = {"amenity": ["clinic", "doctors"], "healthcare": ["clinic", "centre"]}
SHELTER_TAGS = {"amenity": ["shelter", "community_centre", "school"],
                "emergency": ["assembly_point", "shelter"]}
PLACE_TAGS = {"place": ["city", "town", "village", "hamlet", "suburb", "locality"]}
ROAD_DISPLAY_CLASSES = ("motorway", "trunk", "primary", "secondary", "tertiary")
ROAD_DISPLAY_SIMPLIFY_DEG = 0.0002

# --------------------------------------------------------------------------- #
# Web map
# --------------------------------------------------------------------------- #
MAP_TITLE = "Multi-Hazard Risk & Emergency Routing — Wayanad District, Kerala"
MAP_LAYER_OPACITY = 0.75
ROUTE_SHORTEST_STYLE = {"color": "#555555", "weight": 4, "dashArray": "8 8", "opacity": 0.95}
ROUTE_LEASTRISK_STYLE = {"color": "#00a63e", "weight": 5, "opacity": 0.95}
