"""Light/dark appearance. Follows the macOS setting; set HOBO_THEME=dark or light to force one for testing."""
import os
import subprocess
import time

_state = {"dark": None, "at": 0.0}

PALETTE = {
    False: dict(header_bg="#ffffff", header_fg="#1d2026", header_edge="#8d96a0", gear="#6b7280", gear_hover="#1d2026",
                header_rule="#e3e6eb", window_bg="#eef0f4", card_bg="#ffffff", card_border="#e0e4ea", ink="#1d2026", key="#6b7280",
                accent="#2e86ab", track="#dde1e8", pill_ok_bg="#d8f3e2", pill_ok_fg="#157a3b",
                pill_idle_bg="#ffecc7", pill_idle_fg="#8a5a00", pill_off_bg="#e6e8ec", pill_off_fg="#5f6672",
                plot_bg="#ffffff", plot_border="#ffffff", grid="#e8eaed", axis="#9aa0a6", text="#5f6368",
                legend="#3c4043", muted="#666666", muted2="#777777", box_border="#a9b3bd", empty="#80868b",
                temp="#d1495b", rh="#2e86ab", tip_bg="#ffffff", tip_border="#c4c9cf", tip_text="#202124",
                select="#2e86ab", freeze="#3d9ad8", ok_bg="#e3f4e8", ok_fg="#14532d", err_bg="#fde8e8", err_fg="#7f1d1d",
                warn_bg="#fff3d6", warn_fg="#6b4a00", info_bg="#e4eef7", info_fg="#16384f"),
    True: dict(header_bg="#2c2c2e", header_fg="#eceef2", header_edge="#9aa1ab", gear="#a3aab5", gear_hover="#ffffff",
               header_rule="#3b3d42", window_bg="#17181b", card_bg="#232529", card_border="#33363c", ink="#eceef2", key="#9aa1ab",
               accent="#4db6e4", track="#363a41", pill_ok_bg="#1d4a2e", pill_ok_fg="#7ee2a1",
               pill_idle_bg="#4d3a10", pill_idle_fg="#ffd27a", pill_off_bg="#33363c", pill_off_fg="#a3aab5",
               plot_bg="#232529", plot_border="#232529", grid="#2f3136", axis="#6f7378", text="#a9adb3",
               legend="#d4d7dc", muted="#a0a0a8", muted2="#8e8e96", box_border="#555d66", empty="#8e9298",
               temp="#ff6b7d", rh="#4db6e4", tip_bg="#2b2d31", tip_border="#4a4d54", tip_text="#eceef1",
               select="#4db6e4", freeze="#6cc3f5", ok_bg="#1c3a28", ok_fg="#b8efc9", err_bg="#471f1f", err_fg="#ffc9c9",
               warn_bg="#4a3b12", warn_fg="#ffe3a0", info_bg="#1f3347", info_fg="#c6e0f5"),
}


def refresh(force=False) -> bool:
    """Re-read the system appearance (at most every 0.3 s unless forced); returns True if dark."""
    now = time.time()
    if not force and _state["dark"] is not None and now - _state["at"] < 0.3:
        return _state["dark"]
    forced = os.environ.get("HOBO_THEME", "").lower()
    if forced in ("dark", "light"):
        dark = forced == "dark"
    else:
        try:
            out = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"], capture_output=True,
                                 text=True, timeout=2).stdout
            dark = out.strip().lower() == "dark"
        except (OSError, subprocess.SubprocessError):
            dark = False
    _state.update(dark=dark, at=now)
    return dark


def is_dark() -> bool:
    return _state["dark"] if _state["dark"] is not None else refresh()


def color(name: str) -> str:
    return PALETTE[is_dark()][name]
