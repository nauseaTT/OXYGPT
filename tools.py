"""External tool implementations for the AI service.

This module provides standalone async functions that are exposed to the
Gemini/OpenAI models as callable tools:

- get_market_data: Fetch OHLC candle data from FCSAPI for forex/crypto.
- generate_image: Generate images via freegen.app API with style presets
  and optional real-time progress callbacks.
- search_web: Execute Google searches via Gemini's built-in search tool,
  returning both the result text and grounding source metadata.
- generate_html_booklet: Create professional RTL HTML documents with
  themes, OXYGPT branding, and responsive CSS.
"""

import json
import httpx
import time
import os
import re
import logging
import asyncio
import base64
import uuid
import tempfile
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple, List

try:
    import aiohttp
    import aiofiles
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from datetime import timedelta
    NY_TZ = timezone(timedelta(hours=-5))   # Fallback EST
else:
    NY_TZ = ZoneInfo("America/New_York")

# ---------- Dedicated Logging Setup ----------
os.makedirs("logs", exist_ok=True)
logger = logging.getLogger("tools")
logger.setLevel(logging.INFO)
if not logger.handlers:
    file_handler = logging.FileHandler("logs/tools.log", encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

FCSAPI_TOKEN: str = os.environ.get("FCSAPI_TOKEN", "")
CACHE_FILE: str = "market_data_cache.json"

# Import the modular chart generator
from chart_generator import generate_candlestick_chart


# ---------- File-Based Cache CRUD Operations ----------
def _load_cache() -> Dict[str, Any]:
    """
    Loads the market data cache from the local JSON file.

    Returns:
        Dict[str, Any]: The loaded cache dictionary.
    """
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Error loading cache file: {e}")
    return {}


def _save_cache(cache_data: Dict[str, Any]) -> None:
    """
    Saves the market data cache to the local JSON file.

    Args:
        cache_data (Dict[str, Any]): The cache dictionary to save.
    """
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error saving cache file: {e}")


def get_market_data(
    symbol: str,
    timeframe: str = "1h",
    limit: int = 80,
    category: str = "forex",
    user_id: int = 0
) -> str:
    """
    Fetch historical OHLC candle data from FCSAPI and return exactly the
    requested number of most-recent candles in chronological order.

    Intended for: trend evaluation, liquidity analysis, order blocks,
    fair value gaps, market structure, session Q-phase identification,
    SSMT divergence, and trade planning.

    WHEN TO CALL:
    - When the user requests price history or market analysis for a symbol.
    - When fresh data is needed that is not already in conversation history.
    - For SSMT/PSP analysis requiring a correlated asset: call this tool
        a second time with the correlated symbol (same timeframe, same limit).

    DO NOT CALL if the required data is already visible in the recent
    conversation — reuse it to save tokens.

    TIMEFRAME CACHE DELAYS:
    1m  / 5m  → real-time (no delay)
    15m       → ~7-minute delay
    30m / 1h  → ~15-minute delay
    2h  / 4h  → ~90-minute delay
    1D        → ~6-hour delay
    1W        → ~24-hour delay
    1M        → ~1-week delay

    Args:
        symbol (str):
            Instrument in EXCHANGE:TICKER format.
            Forex examples  : FX:EURUSD, FX:GBPUSD, FX:USDJPY
            Crypto examples : BINANCE:BTCUSDT, BINANCE:ETHUSDT

        timeframe (str):
            Candle timeframe. Supported:
            "1m", "5m", "15m", "30m", "1h", "2h", "4h", "1D", "1W", "1M"

        limit (int):
            Number of the most-recent candles to return (1–300).
            The API fetches up to 300; this value slices the newest `limit`
            rows. Default: 80.

        category (str):
            Asset category. Supported: "forex", "crypto".

        user_id (int):
            Telegram user ID. When non-zero, a candlestick chart image is
            generated locally. Do not modify or guess this value.

    Returns:
        A CSV string structured as follows:

            TICKER,timeframe
            t,o,h,l,c
            <t>,<o>,<h>,<l>,<c>
            ...

        ── Row ordering ──────────────────────────────────────────────
        CHRONOLOGICAL: oldest candle first, newest candle last.
        First data row → OLDEST candle in the returned slice.
        Last  data row → MOST RECENT (current) candle.

        ── Timestamp format (column 't') ─────────────────────────────
        ISO 8601 datetime strings in New York local time (America/New_York).
        Timezone is pre-converted — no decoding needed. Read directly.

        Example:
            "2026-05-15 10:00:00" → candle opened at 10:00 AM New York time.

        To get current market time: read the LAST row's 't' value.

        ── Tool call limit ────────────────────────────────────────────
        You MUST NOT call this tool more than 3 times per conversation
        turn. After 3 calls, use the data you already have — do not
        call it again.

        ── Latest candle note ────────────────────────────────────────
        The last row (most recent candle) may be still-forming.
        Use its 'c' as current price.
        Treat its 'h' and 'l' as provisional until the candle closes.
        Use the second-to-last row for confirmed closed-candle structure.
    """
    # ---------- Validation ----------
    if not FCSAPI_TOKEN:
        logger.error("FCSAPI_TOKEN is not set. Please set it in environment variables or .env file.")
        return "Error: FCSAPI_TOKEN is not configured. Contact administrator."
    
    if not symbol or ":" not in symbol:
        logger.error(f"get_market_data validation failed: Invalid symbol format '{symbol}'")
        return "Error: Invalid symbol format. Expected EXCHANGE:TICKER (e.g., FX:EURUSD)."

    allowed_timeframes: Dict[str, Any] = {
        "1m": 1, "5m": 5, "15m": 15, "30m": 30,
        "1h": 60, "2h": 120, "4h": 240,
        "1D": "1D", "1W": "1W", "1M": "1M"
    }
    if timeframe not in allowed_timeframes:
        logger.error(f"get_market_data validation failed: Unsupported timeframe '{timeframe}'")
        return f"Error: Unsupported timeframe '{timeframe}'."

    if category not in {"forex", "stock", "crypto"}:
        logger.error(f"get_market_data validation failed: Unsupported category '{category}'")
        return f"Error: Unsupported category '{category}'."

    # TTL (cache lifetime) per timeframe, in seconds
    timeframe_ttl: Dict[str, int] = {
        "1m": 0, "5m": 0,           # real-time
        "15m": 420,                  # 7 min
        "30m": 900, "1h": 900,       # 15 min
        "2h": 5400, "4h": 5400,      # 90 min
        "1D": 21600,                 # 6 hours
        "1W": 86400,                 # 1 day
        "1M": 604800                 # 1 week
    }
    ttl: int = timeframe_ttl.get(timeframe, 900)

    exchange, ticker = symbol.split(":", 1)

    # ---------- Cache: key does NOT include limit ----------
    cache_key_str: str = f"{symbol}||{timeframe}||{category}||full||v2"
    now: float = time.time()

    cache_data = _load_cache()
    cleaned_cache = {}
    for k, v in cache_data.items():
        if isinstance(v, list) and len(v) == 2:
            exp, data_str = v
            if now < exp:
                cleaned_cache[k] = v

    full_csv: Optional[str] = None

    if ttl > 0 and cache_key_str in cleaned_cache:
        exp, cached_str = cleaned_cache[cache_key_str]
        if now < exp:
            lines = [line for line in cached_str.strip().split("\n") if line.strip()]
            if len(lines) > 2:   # must have header + at least one candle
                logger.info(f"Cache hit (full data) for {cache_key_str}")
                _save_cache(cleaned_cache)
                full_csv = cached_str
            else:
                logger.warning(f"Removing corrupted cache entry for {cache_key_str}")
                cleaned_cache.pop(cache_key_str, None)

    # ---------- Fetch from API if no valid cache ----------
    if full_csv is None:
        url = f"https://api-v4.fcsapi.com/{category}/history"
        params = {
            "access_key": FCSAPI_TOKEN,
            "symbol": ticker,
            "period": allowed_timeframes[timeframe],
            "limit": 300
            # No 'limit' param → API returns its maximum (≈300 candles)
        }
        logger.info(f"Requesting FCSAPI: URL={url} Params={params}")

        try:
            response = httpx.get(url, params=params, timeout=28.0)
            logger.info(f"FCSAPI Response Status: {response.status_code}")
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as http_err:
            logger.error(f"HTTP error: {http_err}. Response: {http_err.response.text}")
            return "Error: Unable to retrieve market data at this moment."
        except httpx.RequestError as req_err:
            logger.error(f"Request error: {req_err}")
            return "Error: Unable to retrieve market data at this moment."

        if not data.get("status"):
            msg = data.get("msg", "No message")
            code = data.get("code", "No code")
            logger.error(f"API returned status=False. Code: {code}, Message: {msg}")
            return "Error: Unable to retrieve market data at this moment."

        candles = data.get("response")
        if candles is None:
            candles = []
        if isinstance(candles, dict):
            candles = list(candles.values())

        # ---------- Convert to CSV ----------
        csv_lines = [f"{ticker},{timeframe}", "t,o,h,l,c"]

        for candle in candles:
            if not isinstance(candle, dict):
                continue
            # Convert UTC timestamp to NY local time ISO 8601 string
            t_val = candle.get("t")
            t_ny_str = str(t_val)
            if t_val is not None:
                try:
                    dt_utc = datetime.fromtimestamp(int(t_val), tz=timezone.utc)
                    dt_ny = dt_utc.astimezone(NY_TZ)
                    # Format as ISO 8601 datetime string in NY time
                    t_ny_str = dt_ny.strftime("%Y-%m-%d %H:%M:%S")
                except Exception as tz_err:
                    logger.warning(f"Timezone conversion failed for timestamp {t_val}: {tz_err}")

            try:
                rnd = 5 if category == "forex" else 2
                o = round(float(candle.get("o", 0)), rnd)
                h = round(float(candle.get("h", 0)), rnd)
                l = round(float(candle.get("l", 0)), rnd)
                c = round(float(candle.get("c", 0)), rnd)
                csv_lines.append(f"{t_ny_str},{o},{h},{l},{c}")
            except Exception as parse_err:
                logger.warning(f"Failed to parse candle: {parse_err}")
                continue

        if len(csv_lines) <= 2:
            logger.error(f"No valid candles for {symbol}. Raw response: {data}")
            return "Error: No market data found or parsed from the API response."

        full_csv = "\n".join(csv_lines)

        # Store full data in cache
        if ttl > 0:
            cleaned_cache[cache_key_str] = [now + ttl, full_csv]
        _save_cache(cleaned_cache)

    # ---------- Slice to exact limit (most recent candles) ----------
    lines = full_csv.strip().split("\n")
    header_lines = lines[:2]
    data_lines = lines[2:]

    # data_lines are chronological (oldest first); we want the newest `limit`
    if len(data_lines) > limit:
        sliced_data = data_lines[-limit:]   # most recent candles
    else:
        sliced_data = data_lines

    result_csv: str = "\n".join(header_lines + sliced_data)

    # NOTE: Chart generation is handled by the async wrapper
    # (get_market_data_wrapper in api_http.py) so duplicate generation
    # here is removed to avoid doing the work twice (Bug #27).

    return result_csv


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    csv_out = print(get_market_data("FX:EURUSD", limit=100).count('\n'))


# ============================================================
# IMAGE GENERATION MODULE
# ============================================================

# ---------- Style Presets ----------
STYLE_PRESETS: Dict[str, str] = {
    "realistic": (
        "photorealistic, high detail, 8K resolution, professional photography, "
        "sharp focus, natural lighting, studio quality"
    ),
    "anime": (
        "anime style, vibrant colors, detailed, studio ghibli inspired, "
        "clean lineart, cel shading, expressive"
    ),
    "cartoon": (
        "cartoon style, colorful, fun, Pixar-like rendering, "
        "smooth shading, exaggerated features"
    ),
    "oil_painting": (
        "oil painting style, textured brushstrokes, classical art, "
        "rich colors, canvas texture visible"
    ),
    "digital_art": (
        "digital art, concept art, detailed illustration, "
        "professional digital painting, trending on artstation"
    ),
    "minimal": (
        "minimalist design, clean lines, simple, modern aesthetic, "
        "negative space, geometric"
    ),
    "3d_render": (
        "3D render, octane render, unreal engine 5, ray tracing, "
        "highly detailed, cinematic lighting"
    ),
    "watercolor": (
        "watercolor painting, soft edges, transparent layers, "
        "delicate colors, artistic, flowing"
    )
}

# ---------- API Configuration ----------
IMG_API_BASE_URL = "https://{}.freegen.app/"
IMG_API_WS_URL = "wss://websocket-bridge.freegen.app/ws"
IMG_API_TIMEOUT = 45
IMG_MAX_RETRIES = 3
IMG_RETRY_DELAY = 2


def detect_optimal_ratio(prompt: str) -> str:
    """
    Detect optimal aspect ratio based on prompt content.
    
    Rules:
    - Portrait/person -> 9:16 (vertical)
    - Landscape/nature -> 16:9 (horizontal)
    - Object/product -> 1:1 (square)
    - Default -> 4:3
    """
    prompt_lower = prompt.lower()

    portrait_keywords = [
        "portrait", "person", "man", "woman", "face", "head",
        "selfie", "body", "figure", "character", "human"
    ]
    if any(kw in prompt_lower for kw in portrait_keywords):
        return "9:16"

    landscape_keywords = [
        "landscape", "mountain", "ocean", "sky", "city", "street",
        "forest", "desert", "sunset", "sunrise", "horizon", "panorama"
    ]
    if any(kw in prompt_lower for kw in landscape_keywords):
        return "16:9"

    object_keywords = [
        "object", "product", "item", "logo", "icon", "symbol",
        "phone", "laptop", "shoe", "watch", "ring"
    ]
    if any(kw in prompt_lower for kw in object_keywords):
        return "1:1"

    return "4:3"


def enhance_prompt(user_prompt: str, style: str = "realistic") -> str:
    """
    Enhance user prompt for optimal image generation output.
    
    Adds style and quality modifiers only.
    """
    style_suffix = STYLE_PRESETS.get(style, STYLE_PRESETS["realistic"])
    return f"{user_prompt}, {style_suffix}"


async def generate_image(
    prompt: str,
    ratio: str = "4:3",
    user_id: int = 0,
    style: str = "realistic",
    enhance: bool = True,
    on_progress: Optional[Any] = None,
) -> str:
    """Generate an image from text with optimized quality.

    Args:
        prompt: Image description in English.
        ratio: Aspect ratio (1:1, 4:3, 16:9, 9:16, auto).
        user_id: Telegram user ID.
        style: Artistic style from STYLE_PRESETS.
        enhance: Whether to enhance the prompt.
        on_progress: Optional async callable ``(status: str, progress: float)``
            invoked with intermediate WebSocket progress updates from the
            image generation API.  Silently ignored if the API does not
            send progress messages.

    Returns:
        Path to saved JPEG file or "Error: ..." message.
    """
    if not HAS_AIOHTTP:
        return "Error: aiohttp is not installed. Run: pip install aiohttp aiofiles"

    final_prompt = enhance_prompt(prompt, style) if enhance else prompt

    if ratio == "auto":
        ratio = detect_optimal_ratio(prompt)

    last_error = None
    for attempt in range(IMG_MAX_RETRIES):
        try:
            result = await _generate_with_api(final_prompt, ratio, user_id, on_progress)
            if not result.startswith("Error:"):
                return result
            last_error = result
        except Exception as e:
            last_error = str(e)
            logger.warning(f"Image generation attempt {attempt + 1} failed: {e}")

        if attempt < IMG_MAX_RETRIES - 1:
            await asyncio.sleep(IMG_RETRY_DELAY)

    return f"Error: تصویر پس از {IMG_MAX_RETRIES} تلاش تولید نشد. {last_error}"


async def _generate_with_api(
    prompt: str,
    ratio: str,
    user_id: int,
    on_progress: Optional[Any] = None,
) -> str:
    """Generate image via the freegen.app API.

    Submits a generation job and subscribes to a WebSocket for the
    result.  Intermediate WebSocket messages (non-image) are logged
    and optionally reported via ``on_progress`` for real-time
    animation updates.

    Args:
        prompt: Enhanced prompt text.
        ratio: Aspect ratio string.
        user_id: Telegram user ID for filename uniqueness.
        on_progress: Optional async callback ``(status, progress)``
            for reporting intermediate progress to the animator.

    Returns:
        Path to the saved JPEG file, or "Error: ..." on failure.
    """
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=IMG_API_TIMEOUT)
    ) as session:
        sign_url = IMG_API_BASE_URL.format("prompt-signer")
        async with session.post(sign_url, json={"prompt": prompt}) as resp:
            resp.raise_for_status()
            sign_data = await resp.json()

        job_url = IMG_API_BASE_URL.format("image-generator")
        job_payload = {
            "prompt": sign_data["prompt"],
            "sig": sign_data["sig"],
            "ts": sign_data["ts"],
            "ratio_id": ratio
        }
        async with session.post(job_url, json=job_payload) as resp:
            resp.raise_for_status()
            job_data = await resp.json()

        ws_timeout = aiohttp.ClientTimeout(total=IMG_API_TIMEOUT)
        async with session.ws_connect(IMG_API_WS_URL, timeout=ws_timeout) as ws:
            await ws.send_json({
                "job_id": job_data["job_id"],
                "type": "subscribe"
            })

            async for message in ws:
                if message.type == aiohttp.WSMsgType.ERROR:
                    logger.error(f"WebSocket error: {ws.exception()}")
                    break

                if message.type != aiohttp.WSMsgType.TEXT:
                    continue

                try:
                    data = json.loads(message.data)
                except (json.JSONDecodeError, TypeError):
                    logger.warning(f"Invalid JSON received: {message.data[:100]}")
                    continue

                b64 = data.get("image_data")
                if not b64:
                    # Intermediate message — log and optionally report progress
                    status = data.get("status", data.get("type", ""))
                    pct = data.get("progress", data.get("percent", 0))
                    if status or pct:
                        logger.debug(f"WS progress: status={status}, pct={pct}")
                        if on_progress:
                            try:
                                await on_progress(str(status), float(pct) if pct else 0)
                            except Exception:
                                pass
                    continue

                if "," in b64:
                    _, b64 = b64.split(",", 1)

                image_bytes = base64.b64decode(b64)
                filename = f"generated_image_{user_id}_{uuid.uuid4().hex[:8]}.jpg"

                async with aiofiles.open(filename, "wb") as f:
                    await f.write(image_bytes)

                logger.info(f"Image generated: {filename} ({len(image_bytes)} bytes)")
                return filename

    return "Error: پاسخی از API دریافت نشد"


