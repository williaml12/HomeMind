import os
import io
import csv
import json
import math
import time
import base64
import queue
import threading
from collections import deque
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from PIL import Image

from arduino.app_utils import App, Bridge
from arduino.app_bricks.web_ui import WebUI
from arduino.app_peripherals.camera import Camera
from arduino.app_bricks.video_objectdetection import VideoObjectDetection
from arduino.app_bricks.image_classification import ImageClassification
from arduino.app_bricks.dbstorage_tsstore import TimeSeriesStore
from arduino.app_bricks.weather_forecast import WeatherForecast


# ============================================================
# CONFIGURATION
# ============================================================

CAMERA_WIDTH = 640
CAMERA_HEIGHT = 480

FACE_CONFIDENCE = 0.60

CLASSIFICATION_THRESHOLD = 0.80
CLASSIFICATION_COOLDOWN = 5.0

ONE_RESULT_PER_MOTION = True

FACE_PADDING_X = 0.40
FACE_PADDING_Y = 0.40

MOTION_COOLDOWN = 3.0

REPORT_INTERVAL_SECONDS = 60 * 60

TREND_THRESHOLD_F = 1.0

TEMP_ALERT_C = 35.0
HUMIDITY_ALERT = 80.0


# ============================================================
# TIMEZONE
# ============================================================

TIMEZONE = ZoneInfo("America/New_York")


# ============================================================
# FILES
# ============================================================

SNAPSHOT_FOLDER = "stranger_snapshots"
DATA_FOLDER = "dashboard_data"

HISTORY_FILE = os.path.join(
    DATA_FOLDER,
    "sensor_history.csv"
)

EVENT_FILE = os.path.join(
    DATA_FOLDER,
    "security_events.json"
)

MAX_HISTORY = 1000
MAX_EVENTS = 50


os.makedirs(SNAPSHOT_FOLDER, exist_ok=True)
os.makedirs(DATA_FOLDER, exist_ok=True)


# ============================================================
# TELEGRAM CONFIGURATION
# ============================================================

BOT_TOKEN = "YOUR_BOT_TOKEN"
CHAT_ID = "YOUR_CHAT_ID"


def telegram_configured():

    return (
        BOT_TOKEN.strip() != ""
        and
        CHAT_ID.strip() != ""
        and
        BOT_TOKEN != "YOUR_BOT_TOKEN"
        and
        CHAT_ID != "YOUR_CHAT_ID"
    )


def telegram_url(method):

    return (
        "https://api.telegram.org/"
        f"bot{BOT_TOKEN}/"
        f"{method}"
    )


# ============================================================
# TELEGRAM SESSION (Connection Pooling + Retries)
# ============================================================

telegram_session = requests.Session()

retry_strategy = Retry(
    total=3,
    backoff_factor=0.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["POST", "GET"]
)

adapter = HTTPAdapter(
    max_retries=retry_strategy,
    pool_connections=5,
    pool_maxsize=10
)

telegram_session.mount("https://", adapter)


# ============================================================
# GLOBAL LOCKS
# ============================================================

state_lock = threading.Lock()

events_lock = threading.Lock()

classification_lock = threading.Lock()

face_data_lock = threading.Lock()


# ============================================================
# DASHBOARD STATE
# ============================================================

dashboard_state = {

    "temperature": None,

    "humidity": None,

    "heat_index": None,

    "trend": "➡️ Stable",

    "sensor_ok": False,

    "pir_active": False,

    "door_open": False,

    "system_armed": True,

    "security_led": False,

    "notifications_enabled": True,

    "last_sensor_update": None,

    # Climate monitoring additions
    "aqi": None,
    "aqi_level": "Unknown",
    "weather": "unknown",
    "weather_description": "Unknown"
}


# ============================================================
# SECURITY STATE
# ============================================================

pir_active = False

classification_completed_for_motion = False

classification_running = False

last_classification_time = 0.0

last_motion_notification_time = 0.0


# ============================================================
# SENSOR STATE
# ============================================================

pir_initialized = False

door_initialized = False

last_report_time = time.time()

temp_alert_sent = False

humidity_alert_sent = False


# ============================================================
# DATABASE
# ============================================================

db = TimeSeriesStore()


# ============================================================
# HEAT INDEX HISTORY
# ============================================================

last_three_heat_indexes = deque(maxlen=3)


# ============================================================
# HOURLY READINGS
# ============================================================

hourly_temperature_readings = []

hourly_humidity_readings = []

hourly_heat_index_readings = []


# ============================================================
# FACE DATA
# ============================================================

latest_face_box = None

latest_face_confidence = 0.0


# ============================================================
# EVENTS
# ============================================================

