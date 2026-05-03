"""Constants and option lists for Step 3 (burn subtitles)."""

SUB_POSITIONS = {
    "Bottom center (default)": 2,
    "Top center": 8,
    "Middle center": 5,
    "Bottom left": 1,
    "Bottom right": 3,
}
FONT_COLORS = ["white", "yellow", "cyan", "green", "red", "black"]
OUTLINE_COLORS = ["black", "white", "none"]
BG_COLORS = [
    "black",
    "white",
    "yellow",
    "blue",
    "red",
    "green",
    "purple",
    "orange",
    "gray",
]
FONT_FAMILIES = [
    "Arial",
    "Arial Bold",
    "Impact",
    "Tahoma",
    "Verdana",
    "Trebuchet MS",
    "Times New Roman",
    "Courier New",
]
BG_BOX_STYLES = {
    "None": "none",
    "Semi-transparent box": "semi",
    "Opaque box": "opaque",
}
PREVIEW_ASPECTS = {
    "Auto (from source video)": None,
    "16:9 (Landscape)": 16 / 9,
    "9:16 (Portrait)": 9 / 16,
    "1:1 (Square)": 1.0,
    "4:3": 4 / 3,
}
PREVIEW_ASPECT_AUTO = "Auto (from source video)"
PRESET_OPTIONS = ["ultrafast", "veryfast", "fast", "medium", "slow"]
CRF_RANGE = (18, 28)
DEFAULT_CRF = 20
DEFAULT_PRESET = "medium"
# Legacy: hard-burn ASS now sets PlayRes to the real video size; Fontsize uses
# ``int(video_h * font_pct / 100)`` in burn_step. Kept for any old configs/tests.
ASS_PLAYRES_Y = 288
COLOR_MAP = {
    "white": "FFFFFF",
    "yellow": "FFFF00",
    "cyan": "00FFFF",
    "black": "000000",
    "red": "FF0000",
    "green": "00FF00",
    "blue": "0000FF",
    "purple": "800080",
    "orange": "FFA500",
    "gray": "808080",
}

CHANNEL_PROFILE_FILE = ".subsync_channel_profiles.json"
CHANNEL_PROFILE_ASSETS = ".subsync_channel_profiles"
BRAND_POSITIONS = {
    "Random": "random",
    "Top left": "top_left",
    "Top right": "top_right",
    "Bottom left": "bottom_left",
    "Bottom right": "bottom_right",
}
