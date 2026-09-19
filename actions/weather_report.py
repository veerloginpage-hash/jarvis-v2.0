from __future__ import annotations

import html
import json
import os
import sys
import tempfile
import webbrowser
from pathlib import Path
from urllib.parse import quote_plus

import requests


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = _base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"

_OWM_KEY_NAMES = (
    "openweather_api_key",
    "open_weather_api_key",
    "openweathermap_api_key",
    "open_weather_map_api_key",
)


def weather_action(
    parameters: dict,
    player=None,
    session_memory=None,
) -> str:
    city     = parameters.get("city")
    when     = parameters.get("time", "today")  

    if not city or not isinstance(city, str) or not city.strip():
        msg = "Sir, the city is missing for the weather report."
        _log(msg, player)
        return msg

    city = city.strip()
    when = (when or "today").strip()

    if _wants_map_mode(parameters):
        return _show_weather_map(city=city, player=player, session_memory=session_memory)

    search_query  = f"weather in {city} {when}"
    url           = f"https://www.google.com/search?q={quote_plus(search_query)}"

    try:
        opened = webbrowser.open(url)
        if not opened:
            raise RuntimeError("webbrowser.open returned False")
    except Exception as e:
        msg = f"Sir, I couldn't open the browser for the weather report: {e}"
        _log(msg, player)
        return msg

    msg = f"Showing the weather for {city}, {when}, sir."
    _log(msg, player)

    if session_memory:
        try:
            session_memory.set_last_search(query=search_query, response=msg)
        except Exception:
            pass

    return msg


def _wants_map_mode(parameters: dict) -> bool:
    mode = str(parameters.get("mode", "")).strip().lower()
    if mode in {"map", "weather_map", "radar", "layers", "interactive_map"}:
        return True

    if parameters.get("map") is True:
        return True

    request = " ".join(
        str(parameters.get(k, "")) for k in ("request", "query", "description")
    ).lower()
    return any(
        phrase in request
        for phrase in (
            "weather map",
            "rain map",
            "radar",
            "weather radar",
            "temperature map",
            "wind map",
            "cloud map",
            "pressure map",
            "weather layers",
        )
    )