def load_events():

    if not os.path.exists(EVENT_FILE):
        return []

    try:

        with open(
            EVENT_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(file)

        if isinstance(data, list):
            return data

    except Exception as exc:

        print(
            f"[APP] Event history load error: {exc}",
            flush=True
        )

    return []


events = load_events()


# ============================================================
# TIME HELPERS
# ============================================================

def local_now():

    return datetime.now(TIMEZONE)


def iso_time():

    return local_now().isoformat(
        timespec="seconds"
    )


def readable_time():

    return local_now().strftime(
        "%B %d, %Y at %I:%M:%S %p %Z"
    )


# ============================================================
# LOGGING
# ============================================================

def log(message):

    print(
        f"[APP] {message}",
        flush=True
    )


# ============================================================
# SAVE EVENTS
# ============================================================

def save_events():

    try:

        with open(
            EVENT_FILE,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                events[-MAX_EVENTS:],
                file,
                indent=2
            )

    except Exception as exc:

        log(
            f"Event save error: {exc}"
        )


# ============================================================
# SENSOR HISTORY
# ============================================================

def save_sensor_history(
    temperature,
    humidity
):

    exists = os.path.exists(
        HISTORY_FILE
    )

    try:

        with open(
            HISTORY_FILE,
            "a",
            newline="",
            encoding="utf-8"
        ) as file:

            writer = csv.writer(file)

            if not exists:

                writer.writerow(
                    [
                        "timestamp",
                        "temperature",
                        "humidity"
                    ]
                )

            writer.writerow(
                [
                    iso_time(),
                    f"{temperature:.2f}",
                    f"{humidity:.2f}"
                ]
            )

    except Exception as exc:

        log(
            f"Sensor history save error: {exc}"
        )


# ============================================================
# LOAD SENSOR HISTORY
# ============================================================

def load_sensor_history():

    history = []

    if not os.path.exists(
        HISTORY_FILE
    ):

        return history

    try:

        with open(
            HISTORY_FILE,
            "r",
            newline="",
            encoding="utf-8"
        ) as file:

            reader = csv.DictReader(file)

            for row in reader:

                try:

                    temperature = float(
                        row.get(
                            "temperature",
                            ""
                        )
                    )

                    humidity = float(
                        row.get(
                            "humidity",
                            ""
                        )
                    )

                    timestamp = row.get(
                        "timestamp",
                        ""
                    )

                    history.append(
                        {
                            "timestamp":
                                timestamp,

                            "temperature":
                                temperature,

                            "humidity":
                                humidity
                        }
                    )

                except (
                    ValueError,
                    TypeError
                ):

                    continue

    except Exception as exc:

        log(
            f"Sensor history load error: {exc}"
        )

    if len(history) > MAX_HISTORY:

        history = history[
            -MAX_HISTORY:
        ]

    return history


# ============================================================
# IMAGE HELPERS
# ============================================================

def convert_to_pil(frame):

    if frame is None:
        return None

    if isinstance(frame, Image.Image):

        return frame.copy()

    try:

        return Image.fromarray(frame)

    except Exception as exc:

        log(
            f"Image conversion error: {exc}"
        )

        return None


def image_to_data_url(
    image,
    max_width=480,
    quality=55
):

    try:

        output = image.copy()

        if output.width > max_width:

            scale = (
                max_width /
                float(output.width)
            )

            output = output.resize(
                (
                    max_width,
                    int(
                        output.height *
                        scale
                    )
                ),
                Image.LANCZOS
            )

        buffer = io.BytesIO()

        output.save(
            buffer,
            format="JPEG",
            quality=quality,
            optimize=True
        )

        encoded = base64.b64encode(
            buffer.getvalue()
        ).decode("utf-8")

        return (
            "data:image/jpeg;base64,"
            + encoded
        )

    except Exception as exc:

        log(
            f"Image encoding error: {exc}"
        )

        return None


# ============================================================
# SECURITY EVENT
# ============================================================

def create_security_event(
    event_type,
    message,
    severity="info",
    image=None,
    confidence=None
):

    image_data = None

    if image is not None:

        image_data = image_to_data_url(
            image
        )

    event = {

        "timestamp":
            iso_time(),

        "display_time":
            readable_time(),

        "type":
            str(event_type),

        "message":
            str(message),

        "severity":
            str(severity),

        "confidence":
            confidence,

        "image":
            image_data
    }

    with events_lock:

        events.append(event)

        while len(events) > MAX_EVENTS:

            events.pop(0)

        save_events()

    log(
        f"WEBUI EVENT: {event_type}"
    )

    try:

        ui.send_message(
            "security_event",
            event
        )

    except Exception as exc:

        log(
            f"WebUI event error: {exc}"
        )


# ============================================================
# HEAT INDEX
# ============================================================

def calculate_heat_index(
    temp_f,
    humidity
):

    if temp_f < 80.0:

        return temp_f

    simple_hi = (

        0.5 *
        (
            temp_f
            + 61.0
            + (
                (temp_f - 68.0)
                * 1.2
            )
            + (
                humidity
                * 0.094
            )
        )
    )

    if simple_hi < 80.0:

        return simple_hi

    heat_index = (

        -42.379

        + (
            2.04901523 *
            temp_f
        )

        + (
            10.14333127 *
            humidity
        )

        - (
            0.22475541 *
            temp_f *
            humidity
        )

        - (
            0.00683783 *
            temp_f *
            temp_f
        )

        - (
            0.05481717 *
            humidity *
            humidity
        )

        + (
            0.00122874 *
            temp_f *
            temp_f *
            humidity
        )

        + (
            0.00085282 *
            temp_f *
            humidity *
            humidity
        )

        - (
            0.00000199 *
            temp_f *
            temp_f *
            humidity *
            humidity
        )
    )

    # Low humidity adjustment

    if (
        humidity < 13
        and
        80 <= temp_f <= 112
    ):

        adjustment = (

            (
                13.0 - humidity
            )
            / 4.0
        ) * (

            (
                17.0 -
                abs(temp_f - 95.0)
            )
            / 17.0
        )

        heat_index -= adjustment

    # High humidity adjustment

    elif (
        humidity > 85
        and
        80 <= temp_f <= 87
    ):

        adjustment = (

            (
                humidity - 85.0
            )
            / 10.0
        ) * (

            (
                87.0 - temp_f
            )
            / 5.0
        )

        heat_index += adjustment

    return heat_index


# ============================================================
# TREND
# ============================================================

def calculate_trend():

    if len(
        last_three_heat_indexes
    ) < 3:

        return "➡️ Stable"

    first = (
        last_three_heat_indexes[0]
    )

    latest = (
        last_three_heat_indexes[-1]
    )

    difference = latest - first

    if difference > TREND_THRESHOLD_F:

        return "📈 Rising"

    if difference < -TREND_THRESHOLD_F:

        return "📉 Falling"

    return "➡️ Stable"


# ============================================================
# UPDATE ENVIRONMENT
# ============================================================

def update_environment_state(
    temperature,
    humidity
):

    temp_f = (
        temperature *
        9.0 /
        5.0
    ) + 32.0

    heat_index_f = calculate_heat_index(
        temp_f,
        humidity
    )

    last_three_heat_indexes.append(
        heat_index_f
    )

    trend = calculate_trend()

    with state_lock:

        dashboard_state[
            "temperature"
        ] = temperature

        dashboard_state[
            "humidity"
        ] = humidity

        dashboard_state[
            "heat_index"
        ] = heat_index_f

        dashboard_state[
            "trend"
        ] = trend

        dashboard_state[
            "sensor_ok"
        ] = True

        dashboard_state[
            "last_sensor_update"
        ] = iso_time()

    return (
        temp_f,
        heat_index_f,
        trend
    )


# ============================================================
# TELEGRAM MESSAGE QUEUE (Non-blocking)
# ============================================================

telegram_queue = queue.Queue()

telegram_sender_running = True


def telegram_sender_worker():
    """Background worker that sends Telegram messages without blocking."""

    log("📨 Telegram sender thread started")

    while telegram_sender_running:

        try:

            item = telegram_queue.get(timeout=1)

            if item is None:
                break

            chat_id, text, photo_path = item

            try:

                if photo_path:

                    # Send photo
                    with open(photo_path, "rb") as photo:

                        response = telegram_session.post(

                            telegram_url("sendPhoto"),

                            data={
                                "chat_id": chat_id,
                                "caption": text
                            },

                            files={"photo": photo},

                            timeout=15
                        )

                else:

                    # Send text message
                    response = telegram_session.post(

                        telegram_url("sendMessage"),

                        data={
                            "chat_id": chat_id,
                            "text": text
                        },

                        timeout=10
                    )

                if response.ok:

                    log(
                        f"✅ Telegram message sent "
                        f"(queue: {telegram_queue.qsize()})"
                    )

                else:

                    log(
                        f"⚠️ Telegram send failed: "
                        f"{response.text[:200]}"
                    )

            except Exception as exc:

                log(f"Telegram send error: {exc}")

            telegram_queue.task_done()

        except queue.Empty:

            continue

        except Exception as exc:

            log(f"Telegram worker error: {exc}")

    log("📨 Telegram sender thread stopped")


# Start worker thread
telegram_worker_thread = threading.Thread(
    target=telegram_sender_worker,
    daemon=True,
    name="TelegramSender"
)

telegram_worker_thread.start()


# ============================================================
# TELEGRAM SENDERS (Non-blocking)
# ============================================================

def send_telegram_message(chat_id, text):
    """Queue a text message for sending (non-blocking, ~1ms)."""

    if not telegram_configured():

        log("Telegram not configured; message skipped.")

        return False

    telegram_queue.put((chat_id, text, None))

    log(f"📤 Queued Telegram message (queue: {telegram_queue.qsize()})")

    return True


def send_telegram_photo(filename, confidence):
    """Queue a photo for sending (non-blocking, ~1ms)."""

    if not telegram_configured():

        log("Telegram not configured; photo skipped.")

        return False

    caption = (
        f"🚨 SECURITY CAMERA ALERT\n\n"
        f"🚨 STRANGER DETECTED\n\n"
        f"Confidence: {confidence * 100:.2f}%\n"
        f"Time: {readable_time()}"
    )

    telegram_queue.put((CHAT_ID, caption, filename))

    log(f"📤 Queued Telegram photo (queue: {telegram_queue.qsize()})")

    return True


# ============================================================
# ENVIRONMENT CALLBACK
# ============================================================

def on_environment(
    temperature,
    humidity
):

    global last_report_time
    global temp_alert_sent
    global humidity_alert_sent

    try:

        temperature = float(
            temperature
        )

        humidity = float(
            humidity
        )

        (
            temp_f,
            heat_index_f,
            trend
        ) = update_environment_state(
            temperature,
            humidity
        )

        save_sensor_history(
            temperature,
            humidity
        )

        hourly_temperature_readings.append(
            temp_f
        )

        hourly_humidity_readings.append(
            humidity
        )

        hourly_heat_index_readings.append(
            heat_index_f
        )

        log(
            f"🌡️ DHT11: "
            f"{temperature:.1f} °C | "
            f"{humidity:.1f} %"
        )

        log(
            f"🔥 Heat Index: "
            f"{heat_index_f:.1f} °F | "
            f"Trend: {trend}"
        )

        # ----------------------------------------------------
        # WEBUI
        # ----------------------------------------------------

        try:

            ui.send_message(
                "sensor_update",
                {
                    "temperature":
                        temperature,

                    "humidity":
                        humidity,

                    "heat_index":
                        heat_index_f,

                    "trend":
                        trend,

                    "sensor_ok":
                        True,

                    "timestamp":
                        iso_time()
                }
            )

        except Exception as exc:

            log(
                f"WebUI sensor error: {exc}"
            )

        # ----------------------------------------------------
        # TEMPERATURE ALERT
        # ----------------------------------------------------

        if (
            temperature > TEMP_ALERT_C
            and
            not temp_alert_sent
        ):

            temp_alert_sent = True

            send_telegram_message(

                CHAT_ID,

                "⚠️ HIGH TEMPERATURE!\n\n"
                f"{temperature:.2f}°C / "
                f"{temp_f:.2f}°F\n"
                f"🔥 Heat Index: "
                f"{heat_index_f:.2f}°F"
            )

        elif temperature <= TEMP_ALERT_C:

            temp_alert_sent = False

        # ----------------------------------------------------
        # HUMIDITY ALERT
        # ----------------------------------------------------

        if (
            humidity > HUMIDITY_ALERT
            and
            not humidity_alert_sent
        ):

            humidity_alert_sent = True

            send_telegram_message(

                CHAT_ID,

                "💧 HIGH HUMIDITY!\n\n"
                f"Humidity: "
                f"{humidity:.1f}%\n"
                f"🔥 Heat Index: "
                f"{heat_index_f:.2f}°F"
            )

        elif humidity <= HUMIDITY_ALERT:

            humidity_alert_sent = False

        # ----------------------------------------------------
        # HOURLY REPORT
        # ----------------------------------------------------

        now = time.time()

        if (
            now - last_report_time
            >=
            REPORT_INTERVAL_SECONDS
        ):

            last_report_time = now

            send_hourly_report()

    except Exception as exc:

        log(
            f"DHT callback error: {exc}"
        )


# ============================================================
# RECORD SENSOR SAMPLES (for climate dashboard)
# ============================================================

def record_sensor_samples(celsius, humidity):
    """Legacy function for climate monitoring system"""

    try:

        T = float(celsius)

        RH = float(humidity)

    except (ValueError, TypeError):

        print("Unable to convert sensor values:", celsius, humidity)

        return

    if math.isnan(T) or math.isnan(RH):

        print("NaN sensor samples:", T, RH)

        return

    RH = max(0.0, min(RH, 100.0))

    ts = int(datetime.now().timestamp() * 1000)

    # Temperature in Fahrenheit
    fahrenheit = (T * 9.0 / 5.0) + 32.0

    # Database
    db.write_sample("temperature", fahrenheit, ts)

    db.write_sample("humidity", RH, ts)

    # Realtime dashboard
    ui.send_message("temperature", {"value": fahrenheit, "ts": ts})

    ui.send_message("humidity", {"value": RH, "ts": ts})

    # Dew Point
    dew_point_f = None

    if RH > 0.0:

        a = 17.27

        b = 237.7

        rh_frac = max(min(RH, 100.0), 1e-6)

        gamma = ((a * T) / (b + T)) + math.log(rh_frac / 100.0)

        denominator = (a - gamma)

        if denominator != 0:

            dew_point_c = (b * gamma) / denominator

            dew_point_f = (dew_point_c * 9.0 / 5.0) + 32.0

    if dew_point_f is not None:

        db.write_sample("dew_point", dew_point_f, ts)

        ui.send_message("dew_point", {"value": dew_point_f, "ts": ts})

    # Heat Index
    T_f = fahrenheit

    R = max(min(RH, 100.0), 0.0)

    if T_f < 80.0:

        heat_index_f = T_f

    else:

        heat_index_f = (
            -42.379 + 2.04901523 * T_f + 10.14333127 * R -
            0.22475541 * T_f * R - 0.00683783 * T_f * T_f -
            0.05481717 * R * R + 0.00122874 * T_f * T_f * R +
            0.00085282 * T_f * R * R - 0.00000199 * T_f * T_f * R * R
        )

    db.write_sample("heat_index", heat_index_f, ts)

    ui.send_message("heat_index", {"value": heat_index_f, "ts": ts})

    # Absolute Humidity
    absolute_humidity = None

    denominator = 273.15 + T

    if RH >= 0.0 and denominator != 0:

        es = 6.112 * math.exp((17.67 * T) / (T + 243.5))

        absolute_humidity = es * (R / 100.0) * 2.1674 / denominator

    if absolute_humidity is not None:

        db.write_sample("absolute_humidity", float(absolute_humidity), ts)

        ui.send_message("absolute_humidity", {"value": float(absolute_humidity), "ts": ts})

    # Combined climate status
    ui.send_message("climate_status", {
        "temperature": fahrenheit,
        "humidity": RH,
        "dew_point": dew_point_f,
        "heat_index": heat_index_f,
        "absolute_humidity": absolute_humidity,
        "ts": ts
    })

    print(f"Indoor Temperature: {fahrenheit:.1f} °F")

    print(f"Indoor Humidity: {RH:.1f} %")

    if dew_point_f is not None:

        print(f"Dew Point: {dew_point_f:.1f} °F")

    print(f"Heat Index: {heat_index_f:.1f} °F")

    if absolute_humidity is not None:

        print(f"Absolute Humidity: {absolute_humidity:.2f} g/m³")

    print()

    # Periodic outdoor update
    get_air_quality()

    get_weather_forecast(CITY)


# ============================================================
# AQI FUNCTIONS
# ============================================================

CITY = "Brooklyn"

API_TOKEN = "demo"

AQI_ENDPOINT = f"https://api.waqi.info/feed/{CITY}/?token={API_TOKEN}"

AQI_LEVELS = [
    {"min": 0, "max": 50, "description": "Good"},
    {"min": 51, "max": 100, "description": "Moderate"},
    {"min": 101, "max": 150, "description": "Unhealthy for Sensitive Groups"},
    {"min": 151, "max": 200, "description": "Unhealthy"},
    {"min": 201, "max": 300, "description": "Very Unhealthy"},
    {"min": 301, "max": 500, "description": "Hazardous"}
]


def map_aqi_level(aqi_value: int) -> str:
    for level in AQI_LEVELS:
        if level["min"] <= aqi_value <= level["max"]:
            return level["description"]
    return "Unknown"


def get_air_quality():
    try:
        response = requests.get(AQI_ENDPOINT, timeout=10)
        response.raise_for_status()
        response_json = response.json()
        status = response_json.get("status")
        data = response_json.get("data")
        
        if status != "ok" or not data:
            print("AQI API Error:", response_json)
            return "Unknown"
        
        aqi = data.get("aqi", -1)
        if aqi == "-" or aqi is None:
            print("AQI value unavailable.")
            return "Unknown"
        
        try:
            aqi = int(aqi)
        except (ValueError, TypeError):
            print("Invalid AQI value:", aqi)
            return "Unknown"
        
        aqi_level = map_aqi_level(aqi)
        
        with state_lock:
            dashboard_state["aqi"] = aqi
            dashboard_state["aqi_level"] = aqi_level
        
        print(f"Outdoor AQI: {aqi} ({aqi_level})")
        
        ui.send_message("air_quality", {
            "aqi": aqi,
            "level": aqi_level,
            "ts": int(time.time() * 1000)
        })
        
        return aqi_level
        
    except Exception as e:
        print(f"AQI request failed: {e}")
        return "Unknown"


# ============================================================
# WEATHER FUNCTIONS
# ============================================================

forecaster = WeatherForecast()


def get_weather_forecast(city: str):
    try:
        forecast = forecaster.get_forecast_by_city(city)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        with state_lock:
            dashboard_state["weather"] = forecast.category
            dashboard_state["weather_description"] = forecast.description
        
        print(f"[{now}] Weather for {city}: {forecast.description} -> {forecast.category}")
        
        ui.send_message("weather", {
            "category": forecast.category,
            "description": forecast.description,
            "ts": int(time.time() * 1000)
        })
        
        return forecast.category
        
    except Exception as e:
        print(f"Weather request failed: {e}")
        return "unknown"


# ============================================================
# MOTION NOTIFICATION
# ============================================================

def send_motion_notification():

    if not telegram_configured():

        log(
            "Motion notification skipped "
            "(Telegram not configured)."
        )

        return

    send_telegram_message(

        CHAT_ID,

        "🚨 MOTION DETECTED!\n\n"
        "PIR motion detected.\n"
        "💡 Motion LED active."
    )


# ============================================================
# DOOR NOTIFICATION
# ============================================================

def send_door_notification(event):

    if event == "opened":

        message = (
            "🚪 DOOR OPENED!\n\n"
            "Door sensor detected an "
            "open door.\n"
            "🔔 Buzzer may be active if armed."
        )

    elif event == "closed":

        message = (
            "🔒 DOOR CLOSED\n\n"
            "Door sensor detected the "
            "door is closed."
        )

    else:

        return

    send_telegram_message(
        CHAT_ID,
        message
    )


# ============================================================
# PIR CALLBACK
# ============================================================

def on_pir(value):

    global pir_active
    global classification_completed_for_motion
    global pir_initialized
    global last_motion_notification_time

    try:

        active = (int(value) == 1)

        previous = pir_active

        pir_active = active

        with state_lock:

            dashboard_state["pir_active"] = active

        # ----------------------------------------------------
        # INITIAL STATE
        # ----------------------------------------------------

        if not pir_initialized:

            pir_initialized = True

            log(
                "PIR initial state: "
                + ("MOTION" if active else "IDLE")
            )

            try:

                ui.send_message("pir_state", {"active": active})

            except Exception:
                pass

            return

        # ----------------------------------------------------
        # MOTION START
        # ----------------------------------------------------

        if active and not previous:

            classification_completed_for_motion = False

            log("🚨 PIR MOTION DETECTED")

            try:

                ui.send_message("pir_state", {"active": True})

            except Exception:
                pass

            create_security_event(
                "Motion Detected",
                "PIR motion detected.",
                "warning"
            )

            now = time.time()

            if (now - last_motion_notification_time >= MOTION_COOLDOWN):

                with state_lock:

                    notifications_enabled = dashboard_state["notifications_enabled"]

                if notifications_enabled:

                    send_motion_notification()

                    last_motion_notification_time = now

        # ----------------------------------------------------
        # MOTION END
        # ----------------------------------------------------

        elif (not active and previous):

            log("⏳ PIR motion ended.")

            try:

                ui.send_message("pir_state", {"active": False})

            except Exception:
                pass

    except Exception as exc:

        log(f"PIR callback error: {exc}")


# ============================================================
# DOOR CALLBACK
# ============================================================

def on_door(is_open):

    global door_initialized

    try:

        is_open = bool(is_open)

        with state_lock:

            previous = dashboard_state["door_open"]

            dashboard_state["door_open"] = is_open

        # ----------------------------------------------------
        # INITIAL STATE
        # ----------------------------------------------------

        if not door_initialized:

            door_initialized = True

            log(
                "Door initial state: "
                + ("OPEN" if is_open else "CLOSED")
            )

            try:

                ui.send_message("door_state", {"open": is_open})

            except Exception:
                pass

            return

        # ----------------------------------------------------
        # NO CHANGE
        # ----------------------------------------------------

        if previous == is_open:

            return

        # ----------------------------------------------------
        # STATE CHANGED
        # ----------------------------------------------------

        log("🚪 DOOR: " + ("OPEN" if is_open else "CLOSED"))

        try:

            ui.send_message("door_state", {"open": is_open})

        except Exception:
            pass

        if is_open:

            create_security_event(
                "Door Open",
                "Door sensor reports OPEN.",
                "warning"
            )

            send_door_notification("opened")

        else:

            create_security_event(
                "Door Closed",
                "Door sensor reports CLOSED.",
                "info"
            )

            send_door_notification("closed")

    except Exception as exc:

        log(f"Door callback error: {exc}")


# ============================================================
# HOURLY REPORT
# ============================================================

def send_hourly_report():

    if not hourly_temperature_readings:

        log("No readings available for hourly report.")

        return

    average_temperature = (
        sum(hourly_temperature_readings)
        / len(hourly_temperature_readings)
    )

    average_humidity = (
        sum(hourly_humidity_readings)
        / len(hourly_humidity_readings)
    )

    average_heat_index = (
        sum(hourly_heat_index_readings)
        / len(hourly_heat_index_readings)
    )

    trend = calculate_trend()

    report = (

        "📊 HOURLY COMFORT REPORT\n\n"

        "🌡 Average Temperature:\n"
        f"{average_temperature:.2f} °F\n\n"

        "💧 Average Humidity:\n"
        f"{average_humidity:.1f}%\n\n"

        "🔥 Average Heat Index:\n"
        f"{average_heat_index:.2f} °F\n\n"

        "📈 Comfort Trend:\n"
        f"{trend}\n\n"

        "📋 Readings Collected:\n"
        f"{len(hourly_temperature_readings)}"
    )

    print(
        "\n"
        "========================================\n"
        "           HOURLY REPORT\n"
        "========================================\n"
        f"{report}\n"
        "========================================\n",
        flush=True
    )

    send_telegram_message(CHAT_ID, report)

    hourly_temperature_readings.clear()

    hourly_humidity_readings.clear()

    hourly_heat_index_readings.clear()


# ============================================================
# FACE DETECTION METADATA
# ============================================================

def receive_detection_data(detections):

    global latest_face_box
    global latest_face_confidence

    try:

        if not isinstance(detections, dict):

            return

        faces = detections.get("face", [])

        if not faces:

            return

        face = faces[0]

        if not isinstance(face, dict):

            return

        box = face.get("bounding_box_xyxy")

        confidence = float(face.get("confidence", 0))

        if box is None:

            return

        with face_data_lock:

            latest_face_box = tuple(int(v) for v in box)

            latest_face_confidence = confidence

    except Exception as exc:

        log(f"Face metadata error: {exc}")


# ============================================================
# FACE DETECTED
# ============================================================

def face_detected():

    global classification_completed_for_motion

    if not pir_active:

        return

    with state_lock:

        armed = dashboard_state["system_armed"]

    if not armed:

        return

    if (ONE_RESULT_PER_MOTION and classification_completed_for_motion):

        return

    classification_completed_for_motion = True

    log("👤 FACE DETECTED")

    log("➡️ Classifying face...")

    threading.Thread(
        target=classify_and_process,
        daemon=True
    ).start()


# ============================================================
# SAVE STRANGER FACE
# ============================================================

def save_stranger_face(image, box, confidence):

    if box is None:

        log("⚠️ No face bounding box.")

        return None

    try:

        x1, y1, x2, y2 = box

        face_width = x2 - x1

        face_height = y2 - y1

        pad_x = int(face_width * FACE_PADDING_X)

        pad_y = int(face_height * FACE_PADDING_Y)

        crop_box = (
            max(0, x1 - pad_x),
            max(0, y1 - pad_y),
            min(image.width, x2 + pad_x),
            min(image.height, y2 + pad_y)
        )

        face_image = image.crop(crop_box)

        filename = (
            "stranger_"
            + local_now().strftime("%Y%m%d_%H%M%S")
            + "_"
            + str(int(confidence * 100))
            + ".jpg"
        )

        filepath = os.path.join(SNAPSHOT_FOLDER, filename)

        face_image.save(filepath, "JPEG", quality=95)

        log(f"📸 Stranger snapshot saved: {filepath}")

        return filepath

    except Exception as exc:

        log(f"❌ Snapshot error: {exc}")

        return None


# ============================================================
# CLASSIFICATION
# ============================================================

def classify_and_process():

    global classification_running
    global last_classification_time

    with classification_lock:

        if classification_running:

            log("Classification already running.")

            return

        classification_running = True

    try:

        now = time.monotonic()

        if (now - last_classification_time < CLASSIFICATION_COOLDOWN):

            log("Classification cooldown.")

            return

        last_classification_time = now

        log("📷 Capturing image...")

        frame = camera.capture()

        if frame is None:

            log("❌ Camera returned no frame.")

            return

        image = convert_to_pil(frame)

        if image is None:

            log("❌ Unable to convert camera frame.")

            return

        log(f"✅ Captured {image.width}x{image.height}")

        with face_data_lock:

            face_box = latest_face_box

            face_confidence = latest_face_confidence

        log(f"Face box: {face_box}")

        log(f"Face confidence: {face_confidence:.2f}")

        log("🧠 Running Family/Stranger classification...")

        results = image_classification.classify(
            image,
            image_type="jpeg",
            confidence=0.0
        )

        if not results:

            log("❌ Classification returned no result.")

            return

        classifications = results.get("classification", [])

        if not classifications:

            log("⚠️ No classification entries.")

            return

        family_confidence = 0.0

        stranger_confidence = 0.0

        for item in classifications:

            class_name = str(item.get("class_name", "")).lower().strip()

            confidence = float(item.get("confidence", 0))

            if confidence > 1.0:

                confidence /= 100.0

            log(f"{class_name}: {confidence * 100:.2f}%")

            if class_name == "family":

                family_confidence = confidence

            elif class_name == "stranger":

                stranger_confidence = confidence

        # ====================================================
        # FAMILY
        # ====================================================

        if (family_confidence >= CLASSIFICATION_THRESHOLD
                and family_confidence > stranger_confidence):

            log("🟢 FAMILY MEMBER")

            create_security_event(
                "Family Member",
                "Family member detected.",
                "info",
                image,
                family_confidence
            )

        # ====================================================
        # STRANGER
        # ====================================================

        elif (stranger_confidence >= CLASSIFICATION_THRESHOLD
                and stranger_confidence > family_confidence):

            log("🚨 STRANGER DETECTED")

            snapshot = save_stranger_face(image, face_box, stranger_confidence)

            create_security_event(
                "Stranger Detected",
                "Unknown person detected.",
                "critical",
                image,
                stranger_confidence
            )

            if snapshot:

                send_telegram_photo(snapshot, stranger_confidence)

        # ====================================================
        # UNCERTAIN
        # ====================================================

        else:

            confidence = max(family_confidence, stranger_confidence)

            log("⚠️ CLASSIFICATION UNCERTAIN")

            create_security_event(
                "Uncertain",
                "Face detected, but classification confidence was below threshold.",
                "warning",
                image,
                confidence
            )

    except Exception as exc:

        log(f"❌ CLASSIFICATION ERROR: {exc}")

    finally:

        classification_running = False


# ============================================================
# DASHBOARD STATE
# ============================================================

def send_dashboard_state():

    with state_lock:

        current_state = dict(dashboard_state)

    with events_lock:

        current_events = list(events[-MAX_EVENTS:])

    current_history = load_sensor_history()

    try:

        ui.send_message(

            "sync",

            {
                "state": current_state,
                "events": current_events,
                "history": current_history
            }
        )

    except Exception as exc:

        log(f"Dashboard sync error: {exc}")


# ============================================================
# WEBUI CONTROL
# ============================================================

def handle_control(client_id, data):

    if not isinstance(data, dict):

        return

    action = data.get("action")

    # --------------------------------------------------------
    # Browser startup
    # --------------------------------------------------------

    if action == "request_sync":

        send_dashboard_state()

        return

    # --------------------------------------------------------
    # ARM / DISARM
    # --------------------------------------------------------

    if action == "set_armed":

        enabled = bool(data.get("enabled", False))

        with state_lock:

            dashboard_state["system_armed"] = enabled

        log("Security: " + ("ARMED" if enabled else "DISARMED"))

        # ----------------------------------------------------
        # NOTIFY MCU TO UPDATE BUZZER STATE
        # ----------------------------------------------------

        try:

            result = Bridge.call(
                "set_system_armed",
                enabled,
                timeout=3
            )

            log(f"MCU armed state updated: {result}")

        except Exception as exc:

            log(f"⚠️ MCU armed state error: {exc}")

        send_dashboard_state()

        return

    # --------------------------------------------------------
    # NOTIFICATIONS
    # --------------------------------------------------------

    if action == "set_notifications":

        enabled = bool(data.get("enabled", False))

        with state_lock:

            dashboard_state["notifications_enabled"] = enabled

        send_dashboard_state()

        return

    # --------------------------------------------------------
    # LED
    # --------------------------------------------------------

    if action == "set_led":

        enabled = bool(data.get("enabled", False))

        try:

            result = Bridge.call(
                "set_security_led",
                enabled,
                timeout=3
            )

            with state_lock:

                dashboard_state["security_led"] = enabled

            log(f"LED command result: {result}")

            send_dashboard_state()

        except Exception as exc:

            log(f"⚠️ LED Bridge error: {exc}")

        return


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

def handle_command(chat_id, text):

    text = text.lower().strip()

    log(f"Telegram command: {text}")

    # --------------------------------------------------------
    # START
    # --------------------------------------------------------

    if text == "/start":

        with state_lock:

            dashboard_state["notifications_enabled"] = True

        send_telegram_message(

            chat_id,

            "🏠 Arduino UNO Q "
            "Smart Home Monitor\n\n"

            "🌡 DHT11\n"
            "🚨 PIR Motion\n"
            "🚪 Door Sensor\n"
            "🔔 Buzzer\n"
            "🤖 AI Face Detection\n"
            "👨‍👩‍👧 Family / Stranger\n\n"

            "Commands:\n\n"

            "/temp\n"
            "/humidity\n"
            "/heatindex\n"
            "/comfort\n"
            "/report\n\n"

            "/motion\n"
            "/door\n"
            "/status\n"
            "/buzzer\n\n"

            "/start\n"
            "/stop\n\n"

            "/motionledon\n"
            "/motionledoff\n"
        )

        return

    # --------------------------------------------------------
    # STOP
    # --------------------------------------------------------

    if text == "/stop":

        with state_lock:

            dashboard_state["notifications_enabled"] = False

        send_telegram_message(

            chat_id,

            "⏹️ Automatic motion "
            "and door notifications "
            "DISABLED."
        )

        return

    # --------------------------------------------------------
    # TEMP
    # --------------------------------------------------------

    if text == "/temp":

        with state_lock:

            temp_c = dashboard_state["temperature"]

        if temp_c is None:

            send_telegram_message(chat_id, "❌ DHT11 data unavailable.")

            return

        temp_f = (temp_c * 9.0 / 5.0) + 32.0

        send_telegram_message(

            chat_id,

            "🌡 Temperature:\n"
            f"{temp_c:.2f}°C / "
            f"{temp_f:.2f}°F"
        )

        return

    # --------------------------------------------------------
    # HUMIDITY
    # --------------------------------------------------------

    if text == "/humidity":

        with state_lock:

            humidity = dashboard_state["humidity"]

        if humidity is None:

            send_telegram_message(chat_id, "❌ DHT11 data unavailable.")

            return

        send_telegram_message(chat_id, f"💧 Humidity: {humidity:.1f}%")

        return

    # --------------------------------------------------------
    # HEAT INDEX
    # --------------------------------------------------------

    if text == "/heatindex":

        with state_lock:

            temp_c = dashboard_state["temperature"]

            humidity = dashboard_state["humidity"]

            heat_index = dashboard_state["heat_index"]

            trend = dashboard_state["trend"]

        if (temp_c is None or humidity is None or heat_index is None):

            send_telegram_message(chat_id, "❌ DHT11 data unavailable.")

            return

        temp_f = (temp_c * 9.0 / 5.0) + 32.0

        send_telegram_message(

            chat_id,

            "🔥 Heat Index:\n"
            f"{heat_index:.2f}°F\n\n"

            f"Temperature: {temp_f:.2f}°F\n"

            f"Humidity: {humidity:.1f}%\n\n"

            f"Trend: {trend}"
        )

        return

    # --------------------------------------------------------
    # COMFORT
    # --------------------------------------------------------

    if text == "/comfort":

        with state_lock:

            temp_c = dashboard_state["temperature"]

            humidity = dashboard_state["humidity"]

            heat_index = dashboard_state["heat_index"]

            trend = dashboard_state["trend"]

        if temp_c is None:

            send_telegram_message(chat_id, "❌ DHT11 data unavailable.")

            return

        temp_f = (temp_c * 9.0 / 5.0) + 32.0

        send_telegram_message(

            chat_id,

            "🌡 COMFORT STATUS\n\n"

            f"Temperature: {temp_c:.2f}°C / {temp_f:.2f}°F\n\n"

            f"Humidity: {humidity:.1f}%\n\n"

            f"🔥 Heat Index: {heat_index:.2f}°F\n\n"

            f"📈 Trend: {trend}"
        )

        return

    # --------------------------------------------------------
    # MOTION
    # --------------------------------------------------------

    if text == "/motion":

        with state_lock:

            active = dashboard_state["pir_active"]

        send_telegram_message(

            chat_id,

            "🚨 Motion Status\n\n"
            + ("ACTIVE" if active else "IDLE")
        )

        return

    # --------------------------------------------------------
    # DOOR
    # --------------------------------------------------------

    if text == "/door":

        with state_lock:

            is_open = dashboard_state["door_open"]

        send_telegram_message(

            chat_id,

            "🚪 Door Status\n\n"
            + ("OPEN" if is_open else "CLOSED")
        )

        return

    # --------------------------------------------------------
    # BUZZER
    # --------------------------------------------------------

    if text == "/buzzer":

        try:

            result = Bridge.call("get_buzzer_state", timeout=3)

            armed = Bridge.call("get_system_armed", timeout=3)

            send_telegram_message(

                chat_id,

                "🔔 Buzzer Status\n\n"
                f"State: {result}\n"
                f"System: {armed}"
            )

        except Exception as exc:

            log(f"Buzzer status error: {exc}")

            send_telegram_message(chat_id, "❌ Unable to get buzzer status.")

        return

    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

    if text == "/status":

        with state_lock:

            temp_c = dashboard_state["temperature"]

            humidity = dashboard_state["humidity"]

            heat_index = dashboard_state["heat_index"]

            trend = dashboard_state["trend"]

            motion = dashboard_state["pir_active"]

            door = dashboard_state["door_open"]

            armed = dashboard_state["system_armed"]

            notifications = dashboard_state["notifications_enabled"]

        if temp_c is not None:

            temp_f = (temp_c * 9.0 / 5.0) + 32.0

            environment = (

                f"Temperature: {temp_c:.2f}°C / {temp_f:.2f}°F\n"

                f"Humidity: {humidity:.1f}%\n"

                f"Heat Index: {heat_index:.2f}°F\n"

                f"Trend: {trend}"
            )

        else:

            environment = "DHT11 unavailable"

        status = (

            "🏠 SMART HOME STATUS\n\n"

            "🌡 ENVIRONMENT\n"
            f"{environment}\n\n"

            "🚪 DOOR\n"
            f"{'OPEN' if door else 'CLOSED'}\n\n"

            "🚨 MOTION\n"
            f"{'ACTIVE' if motion else 'IDLE'}\n\n"

            "🛡 SECURITY\n"
            f"{'ARMED' if armed else 'DISARMED'}\n\n"

            "🔔 Buzzer\n"
            f"{'ARMED + DOOR OPEN = ALARM' if armed and door else 'OFF'}\n\n"

            "🔔 Notifications\n"
            f"{'ON' if notifications else 'OFF'}"
        )

        send_telegram_message(chat_id, status)

        return

    # --------------------------------------------------------
    # LED ON
    # --------------------------------------------------------

    if text == "/motionledon":

        try:

            result = Bridge.call("set_led_command", "ON", timeout=3)

            with state_lock:

                dashboard_state["security_led"] = True

            send_telegram_message(chat_id, f"💡 {result}")

        except Exception as exc:

            log(f"LED command error: {exc}")

            send_telegram_message(chat_id, "❌ Unable to control LED.")

        return

    # --------------------------------------------------------
    # LED OFF
    # --------------------------------------------------------

    if text == "/motionledoff":

        try:

            result = Bridge.call("set_led_command", "OFF", timeout=3)

            with state_lock:

                dashboard_state["security_led"] = False

            send_telegram_message(chat_id, f"💡 {result}")

        except Exception as exc:

            log(f"LED command error: {exc}")

            send_telegram_message(chat_id, "❌ Unable to control LED.")

        return

    # --------------------------------------------------------
    # REPORT
    # --------------------------------------------------------

    if text == "/report":

        send_hourly_report()

        return

    # --------------------------------------------------------
    # HELP
    # --------------------------------------------------------

    if text == "/help":

        send_telegram_message(

            chat_id,

            "🏠 Smart Home Monitor\n\n"

            "/temp\n"
            "/humidity\n"
            "/heatindex\n"
            "/comfort\n"
            "/report\n\n"

            "/motion\n"
            "/door\n"
            "/status\n"
            "/buzzer\n\n"

            "/start\n"
            "/stop\n\n"

            "/motionledon\n"
            "/motionledoff"
        )

        return

    # --------------------------------------------------------
    # UNKNOWN
    # --------------------------------------------------------

    send_telegram_message(

        chat_id,

        "Unknown command.\n\n"
        "Send /help for commands."
    )


# ============================================================
# TELEGRAM
# ============================================================

last_update_id = 0


def clear_old_updates():

    global last_update_id

    if not telegram_configured():

        return

    try:

        response = telegram_session.get(

            telegram_url("getUpdates"),

            params={"offset": -1, "timeout": 1},

            timeout=10
        )

        data = response.json()

        if (data.get("ok") and data.get("result")):

            last_update_id = data["result"][-1]["update_id"]

            log("Old Telegram updates cleared.")

    except Exception as exc:

        log(f"Telegram queue clear error: {exc}")


def get_updates():

    global last_update_id

    if not telegram_configured():

        return []

    try:

        response = telegram_session.get(

            telegram_url("getUpdates"),

            params={
                "offset": last_update_id + 1,
                "timeout": 5,  # Long polling
                "allowed_updates": ["message"]
            },

            timeout=10  # HTTP timeout > long-poll timeout
        )

        data = response.json()

        if not data.get("ok"):

            log(f"Telegram update error: {data}")

            return []

        updates = data.get("result", [])

        if updates:

            last_update_id = updates[-1]["update_id"]

        return updates

    except requests.exceptions.Timeout:

        # Expected with long polling
        return []

    except Exception as exc:

        log(f"Telegram update error: {exc}")

        return []


def process_telegram():

    updates = get_updates()

    for update in updates:

        try:

            if "message" not in update:

                continue

            message = update["message"]

            chat_id = message["chat"]["id"]

            text = message.get("text", "").strip()

            if not text:

                continue

            handle_command(chat_id, text)

        except Exception as exc:

            log(f"Telegram command error: {exc}")


# ============================================================
# WEBUI
# ============================================================

log("Creating WebUI...")

ui = WebUI()

log("✅ WebUI created.")

ui.on_message("control", handle_control)


# ============================================================
# BRIDGE CALLBACKS
# ============================================================

log("Registering Bridge callbacks...")

Bridge.provide("on_environment", on_environment)

Bridge.provide("on_pir", on_pir)

Bridge.provide("on_door", on_door)

Bridge.provide("record_sensor_samples", record_sensor_samples)

Bridge.provide("get_air_quality", get_air_quality)

Bridge.provide("get_weather_forecast", get_weather_forecast)

log("✅ Bridge callbacks registered.")


# ============================================================
# DATABASE API
# ============================================================

def on_get_samples(resource: str, start: str, aggr_window: str):
    try:
        samples = db.read_samples(
            measure=resource,
            start_from=start,
            aggr_window=aggr_window,
            aggr_func="mean",
            limit=100
        )
        return [{"ts": sample[1], "value": sample[2]} for sample in samples]
    except Exception as e:
        print(f"Database error: {e}")
        return {"error": str(e)}

ui.expose_api("GET", "/get_samples/{resource}/{start}/{aggr_window}", on_get_samples)


# ============================================================
# CAMERA
# ============================================================

log("Initializing Camera...")

camera = Camera(
    resolution=(CAMERA_WIDTH, CAMERA_HEIGHT)
)

camera.start()

log("✅ Camera initialized.")


# ============================================================
# IMAGE CLASSIFICATION
# ============================================================

log("Initializing Image Classification...")

image_classification = ImageClassification()

log("✅ Image Classification initialized.")


# ============================================================
# FACE DETECTION
# ============================================================

log("Initializing Face Detection...")

detection_stream = VideoObjectDetection(
    camera=camera,
    confidence=FACE_CONFIDENCE,
    debounce_sec=1.0
)

log("✅ Face Detection initialized.")


# ============================================================
# DETECTION CALLBACKS
# ============================================================

detection_stream.on_detect("face", face_detected)

detection_stream.on_detect_all(receive_detection_data)


# ============================================================
# INITIAL OUTDOOR DATA
# ============================================================

print("Getting initial outdoor environment...")

get_air_quality()

get_weather_forecast(CITY)


# ============================================================
# STARTUP
# ============================================================

log("")
log("============================================================")
log("       UNIFIED UNO Q AI SECURITY DASHBOARD")
log("============================================================")

log("DHT11          : D2")
log("Motion LED     : D3")
log("Door LED       : D4")
log("BUZZER         : D5")
log("PIR            : D8")
log("Door Sensor    : D9")
log("Camera         : USB")
log("LED Matrix     : Q1/Q2 (Built-in)")
log("Face Detection : ENABLED")
log("Classification : FAMILY / STRANGER")
log("Buzzer         : ENABLED (Armed + Door Open)")
log("AQI            : ENABLED")
log("Weather        : ENABLED")
log("Heat Index     : ENABLED")
log("Trend          : ENABLED")
log("Hourly Report  : ENABLED")
log("Telegram       : " + ("CONFIGURED" if telegram_configured() else "NOT CONFIGURED"))
log("Bridge Mode    : MCU PUSH")
log("============================================================")
log("✅ ALL PYTHON COMPONENTS INITIALIZED")
log("Waiting for sensor data and PIR motion...")
log("============================================================")


# ============================================================
# SYNC ARMED STATE TO MCU ON STARTUP
# ============================================================

try:
    Bridge.call(
        "set_system_armed",
        dashboard_state["system_armed"],
        timeout=3
    )
    log("✅ Initial armed state synced to MCU")
except Exception as exc:
    log(f"⚠️ Could not sync armed state to MCU: {exc}")


# ============================================================
# TELEGRAM STARTUP
# ============================================================

if telegram_configured():

    clear_old_updates()

    send_telegram_message(

        CHAT_ID,

        "✅ Arduino UNO Q "
        "Smart Home Monitor ONLINE\n\n"

        "🌡 DHT11 monitoring enabled\n"
        "🔥 Heat Index monitoring enabled\n"
        "📈 Comfort trend enabled\n"
        "🚨 PIR motion detection enabled\n"
        "🚪 Door sensor monitoring enabled\n"
        "🔔 Buzzer alarm enabled (Armed + Door Open)\n"
        "🤖 AI face detection enabled\n"
        "👨‍👩‍👧 Family/Stranger recognition enabled\n"
        "🌫️ AQI monitoring enabled\n"
        "🌤️ Weather monitoring enabled\n\n"

        "Automatic motion and door "
        "notifications are enabled.\n\n"

        "Send /help for commands."
    )


# ============================================================
# MAIN LOOP
# ============================================================

def main_loop():

    try:

        process_telegram()

    except Exception as exc:

        log(f"Main loop error: {exc}")

    time.sleep(0.05)  # Tiny yield for long polling efficiency


# ============================================================
# RUN APP
# ============================================================

try:

    log("🚀 Starting Arduino App...")

    App.run(user_loop=main_loop)

except Exception as exc:

    log("❌ FATAL APPLICATION ERROR")

    log(repr(exc))

    raise

finally:

    try:

        camera.stop()

        log("Camera stopped.")

    except Exception:

        pass

    try:

        telegram_sender_running = False

        telegram_queue.put(None)

        log("Telegram sender stopped.")

    except Exception:

        pass