# ============================================================
# WEB SEARCH MODULE (Google Search via Gemini)
# ============================================================


async def search_web(client: Any, query: str, model_name: str = "gemini-2.5-flash") -> Dict[str, Any]:
    """Execute a web search using Gemini's built-in Google Search tool.

    Extracts grounding metadata (source URLs and titles) from the
    response when available, so the animator can display clickable
    source links during the waiting animation.

    Args:
        client: A genai.Client instance initialized with an API key.
        query: The search query string.
        model_name: The Gemini model to use for the search.

    Returns:
        Dict with keys:
            - ``text`` (str): The search result summary.
            - ``sources`` (list[dict]): Up to 3 source dicts, each
              with ``uri`` and ``title`` keys.
    """
    from google.genai import types

    current_date: str = datetime.now().strftime("%Y-%m-%d")
    response = await client.aio.models.generate_content(
        model=model_name,
        contents=[{"role": "user", "parts": [{"text": query}]}],
        config=types.GenerateContentConfig(
            system_instruction=(
                f"Today's date: {current_date}. "
                "You are a search assistant. Use Google Search to find "
                "accurate information and return a concise summary.\n"
                "NOTE: Pay attention to the date — prioritize the most "
                "recent and relevant information available."
            ),
            tools=[types.Tool(google_search={})]
        )
    )

    # Extract grounding metadata (source URLs) when available
    sources: List[Dict[str, str]] = []
    try:
        if response.candidates:
            candidate = response.candidates[0]
            metadata = getattr(candidate, "grounding_metadata", None)
            if metadata:
                chunks = getattr(metadata, "grounding_chunks", None) or []
                for chunk in chunks[:3]:
                    web = getattr(chunk, "web", None)
                    if web:
                        uri = getattr(web, "uri", "")
                        title = getattr(web, "title", "")
                        if uri:
                            sources.append({"uri": uri, "title": title or uri})
    except Exception as e:
        logger.debug(f"Failed to extract grounding metadata: {e}")

    return {"text": response.text or "", "sources": sources}