def _get_openweather_key() -> str:
    for env_name in ("OPENWEATHER_API_KEY", "OPENWEATHERMAP_API_KEY"):
        key = os.getenv(env_name, "").strip()
        if key:
            return key

    try:
        data = json.loads(API_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return ""

    for key_name in _OWM_KEY_NAMES:
        key = str(data.get(key_name, "")).strip()
        if key:
            return key
    return ""


def _fetch_current_weather(city: str, api_key: str) -> dict:
    response = requests.get(
        "https://api.openweathermap.org/data/2.5/weather",
        params={"q": city, "appid": api_key, "units": "metric"},
        timeout=12,
    )
    response.raise_for_status()
    data = response.json()
    coord = data.get("coord") or {}
    if "lat" not in coord or "lon" not in coord:
        raise RuntimeError("OpenWeather did not return coordinates for that city.")
    return data


def _show_weather_map(city: str, player=None, session_memory=None) -> str:
    api_key = _get_openweather_key()
    if not api_key:
        msg = (
            "Sir, OpenWeather is not configured yet. Add openweather_api_key "
            "to config/api_keys.json or set OPENWEATHER_API_KEY."
        )
        _log(msg, player)
        return msg

    try:
        weather = _fetch_current_weather(city, api_key)
    except Exception as e:
        msg = f"Sir, I couldn't load OpenWeather data for {city}: {e}"
        _log(msg, player)
        return msg

    try:
        html_path = _write_weather_map_html(city=city, api_key=api_key, weather=weather)
        opened = webbrowser.open(html_path.as_uri())
        if not opened:
            raise RuntimeError("webbrowser.open returned False")
    except Exception as e:
        msg = f"Sir, I couldn't open the weather map: {e}"
        _log(msg, player)
        return msg

    msg = f"Opening Weather Map Mode for {city}, sir."
    _log(msg, player)

    if session_memory:
        try:
            session_memory.set_last_search(query=f"weather map {city}", response=msg)
        except Exception:
            pass

    return msg


def _write_weather_map_html(city: str, api_key: str, weather: dict) -> Path:
    coord = weather.get("coord") or {}
    lat = float(coord.get("lat", 20.5937))
    lon = float(coord.get("lon", 78.9629))
    payload = {
        "city": city,
        "apiKey": api_key,
        "lat": lat,
        "lon": lon,
        "weather": weather,
    }
    payload_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    title = html.escape(f"JARVIS Weather Map - {city}", quote=True)
    markup = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
    :root {{
      color-scheme: dark;
      --bg: #02070b;
      --panel: rgba(1, 13, 20, 0.88);
      --line: rgba(0, 212, 255, 0.42);
      --cyan: #00d4ff;
      --cyan-soft: #77ecff;
      --amber: #ffb000;
      --red: #ff3355;
      --text: #d8f8ff;
      --muted: #6daebf;
    }}
    html, body, #map {{
      width: 100%;
      height: 100%;
      margin: 0;
      background: var(--bg);
      font-family: "Segoe UI", Arial, sans-serif;
      overflow: hidden;
    }}
    #map {{
      filter: saturate(1.12) contrast(1.08);
    }}
    .hud {{
      position: fixed;
      z-index: 700;
      color: var(--text);
      pointer-events: none;
      text-shadow: 0 0 12px rgba(0, 212, 255, 0.35);
    }}
    .topbar {{
      top: 18px;
      left: 18px;
      right: 18px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }}
    .brand, .weather-card, .control-card {{
      background: linear-gradient(135deg, rgba(0, 21, 32, 0.94), rgba(0, 5, 9, 0.78));
      border: 1px solid var(--line);
      box-shadow: 0 0 24px rgba(0, 212, 255, 0.16), inset 0 0 22px rgba(0, 212, 255, 0.06);
      backdrop-filter: blur(12px);
    }}
    .brand {{
      min-width: 240px;
      padding: 12px 16px;
      border-radius: 6px;
      letter-spacing: 0;
    }}
    .brand .label {{
      font-size: 12px;
      color: var(--cyan);
      text-transform: uppercase;
    }}
    .brand .city {{
      margin-top: 4px;
      font-size: 22px;
      font-weight: 700;
    }}
    .weather-card {{
      top: 92px;
      left: 18px;
      width: min(360px, calc(100vw - 36px));
      border-radius: 6px;
      overflow: hidden;
    }}
    .weather-inner {{
      padding: 16px;
    }}
    .temp-row {{
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 12px;
    }}
    .temp {{
      font-size: 50px;
      line-height: 1;
      color: var(--cyan-soft);
      font-weight: 800;
    }}
    .desc {{
      color: var(--amber);
      font-size: 14px;
      text-transform: uppercase;
      text-align: right;
    }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      margin-top: 14px;
    }}
    .metric {{
      border: 1px solid rgba(0, 212, 255, 0.22);
      background: rgba(0, 212, 255, 0.055);
      border-radius: 4px;
      padding: 10px;
      min-width: 0;
    }}
    .metric span {{
      display: block;
      color: var(--muted);
      font-size: 11px;
      text-transform: uppercase;
    }}
    .metric strong {{
      display: block;
      color: var(--text);
      font-size: 16px;
      margin-top: 3px;
      overflow-wrap: anywhere;
    }}
    .control-card {{
      top: 92px;
      right: 18px;
      width: min(260px, calc(100vw - 36px));
      border-radius: 6px;
      padding: 12px;
      pointer-events: auto;
    }}
    .control-title {{
      color: var(--cyan);
      font-size: 12px;
      text-transform: uppercase;
      margin-bottom: 10px;
    }}
    .layer-btn, .fs-btn {{
      width: 100%;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin: 7px 0;
      border: 1px solid rgba(0, 212, 255, 0.25);
      border-radius: 4px;
      padding: 10px 11px;
      background: rgba(0, 18, 28, 0.84);
      color: var(--text);
      cursor: pointer;
      font-size: 14px;
    }}
    .layer-btn.active {{
      border-color: var(--cyan);
      color: var(--cyan-soft);
      box-shadow: inset 0 0 18px rgba(0, 212, 255, 0.12);
    }}
    .dot {{
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: var(--muted);
      box-shadow: 0 0 10px currentColor;
      flex: 0 0 auto;
    }}
    .active .dot {{
      background: var(--cyan);
      color: var(--cyan);
    }}
    .fs-btn {{
      margin-top: 12px;
      border-color: rgba(255, 176, 0, 0.36);
      color: #ffe0a3;
    }}
    .scanline {{
      position: fixed;
      inset: 0;
      z-index: 650;
      pointer-events: none;
      background: repeating-linear-gradient(
        to bottom,
        rgba(0, 212, 255, 0.035),
        rgba(0, 212, 255, 0.035) 1px,
        transparent 1px,
        transparent 6px
      );
      mix-blend-mode: screen;
    }}
    .leaflet-control-zoom a {{
      background: rgba(0, 18, 28, 0.95) !important;
      color: var(--cyan-soft) !important;
      border-color: rgba(0, 212, 255, 0.4) !important;
    }}
    .leaflet-control-attribution {{
      background: rgba(0, 7, 11, 0.72) !important;
      color: var(--muted) !important;
    }}
    @media (max-width: 740px) {{
      .topbar {{
        top: 10px;
        left: 10px;
        right: 10px;
      }}
      .brand {{
        min-width: 0;
        width: 100%;
      }}
      .weather-card {{
        top: 84px;
        left: 10px;
      }}
      .control-card {{
        left: 10px;
        right: auto;
        top: auto;
        bottom: 16px;
        width: calc(100vw - 44px);
      }}
      .layer-btn {{
        width: calc(50% - 5px);
        display: inline-flex;
        margin: 5px 2px;
      }}
      .temp {{
        font-size: 42px;
      }}
    }}
  </style>