# ============================================================
# HTML BOOKLET GENERATOR
# ============================================================

# Theme CSS variable definitions
_BOOKLET_THEMES: Dict[str, Dict[str, str]] = {
    "wood": {
        "--primary": "#8B6914",
        "--primary-dark": "#5C4A1E",
        "--primary-light": "#FDF6E3",
        "--accent": "#C0392B",
        "--bg": "#FFFBF0",
        "--bg-alt": "#F5EFE0",
        "--text": "#2C1810",
        "--text-light": "#6B5B4F",
        "--border": "#D4C4A8",
        "--code-bg": "#F0E8D8",
        "--header-from": "#3D2B1F",
        "--header-to": "#5C4A1E",
    },
    "blue": {
        "--primary": "#1a73e8",
        "--primary-dark": "#0d47a1",
        "--primary-light": "#e8f0fe",
        "--accent": "#34a853",
        "--bg": "#ffffff",
        "--bg-alt": "#f8f9fa",
        "--text": "#202124",
        "--text-light": "#5f6368",
        "--border": "#dadce0",
        "--code-bg": "#f1f3f4",
        "--header-from": "#0d47a1",
        "--header-to": "#1565c0",
    },
    "green": {
        "--primary": "#0d904f",
        "--primary-dark": "#0b6b3a",
        "--primary-light": "#e6f4ea",
        "--accent": "#1a73e8",
        "--bg": "#ffffff",
        "--bg-alt": "#f0faf4",
        "--text": "#202124",
        "--text-light": "#5f6368",
        "--border": "#ceead6",
        "--code-bg": "#e8f5e9",
        "--header-from": "#0b6b3a",
        "--header-to": "#0d904f",
    },
    "purple": {
        "--primary": "#7c3aed",
        "--primary-dark": "#5b21b6",
        "--primary-light": "#ede9fe",
        "--accent": "#ec4899",
        "--bg": "#ffffff",
        "--bg-alt": "#faf5ff",
        "--text": "#1f2937",
        "--text-light": "#6b7280",
        "--border": "#ddd6fe",
        "--code-bg": "#f3e8ff",
        "--header-from": "#4c1d95",
        "--header-to": "#7c3aed",
    },
    "dark": {
        "--primary": "#60a5fa",
        "--primary-dark": "#93c5fd",
        "--primary-light": "#1e3a5f",
        "--accent": "#34d399",
        "--bg": "#111827",
        "--bg-alt": "#1f2937",
        "--text": "#f9fafb",
        "--text-light": "#9ca3af",
        "--border": "#374151",
        "--code-bg": "#1e293b",
        "--header-from": "#0f172a",
        "--header-to": "#1e293b",
    },
}

# Auto theme detection keywords
_AUTO_THEME_KEYWORDS: Dict[str, list] = {
    "dark": ["code", "python", "javascript", "api", "برنامه", "کد", "css", "html"],
    "green": ["سبز", "سلامت", "environment", "green", "طبیعت"],
    "purple": ["فلسفه", "هنر", "art", "philosophy", "ادبیات"],
    "blue": ["آبی", "blue", "科技"],
}

# HTML sanitization patterns
_DANGEROUS_PATTERNS = [
    r'<\s*script[^>]*>.*?<\s*/\s*script\s*>',
    r'<\s*iframe[^>]*>.*?<\s*/\s*iframe\s*>',
    r'<\s*object[^>]*>.*?<\s*/\s*object\s*>',
    r'<\s*embed[^>]*>.*?<\s*/\s*embed\s*>',
    r'<\s*form[^>]*>.*?<\s*/\s*form\s*>',
    r'<\s*script[^>]*/\s*>',
    r'<\s*iframe[^>]*/\s*>',
    r'on\w+\s*=\s*["\'][^"\']*["\']',
    r'javascript\s*:',
]


def _resolve_booklet_theme(theme: str, title: str, html_content: str) -> str:
    """Resolve theme name: auto-detect from content or validate against known themes."""
    if theme in _BOOKLET_THEMES:
        return theme
    combined = (title.lower() + " " + html_content[:500].lower())
    for theme_name, keywords in _AUTO_THEME_KEYWORDS.items():
        if any(k in combined for k in keywords):
            return theme_name
    return "wood"