</head>
<body>
  <div id="map"></div>
  <div class="scanline"></div>
  <div class="hud topbar">
    <div class="brand">
      <div class="label">JARVIS Weather Map Mode</div>
      <div class="city" id="cityName">--</div>
    </div>
  </div>
  <section class="hud weather-card">
    <div class="weather-inner">
      <div class="temp-row">
        <div class="temp" id="temp">--</div>
        <div class="desc" id="description">Awaiting telemetry</div>
      </div>
      <div class="metrics">
        <div class="metric"><span>Feels Like</span><strong id="feels">--</strong></div>
        <div class="metric"><span>Humidity</span><strong id="humidity">--</strong></div>
        <div class="metric"><span>Wind</span><strong id="wind">--</strong></div>
        <div class="metric"><span>Pressure</span><strong id="pressure">--</strong></div>
      </div>
    </div>
  </section>
  <aside class="hud control-card" aria-label="Weather layer controls">
    <div class="control-title">OpenWeather Layers</div>
    <button class="layer-btn active" data-layer="precipitation_new"><span>Rain / Radar</span><span class="dot"></span></button>
    <button class="layer-btn" data-layer="temp_new"><span>Temperature</span><span class="dot"></span></button>
    <button class="layer-btn" data-layer="wind_new"><span>Wind</span><span class="dot"></span></button>
    <button class="layer-btn" data-layer="clouds_new"><span>Clouds</span><span class="dot"></span></button>
    <button class="layer-btn" data-layer="pressure_new"><span>Pressure</span><span class="dot"></span></button>
    <button class="fs-btn" id="fullscreenBtn" type="button">Toggle Full Screen</button>
  </aside>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const boot = {payload_json};
    const map = L.map("map", {{
      zoomControl: true,
      preferCanvas: true
    }}).setView([boot.lat, boot.lon], 8);

    L.tileLayer("https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png", {{
      maxZoom: 19,
      attribution: '&copy; OpenStreetMap &copy; CARTO'
    }}).addTo(map);

    const marker = L.circleMarker([boot.lat, boot.lon], {{
      radius: 9,
      color: "#00d4ff",
      fillColor: "#00d4ff",
      fillOpacity: 0.58,
      weight: 2
    }}).addTo(map);

    let weatherLayer;
    const layerMeta = {{
      precipitation_new: {{ opacity: 0.78 }},
      temp_new: {{ opacity: 0.62 }},
      wind_new: {{ opacity: 0.70 }},
      clouds_new: {{ opacity: 0.64 }},
      pressure_new: {{ opacity: 0.62 }}
    }};

    function setWeatherLayer(layerName) {{
      if (weatherLayer) map.removeLayer(weatherLayer);
      const meta = layerMeta[layerName] || {{ opacity: 0.65 }};
      weatherLayer = L.tileLayer(
        `https://tile.openweathermap.org/map/${{layerName}}/{{z}}/{{x}}/{{y}}.png?appid=${{boot.apiKey}}`,
        {{ opacity: meta.opacity, maxZoom: 19, attribution: "OpenWeather" }}
      ).addTo(map);
      document.querySelectorAll(".layer-btn").forEach((btn) => {{
        btn.classList.toggle("active", btn.dataset.layer === layerName);
      }});
    }}

    function pretty(value, fallback = "--") {{
      return value === undefined || value === null || Number.isNaN(value) ? fallback : value;
    }}

    function updateOverlay(data) {{
      const main = data.main || {{}};
      const wind = data.wind || {{}};
      const weather = (data.weather && data.weather[0]) || {{}};
      const name = data.name || boot.city || "Selected Location";
      document.getElementById("cityName").textContent = name;
      document.getElementById("temp").textContent = `${{Math.round(pretty(main.temp, 0))}}°C`;
      document.getElementById("description").textContent = weather.description || "Live weather";
      document.getElementById("feels").textContent = `${{Math.round(pretty(main.feels_like, 0))}}°C`;
      document.getElementById("humidity").textContent = `${{pretty(main.humidity)}}%`;
      document.getElementById("wind").textContent = `${{pretty(wind.speed)}} m/s`;
      document.getElementById("pressure").textContent = `${{pretty(main.pressure)}} hPa`;
    }}

    async function fetchWeather(lat, lon) {{
      const url = `https://api.openweathermap.org/data/2.5/weather?lat=${{lat}}&lon=${{lon}}&units=metric&appid=${{boot.apiKey}}`;
      const response = await fetch(url);
      if (!response.ok) throw new Error(`OpenWeather ${{response.status}}`);
      return response.json();
    }}

    map.on("click", async (event) => {{
      marker.setLatLng(event.latlng);
      try {{
        updateOverlay(await fetchWeather(event.latlng.lat, event.latlng.lng));
      }} catch (error) {{
        document.getElementById("description").textContent = error.message;
      }}
    }});

    document.querySelectorAll(".layer-btn").forEach((btn) => {{
      btn.addEventListener("click", () => setWeatherLayer(btn.dataset.layer));
    }});
    document.getElementById("fullscreenBtn").addEventListener("click", async () => {{
      if (!document.fullscreenElement) {{
        await document.documentElement.requestFullscreen();
      }} else {{
        await document.exitFullscreen();
      }}
      setTimeout(() => map.invalidateSize(), 160);
    }});

    updateOverlay(boot.weather);
    setWeatherLayer("precipitation_new");
  </script>
</body>
</html>
"""
    target = Path(tempfile.gettempdir()) / "jarvis_weather_map.html"
    target.write_text(markup, encoding="utf-8")
    return target


def _log(message: str, player=None) -> None:
    print(f"[Weather] {message}")
    if player:
        try:
            player.write_log(f"JARVIS: {message}")
        except Exception:
            pass