def _sanitize_html(html_content: str) -> str:
    """Remove dangerous HTML tags and attributes."""
    sanitized = html_content
    for pattern in _DANGEROUS_PATTERNS:
        sanitized = re.sub(pattern, '', sanitized, flags=re.DOTALL | re.IGNORECASE)
    return sanitized


def _build_booklet_document(sanitized: str, safe_title: str, css_vars: Dict[str, str]) -> str:
    """Build the complete HTML document string."""
    var_block = "; ".join(f"{k}: {v}" for k, v in css_vars.items())
    current_date = datetime.now().strftime("%Y-%m-%d")

    return f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{safe_title}</title>
  <link href="https://fonts.googleapis.com/css2?family=Vazirmatn:wght@300;400;500;600;700;800;900&display=swap" rel="stylesheet">
  <style>
    :root {{ {var_block}; }}

    * {{ margin: 0; padding: 0; box-sizing: border-box; }}

    body {{
        font-family: 'Vazirmatn', Tahoma, Arial, sans-serif;
        background: var(--bg);
        color: var(--text);
        line-height: 1.9;
        max-width: 900px;
        margin: 0 auto;
    }}

    /* ── هدر OXYGPT ── */
    .oxy-header {{
        background: linear-gradient(135deg, var(--header-from), var(--header-to));
        color: #fff;
        padding: 2.5rem 2rem 2rem;
        border-radius: 0 0 20px 20px;
        margin-bottom: 2rem;
        position: relative;
        overflow: hidden;
    }}
    .oxy-header::before {{
        content: '';
        position: absolute;
        top: 0; left: 0; right: 0; bottom: 0;
        background: url("data:image/svg+xml,%3Csvg width='60' height='60' viewBox='0 0 60 60' xmlns='http://www.w3.org/2000/svg'%3E%3Cg fill='none' fill-rule='evenodd'%3E%3Cg fill='%23ffffff' fill-opacity='0.03'%3E%3Cpath d='M36 34v-4h-2v4h-4v2h4v4h2v-4h4v-2h-4zm0-30V0h-2v4h-4v2h4v4h2V6h4V4h-4zM6 34v-4H4v4H0v2h4v4h2v-4h4v-2H6zM6 4V0H4v4H0v2h4v4h2V6h4V4H6z'/%3E%3C/g%3E%3C/g%3E%3C/svg%3E");
        opacity: 0.5;
    }}
    .oxy-header-inner {{
        position: relative;
        z-index: 1;
    }}
    .oxy-brand {{
        display: flex;
        align-items: center;
        gap: 1rem;
        margin-bottom: 1rem;
    }}
    .oxy-logo {{
        font-size: 1.4rem;
        font-weight: 800;
        letter-spacing: 2px;
    }}
    .oxy-bot-link {{
        color: rgba(255,255,255,0.7);
        font-size: 0.85rem;
        text-decoration: none;
        border-bottom: 1px dashed rgba(255,255,255,0.4);
        transition: color 0.2s;
    }}
    .oxy-bot-link:hover {{
        color: #fff;
        border-bottom-color: #fff;
    }}
    .oxy-title {{
        font-size: 2rem;
        font-weight: 700;
        margin: 0.5rem 0;
        color: #fff;
        border: none;
        padding: 0;
    }}
    .oxy-meta {{
        font-size: 0.85rem;
        opacity: 0.7;
        margin-top: 0.5rem;
    }}

    /* ── محتوای اصلی ── */
    .content {{
        padding: 0 2rem 2rem;
    }}

    /* ── تایپوگرافی ── */
    h1 {{
        color: var(--primary);
        font-size: 1.8rem;
        font-weight: 700;
        margin: 2rem 0 1rem;
        padding-bottom: 0.5rem;
        border-bottom: 2px solid var(--border);
    }}
    h2 {{
        color: var(--primary-dark);
        font-size: 1.4rem;
        font-weight: 600;
        margin: 2rem 0 1rem;
        padding: 0.5rem 1rem;
        border-right: 4px solid var(--primary);
        background: var(--primary-light);
        border-radius: 0 8px 8px 0;
    }}
    h3 {{
        color: var(--text);
        font-size: 1.15rem;
        font-weight: 600;
        margin: 1.5rem 0 0.75rem;
    }}
    h4, h5, h6 {{
        color: var(--text-light);
        font-weight: 600;
        margin: 1rem 0 0.5rem;
    }}
    p {{
        margin-bottom: 1rem;
        text-align: justify;
    }}
    ul, ol {{
        margin-right: 1.5rem;
        margin-bottom: 1rem;
    }}
    li {{
        margin-bottom: 0.4rem;
    }}
    strong {{
        color: var(--primary-dark);
        font-weight: 700;
    }}
    a {{
        color: var(--primary);
        text-decoration: none;
        border-bottom: 1px dashed var(--primary);
        transition: border-bottom 0.2s;
    }}
    a:hover {{
        border-bottom-style: solid;
    }}
    hr {{
        border: none;
        border-top: 2px solid var(--border);
        margin: 2rem 0;
    }}

    /* ── بلوک نقل‌قول ── */
    blockquote {{
        background: var(--primary-light);
        border-right: 4px solid var(--primary);
        padding: 1rem 1.5rem;
        margin: 1rem 0;
        border-radius: 0 10px 10px 0;
        font-style: italic;
    }}

    /* ── کدبلاک ── */
    code {{
        background: var(--code-bg);
        padding: 0.15rem 0.45rem;
        border-radius: 5px;
        font-family: 'Courier New', monospace;
        font-size: 0.88em;
        direction: ltr;
        unicode-bidi: isolate;
    }}
    pre {{
        background: #1e293b;
        color: #e2e8f0;
        border-radius: 10px;
        padding: 1.25rem;
        overflow-x: auto;
        margin: 1rem 0;
        direction: ltr;
        text-align: left;
        box-shadow: 0 4px 12px rgba(0,0,0,0.15);
    }}
    pre code {{
        background: none;
        color: inherit;
        padding: 0;
        font-size: 0.85em;
        line-height: 1.7;
    }}

    /* ── جداول ── */
    table {{
        width: 100%;
        border-collapse: collapse;
        margin: 1.5rem 0;
        font-size: 0.95em;
        border-radius: 10px;
        overflow: hidden;
        box-shadow: 0 2px 8px rgba(0,0,0,0.06);
    }}
    th {{
        background: linear-gradient(135deg, var(--header-from, var(--primary-dark)), var(--header-to, var(--primary)));
        color: #fff;
        padding: 0.85rem 1rem;
        text-align: right;
        font-weight: 600;
    }}
    td {{
        padding: 0.7rem 1rem;
        border-bottom: 1px solid var(--border);
        text-align: right;
    }}
    tr:nth-child(even) {{
        background: var(--bg-alt);
    }}
    tr:hover {{
        background: var(--primary-light);
    }}

    /* ── کارت‌ها ── */
    .card {{
        background: var(--bg);
        border: 1px solid var(--border);
        border-radius: 14px;
        padding: 1.5rem;
        margin: 1.25rem 0;
        box-shadow: 0 2px 12px rgba(0,0,0,0.06);
        border-right: 5px solid var(--primary);
        transition: box-shadow 0.2s;
    }}
    .card:hover {{
        box-shadow: 0 4px 20px rgba(0,0,0,0.1);
    }}
    .card h3 {{
        margin-top: 0;
        color: var(--primary);
    }}

    /* ── بخش‌های collapsible ── */
    details {{
        background: var(--bg-alt);
        border: 1px solid var(--border);
        border-radius: 10px;
        padding: 1rem 1.25rem;
        margin: 1rem 0;
    }}
    summary {{
        cursor: pointer;
        font-weight: 600;
        color: var(--primary);
        user-select: none;
    }}
    summary:hover {{
        color: var(--primary-dark);
    }}
    details[open] summary {{
        margin-bottom: 0.75rem;
        padding-bottom: 0.5rem;
        border-bottom: 1px solid var(--border);
    }}

    /* ── باکس نکات و هشدارها ── */
    .tip {{
        background: #e8f8f0;
        border-right: 5px solid #27ae60;
        padding: 1rem 1.5rem;
        margin: 1rem 0;
        border-radius: 0 12px 12px 0;
    }}
    .tip::before {{
        content: '💡 ';
    }}
    .warning {{
        background: #fef9e7;
        border-right: 5px solid #f39c12;
        padding: 1rem 1.5rem;
        margin: 1rem 0;
        border-radius: 0 12px 12px 0;
    }}
    .warning::before {{
        content: '⚠️ ';
    }}

    /* ── هایلایت ── */
    .highlight {{
        background: var(--primary-light);
        padding: 0.1rem 0.4rem;
        border-radius: 4px;
    }}

    /* ── فوتر OXYGPT ── */
    .oxy-footer {{
        background: linear-gradient(135deg, var(--header-from, #3D2B1F), var(--header-to, #5C4A1E));
        color: rgba(255,255,255,0.8);
        padding: 1.5rem 2rem;
        border-radius: 20px 20px 0 0;
        margin-top: 3rem;
        text-align: center;
    }}
    .oxy-footer-inner {{
        display: flex;
        justify-content: center;
        align-items: center;
        gap: 1.5rem;
        flex-wrap: wrap;
    }}
    .oxy-footer a {{
        color: rgba(255,255,255,0.6);
        font-size: 0.85rem;
        text-decoration: none;
        border-bottom: 1px dashed rgba(255,255,255,0.3);
    }}
    .oxy-footer a:hover {{
        color: #fff;
        border-bottom-color: #fff;
    }}

    /* ── پرینت ── */
    @media print {{
        body {{ padding: 0; font-size: 11pt; }}
        .oxy-header {{ border-radius: 0; }}
        h2 {{ page-break-after: avoid; }}
        details, pre, table, .card {{ break-inside: avoid; }}
        @page {{ margin: 2cm; }}
    }}

    /* ── موبایل ── */
    @media (max-width: 768px) {{
        .content {{ padding: 0 1rem 1rem; }}
        .oxy-header {{ padding: 1.5rem 1rem 1.25rem; }}
        .oxy-title {{ font-size: 1.5rem; }}
        h2 {{ font-size: 1.2rem; }}
        table {{ font-size: 0.85em; }}
        .card {{ padding: 1rem; }}
    }}
  </style>
</head>
<body>

<!-- هدر OXYGPT -->
<header class="oxy-header">
  <div class="oxy-header-inner">
    <div class="oxy-brand">
      <span class="oxy-logo">✦ OXYGPT</span>
      <a href="https://t.me/OXYGPTxBot" class="oxy-bot-link">@OXYGPTxBot</a>
    </div>
    <h1 class="oxy-title">{safe_title}</h1>
    <div class="oxy-meta">📅 {current_date}</div>
  </div>
</header>

<main class="content">
  {sanitized}
</main>

<!-- فوتر OXYGPT -->
<footer class="oxy-footer">
  <div class="oxy-footer-inner">
    <span>ساخته شده توسط <strong>OXYGPT</strong></span>
    <a href="https://t.me/OXYGPTxBot">@OXYGPTxBot</a>
  </div>
</footer>

</body>
</html>"""


async def generate_html_booklet(
    html_content: str,
    title: str = "جزوه",
    theme: str = "auto",
    user_id: int = 0,
    db_manager: Any = None,
) -> Tuple[str, Optional[str]]:
    """
    Generate a professional HTML booklet/document and save it as a downloadable file.

    Creates beautiful, RTL-optimized HTML documents with Persian fonts,
    responsive layout, and print-friendly styling.

    IMPORTANT RULES:
    - html_content should contain ONLY the body content (NO html/head/body/doctype tags)
    - Use semantic HTML: <h2> for section headers, <p> for text, <ul>/<ol> for lists
    - Use <table> for tabular data, <pre><code> for code blocks
    - Use <details><summary> for collapsible sections
    - Use class="card" for highlighted cards, class="tip" for tips, class="warning" for warnings
    - The tool wraps your content with proper document structure, fonts, and CSS
    - Generate as much or as little content as the topic requires — you decide the length
    - Focus on quality and completeness over arbitrary length

    YOUR TEXT RESPONSE after calling this tool will be sent as the file caption.
    Keep it BRIEF (2-3 lines max). The file content speaks for itself.

    AVAILABLE THEMES:
    - "wood": Classic modernized (warm wood tones, default)
    - "blue": Professional blue
    - "green": Nature green (environment, health)
    - "purple": Creative purple (arts, philosophy)
    - "dark": Dark mode (coding, technical)
    - "auto": Choose the best theme based on the topic

    Args:
        html_content: The HTML body content (WITHOUT html/head/body tags)
        title: Document title (used for filename and <title> tag, in English/ASCII)
        theme: Color theme - "auto", "wood", "blue", "green", "purple", or "dark"
        user_id: Telegram user ID for usage tracking (0 to skip)
        db_manager: Database manager instance for usage tracking (None to skip)

    Returns:
        Tuple of (message, file_path):
        - message: Summary string on success, or "Error: ..." on failure
        - file_path: Absolute path to the generated HTML file, or None on failure
    """
    # ── Check HTML usage limit ──
    if db_manager:
        html_count = db_manager.get_html_usage(user_id)
        sub_type = db_manager.get_setting(f"sub_{user_id}", "free")
        max_html = 25 if sub_type == "paid" else 10
        if html_count >= max_html:
            return f"Error: شما به سقف مجاز فایلهای HTML ({max_html}) رسیدهاید. لطفاً بعداً تلاش کنید.", None

    # ── Validation ──
    if not html_content or len(html_content.strip()) < 100:
        return "Error: Content too short. Provide substantial HTML content (at least 100 characters).", None

    if len(html_content) > 2_000_000:
        return "Error: Content too large. Maximum 2MB of HTML content.", None

    # ── Sanitize ──
    try:
        sanitized = _sanitize_html(html_content)
    except Exception as e:
        logger.error(f"[HTML TOOL] Sanitization failed: {e}")
        return f"Error: خطا در sanitize محتوا: {str(e)}", None
    if len(sanitized.strip()) < 50:
        return "Error: Content became empty after sanitization. Avoid using script/iframe tags.", None

    # ── Theme ──
    try:
        theme = _resolve_booklet_theme(theme, title, html_content)
    except Exception as e:
        logger.error(f"[HTML TOOL] Theme resolution failed: {e}")
        return f"Error: خطا در تعیین تم: {str(e)}", None
    css_vars = _BOOKLET_THEMES[theme]

    # ── Build document ──
    safe_title = re.sub(r'[^\w\s-]', '', title).strip()[:60] or "booklet"
    filename = f"booklet_{safe_title}_{uuid.uuid4().hex[:8]}.html"
    try:
        document = _build_booklet_document(sanitized, safe_title, css_vars)
    except Exception as e:
        logger.error(f"[HTML TOOL] Document build failed: {e}")
        return f"Error: خطا در ساخت سند: {str(e)}", None

    # ── Increment usage AFTER validation, before file save ──
    # Bug #31: Previously the increment happened before validation,
    # so an exception in sanitize/theme/build would leak the count.
    if db_manager:
        db_manager.increment_html_usage(user_id)

    # ── Save to file ──
    try:
        filepath = os.path.join(tempfile.gettempdir(), filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(document)
        file_size = os.path.getsize(filepath)
        logger.info(f"[HTML TOOL] Generated booklet: {filepath} ({file_size} bytes, theme={theme})")

        section_count = len(re.findall(r'<h[23][^>]*>', sanitized, re.IGNORECASE))
        message = (
            f"✅ جزوه HTML با موفقیت ساخته شد:\n"
            f"• عنوان: {title}\n"
            f"• قالب رنگی: {theme}\n"
            f"• تعداد بخش‌ها: {section_count}\n"
            f"• حجم: {file_size / 1024:.1f} KB\n"
            f"• فایل ذخیره شد: {filename}\n\n"
            f"فایل آماده ارسال است."
        )
        return message, filepath
    except Exception as e:
        logger.error(f"[HTML TOOL] Failed to save file: {e}")
        if db_manager:
            db_manager.decrement_html_usage(user_id)
        return f"Error: خطا در ذخیره فایل HTML: {str(e)}", None
