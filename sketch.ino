/*
 * ============================================================
 * Arduino UNO Q - Unified Home Monitoring + AI Security Bridge
 * WITH LED MATRIX DISPLAY
 *
 * FEATURES
 * ------------------------------------------------------------
 * DHT11 temperature + humidity
 * PIR motion detection
 * Door sensor detection
 * Motion LED
 * Door LED
 * LED Matrix display for AQI and Weather
 * MCU -> Python PUSH notifications
 * Python -> MCU LED control
 *
 *
 * PIN ASSIGNMENTS
 * ------------------------------------------------------------
 * DHT11        -> D2
 * Motion PIR   -> D8
 * Motion LED   -> D3
 * Door Sensor  -> D9
 * Door LED     -> D4
 * LED Matrix   -> Q1/Q2 (built-in)
 *
 *
 * PYTHON BRIDGE CALLBACKS
 * ------------------------------------------------------------
 * on_environment
 * on_pir
 * on_door
 * record_sensor_samples (for climate dashboard)
 * get_air_quality
 * get_weather_forecast
 *
 * ============================================================
 */

#include <Arduino.h>
#include <Arduino_RouterBridge.h>
#include <DHT.h>
#include <Arduino_LED_Matrix.h>

// LED Matrix frames (you need to include these header files)
#include "air_quality_frames.h"
#include "weather_frames.h"


// ============================================================
// PIN CONFIGURATION
// ============================================================

#define DHT11_PIN          2

#define MOTION_SENSOR_PIN  8
#define MOTION_LED_PIN     3

#define DOOR_SENSOR_PIN    9
#define DOOR_LED_PIN       4


// ============================================================
// DHT11 CONFIGURATION
// ============================================================

#define DHT11_TYPE DHT11

DHT dht11(
  DHT11_PIN,
  DHT11_TYPE
);


// ============================================================
// LED MATRIX
// ============================================================

Arduino_LED_Matrix matrix;


// ============================================================
// DHT11 CACHED VALUES
// ============================================================

float last_humidity = NAN;
float last_temp_c   = NAN;
float last_temp_f   = NAN;

unsigned long last_dht_read_ms = 0;

const unsigned long DHT_INTERVAL_MS = 3000;


// ============================================================
// MOTION STATE
// ============================================================

int motion_state = LOW;
int motion_state_prev = LOW;

String last_motion_status =
  "No motion detected";

bool motion_detected_since_last_check =
  false;


// ============================================================
// DOOR STATE
// ============================================================
//
// Your hardware uses:
//
// INPUT_PULLUP
//
// HIGH = OPEN
// LOW  = CLOSED
//
// This matches the original sketch you provided.
// ============================================================

int door_state = LOW;
int door_state_prev = LOW;

String last_door_status =
  "Door closed";

bool door_opened_event =
  false;

bool door_closed_event =
  false;


// ============================================================
// LED STATE
// ============================================================
//
// security_led_state is the manual LED state requested by
// Python/WebUI.
//
// If motion is active, the Motion LED is forced ON regardless
// of the manual setting.
// ============================================================

bool security_led_state = false;


// ============================================================
// ENVIRONMENT UPDATE TIMING
// ============================================================

unsigned long previousEnvironmentMillis = 0;
const unsigned long ENVIRONMENT_INTERVAL = 300000;  // 5 minutes

String currentAQI = "";
String currentWeather = "";


// ============================================================
// DHT11 BRIDGE FUNCTIONS
// ============================================================

String get_humidity(String arg)
{
  if (isnan(last_humidity))
  {
    return "nan";
  }

  return String(
    last_humidity,
    1
  );
}

String get_temp_c(String arg)
{
  if (isnan(last_temp_c))
  {
    return "nan";
  }

  return String(
    last_temp_c,
    2
  );
}

String get_temp_f(String arg)
{
  if (isnan(last_temp_f))
  {
    return "nan";
  }

  return String(
    last_temp_f,
    2
  );
}

String get_temperature_status(String arg)
{
  if (
    isnan(last_temp_c) ||
    isnan(last_humidity)
  )
  {
    return "DHT11 data unavailable";
  }

  String result;

  result +=
    "Temperature: " +
    String(last_temp_c, 2) +
    " C / " +
    String(last_temp_f, 2) +
    " F\n";

  result +=
    "Humidity: " +
    String(last_humidity, 1) +
    "%";

  return result;
}


// ============================================================
// SEND DHT DATA TO PYTHON
// ============================================================

void send_dht_data()
{
  unsigned long now =
    millis();


  if (
    now - last_dht_read_ms
    <
    DHT_INTERVAL_MS
  )
  {
    return;
  }


  last_dht_read_ms =
    now;


  float humidity =
    dht11.readHumidity();

  float temperature_c =
    dht11.readTemperature();

  float temperature_f =
    dht11.readTemperature(true);


  // ----------------------------------------------------------
  // READ FAILURE
  // ----------------------------------------------------------

  if (
    isnan(humidity) ||
    isnan(temperature_c) ||
    isnan(temperature_f)
  )
  {
    Monitor.println(
      "DHT11: READ FAILED"
    );

    return;
  }


  // ----------------------------------------------------------
  // SAVE VALUES
  // ----------------------------------------------------------

  last_humidity =
    humidity;

  last_temp_c =
    temperature_c;

  last_temp_f =
    temperature_f;


  // ----------------------------------------------------------
  // SERIAL / MONITOR
  // ----------------------------------------------------------

  Monitor.print(
    "DHT11: "
  );

  Monitor.print(
    temperature_c,
    1
  );

  Monitor.print(
    " C | "
  );

  Monitor.print(
    humidity,
    1
  );

  Monitor.print(
    " % | "
  );

  Monitor.print(
    temperature_f,
    1
  );

  Monitor.println(
    " F"
  );


  // ----------------------------------------------------------
  // PUSH DATA TO PYTHON
  // ----------------------------------------------------------
  //
  // Python callbacks:
  //
  // def on_environment(temperature, humidity)  -> Security Dashboard
  // def record_sensor_samples(temperature, humidity) -> Climate Dashboard
  //
  // ----------------------------------------------------------

  Bridge.notify(
    "on_environment",
    temperature_c,
    humidity
  );

  Bridge.notify(
    "record_sensor_samples",
    temperature_c,
    humidity
  );
}


// ============================================================
// GET AQI + WEATHER FROM PYTHON
// ============================================================

void updateEnvironmentDisplay() {
  Monitor.println();
  Monitor.println("Updating outdoor environment...");

  // Air Quality
  String airQuality;
  bool aqiOK = Bridge.call("get_air_quality").result(airQuality);

  if (aqiOK) {
    currentAQI = airQuality;
    Monitor.println("AQI Level: " + currentAQI);
    showAirQuality(currentAQI);
  } else {
    Monitor.println("Bridge.call get_air_quality failed");
  }

  // Weather
  String weatherForecast;
  bool weatherOK = Bridge.call("get_weather_forecast", "Brooklyn").result(weatherForecast);

  if (weatherOK) {
    currentWeather = weatherForecast;
    Monitor.println("Weather: " + currentWeather);
    showWeather(currentWeather);
  } else {
    Monitor.println("Bridge.call get_weather_forecast failed");
  }

  Monitor.println();
}


// ============================================================
// AQI LED MATRIX
// ============================================================

void showAirQuality(String airQuality) {
  Monitor.println("Displaying AQI: " + airQuality);

  if (airQuality == "Good") {
    matrix.loadFrame(good);
  } else if (airQuality == "Moderate") {
    matrix.loadFrame(moderate);
  } else if (airQuality == "Unhealthy for Sensitive Groups") {
    matrix.loadFrame(unhealthy_for_sensitive_groups);
  } else if (airQuality == "Unhealthy") {
    matrix.loadFrame(unhealthy);
  } else if (airQuality == "Very Unhealthy") {
    matrix.loadFrame(very_unhealthy);
  } else if (airQuality == "Hazardous") {
    matrix.loadFrame(hazardous);
  } else {
    matrix.loadFrame(unknown);
  }

  delay(5000);
}


// ============================================================
// WEATHER LED MATRIX
// ============================================================

void showWeather(String weatherForecast) {
  Monitor.println("Displaying weather: " + weatherForecast);

  if (weatherForecast == "sunny") {
    matrix.loadSequence(sunny);
    playRepeat(5);
  } else if (weatherForecast == "cloudy") {
    matrix.loadSequence(cloudy);
    playRepeat(5);
  } else if (weatherForecast == "rainy") {
    matrix.loadSequence(rainy);
    playRepeat(10);
  } else if (weatherForecast == "snowy") {
    matrix.loadSequence(snowy);
    playRepeat(5);
  } else if (weatherForecast == "foggy") {
    matrix.loadSequence(foggy);
    playRepeat(3);
  } else {
    Monitor.println("Unknown weather category: " + weatherForecast);
  }
}


// ============================================================
// PLAY LED ANIMATION
// ============================================================

void playRepeat(int repeatCount) {
  for (int i = 0; i < repeatCount; i++) {
    matrix.playSequence();
  }
}


// ============================================================
// MOTION SENSOR
// ============================================================

void update_motion()
{
  int reading =
    digitalRead(
      MOTION_SENSOR_PIN
    );


  motion_state =
    reading;


  // ----------------------------------------------------------
  // MOTION ACTIVE
  // ----------------------------------------------------------

  if (
    motion_state == HIGH
  )
  {
    digitalWrite(
      MOTION_LED_PIN,
      HIGH
    );
  }


  // ----------------------------------------------------------
  // NO MOTION
  // ----------------------------------------------------------

  else
  {
    digitalWrite(
      MOTION_LED_PIN,
      security_led_state
        ? HIGH
        : LOW
    );
  }


  // ----------------------------------------------------------
  // DETECT STATE CHANGE
  // ----------------------------------------------------------

  if (
    motion_state
    !=
    motion_state_prev
  )
  {
    motion_state_prev =
      motion_state;


    // --------------------------------------------------------
    // MOTION START
    // --------------------------------------------------------

    if (
      motion_state == HIGH
    )
    {
      motion_detected_since_last_check =
        true;

      last_motion_status =
        "Motion detected!";


      Monitor.println(
        "========================================"
      );

      Monitor.println(
        "🚨 MOTION DETECTED"
      );

      Monitor.println(
        "Motion LED ON"
      );

      Monitor.println(
        "========================================"
      );


      // ------------------------------------------------------
      // PUSH TO PYTHON
      // ------------------------------------------------------

      Bridge.notify(
        "on_pir",
        true
      );
    }


    // --------------------------------------------------------
    // MOTION END
    // --------------------------------------------------------

    else
    {
      last_motion_status =
        "Motion stopped";


      Monitor.println(
        "Motion stopped"
      );

      Monitor.println(
        "Motion LED OFF"
      );


      // ------------------------------------------------------
      // PUSH TO PYTHON
      // ------------------------------------------------------

      Bridge.notify(
        "on_pir",
        false
      );
    }
  }
}


// ============================================================
// LEGACY / COMPATIBILITY MOTION FUNCTION
// ============================================================

String check_motion(String arg)
{
  update_motion();

  return "OK";
}


// ============================================================
// PYTHON RETRIEVES PENDING MOTION EVENT
// ============================================================

String check_motion_detected(String arg)
{
  if (
    motion_detected_since_last_check
  )
  {
    motion_detected_since_last_check =
      false;


    Monitor.println(
      "Motion event delivered to Python"
    );


    return "MOTION_DETECTED";
  }


  return "NO_MOTION";
}


// ============================================================
// GET MOTION STATUS
// ============================================================

String get_motion_status(String arg)
{
  String status;

  status +=
    "Motion Sensor Status:\n";


  status +=
    "Current state: ";


  if (
    digitalRead(
      MOTION_SENSOR_PIN
    ) == HIGH
  )
  {
    status +=
      "ACTIVE\n";
  }
  else
  {
    status +=
      "IDLE\n";
  }


  status +=
    "Motion LED: ";


  if (
    digitalRead(
      MOTION_LED_PIN
    ) == HIGH
  )
  {
    status +=
      "ON\n";
  }
  else
  {
    status +=
      "OFF\n";
  }


  status +=
    "Last event: ";

  status +=
    last_motion_status;


  return status;
}


// ============================================================
// MOTION LED CONTROL
// ============================================================

String set_motion_led(String arg)
{
  if (
    arg == "ON" ||
    arg == "on"
  )
  {
    security_led_state =
      true;


    // Do not turn OFF motion indication.
    digitalWrite(
      MOTION_LED_PIN,
      HIGH
    );


    Monitor.println(
      "Motion LED turned ON"
    );


    return
      "Motion LED turned ON";
  }


  if (
    arg == "OFF" ||
    arg == "off"
  )
  {
    security_led_state =
      false;


    // If there is currently motion,
    // keep the LED ON.
    if (
      digitalRead(
        MOTION_SENSOR_PIN
      ) == LOW
    )
    {
      digitalWrite(
        MOTION_LED_PIN,
        LOW
      );
    }


    Monitor.println(
      "Motion LED turned OFF"
    );


    return
      "Motion LED turned OFF";
  }


  return
    "Invalid command";
}


// ============================================================
// MOTION LED TOGGLE
// ============================================================

String toggle_motion_led(String arg)
{
  security_led_state =
    !security_led_state;


  // Motion has priority.
  if (
    digitalRead(
      MOTION_SENSOR_PIN
    ) == HIGH
  )
  {
    digitalWrite(
      MOTION_LED_PIN,
      HIGH
    );
  }
  else
  {
    digitalWrite(
      MOTION_LED_PIN,
      security_led_state
        ? HIGH
        : LOW
    );
  }


  return
    security_led_state
      ? "Motion LED toggled ON"
      : "Motion LED toggled OFF";
}


// ============================================================
// SECURITY LED CONTROL
// ============================================================
//
// This is used by your Python dashboard:
//
// Bridge.call("set_security_led", enabled)
//
// ============================================================

void set_security_led(bool state)
{
  security_led_state =
    state;


  // Motion indication always has priority.
  if (
    digitalRead(
      MOTION_SENSOR_PIN
    ) == HIGH
  )
  {
    digitalWrite(
      MOTION_LED_PIN,
      HIGH
    );
  }
  else
  {
    digitalWrite(
      MOTION_LED_PIN,
      state
        ? HIGH
        : LOW
    );
  }


  Monitor.print(
    "Security LED: "
  );


  Monitor.println(
    state
      ? "ON"
      : "OFF"
  );
}


// ============================================================
// SECURITY LED COMMAND
// ============================================================
//
// Python uses:
//
// Bridge.call(
//     "set_led_command",
//     "ON"
// )
//
// ============================================================

String set_led_command(String arg)
{
  if (
    arg == "ON" ||
    arg == "on"
  )
  {
    set_security_led(
      true
    );

    return
      "LED ON";
  }


  if (
    arg == "OFF" ||
    arg == "off"
  )
  {
    set_security_led(
      false
    );

    return
      "LED OFF";
  }


  return
    "Invalid LED command";
}


// ============================================================
// GET LED STATE
// ============================================================

String get_led_state(String arg)
{
  if (
    security_led_state
  )
  {
    return "on";
  }


  return "off";
}


// ============================================================
// DOOR SENSOR
// ============================================================
//
// IMPORTANT:
// Your original sketch uses:
//
// HIGH = OPEN
// LOW  = CLOSED
//
// Because DOOR_SENSOR_PIN uses INPUT_PULLUP.
//
// ============================================================

void update_door()
{
  int current_state =
    digitalRead(
      DOOR_SENSOR_PIN
    );


  // ----------------------------------------------------------
  // Detect state change
  // ----------------------------------------------------------

  if (
    current_state
    !=
    door_state_prev
  )
  {
    door_state_prev =
      current_state;

    door_state =
      current_state;


    // --------------------------------------------------------
    // DOOR OPENED
    // --------------------------------------------------------

    if (
      current_state == HIGH
    )
    {
      door_opened_event =
        true;

      door_closed_event =
        false;


      last_door_status =
        "Door opened";


      // Door LED ON
      digitalWrite(
        DOOR_LED_PIN,
        HIGH
      );


      Monitor.println(
        "========================================"
      );

      Monitor.println(
        "🚪 DOOR OPENED"
      );

      Monitor.println(
        "Door LED ON"
      );

      Monitor.println(
        "========================================"
      );


      // ------------------------------------------------------
      // PUSH TO PYTHON
      // ------------------------------------------------------

      Bridge.notify(
        "on_door",
        true
      );
    }


    // --------------------------------------------------------
    // DOOR CLOSED
    // --------------------------------------------------------

    else
    {
      door_closed_event =
        true;

      door_opened_event =
        false;


      last_door_status =
        "Door closed";


      // Door LED OFF
      digitalWrite(
        DOOR_LED_PIN,
        LOW
      );


      Monitor.println(
        "Door closed"
      );

      Monitor.println(
        "Door LED OFF"
      );


      // ------------------------------------------------------
      // PUSH TO PYTHON
      // ------------------------------------------------------

      Bridge.notify(
        "on_door",
        false
      );
    }
  }
}


// ============================================================
// GET DOOR STATE
// ============================================================

String get_door_state(String arg)
{
  int state =
    digitalRead(
      DOOR_SENSOR_PIN
    );


  if (
    state == HIGH
  )
  {
    return "open";
  }


  return "closed";
}


// ============================================================
// GET DOOR LED STATE
// ============================================================

String get_door_led_state(String arg)
{
  if (
    digitalRead(
      DOOR_LED_PIN
    ) == HIGH
  )
  {
    return "on";
  }


  return "off";
}


// ============================================================
// DOOR LED CONTROL
// ============================================================

String set_door_led(String arg)
{
  if (
    arg == "ON" ||
    arg == "on"
  )
  {
    digitalWrite(
      DOOR_LED_PIN,
      HIGH
    );


    Monitor.println(
      "Door LED turned ON"
    );


    return "on";
  }


  if (
    arg == "OFF" ||
    arg == "off"
  )
  {
    digitalWrite(
      DOOR_LED_PIN,
      LOW
    );


    Monitor.println(
      "Door LED turned OFF"
    );


    return "off";
  }


  return "invalid";
}


// ============================================================
// GET DOOR EVENT
// ============================================================

String get_door_event(String arg)
{
  if (
    door_opened_event
  )
  {
    door_opened_event =
      false;


    return "opened";
  }


  if (
    door_closed_event
  )
  {
    door_closed_event =
      false;


    return "closed";
  }


  return "none";
}


// ============================================================
// GET COMPLETE DOOR STATUS
// ============================================================

String get_door_status(String arg)
{
  String status;

  status +=
    "Door Sensor Status:\n";


  status +=
    "Door: ";


  if (
    digitalRead(
      DOOR_SENSOR_PIN
    ) == HIGH
  )
  {
    status +=
      "OPEN\n";
  }
  else
  {
    status +=
      "CLOSED\n";
  }


  status +=
    "Door LED: ";


  if (
    digitalRead(
      DOOR_LED_PIN
    ) == HIGH
  )
  {
    status +=
      "ON\n";
  }
  else
  {
    status +=
      "OFF\n";
  }


  status +=
    "Last event: ";

  status +=
    last_door_status;


  return status;
}


// ============================================================
// SETUP
// ============================================================

void setup()
{
  // ----------------------------------------------------------
  // SERIAL
  // ----------------------------------------------------------

  Serial.begin(
    115200
  );


  // ----------------------------------------------------------
  // MONITOR
  // ----------------------------------------------------------

  Monitor.begin();


  // ----------------------------------------------------------
  // BRIDGE
  // ----------------------------------------------------------

  Bridge.begin();


  // ----------------------------------------------------------
  // LED MATRIX
  // ----------------------------------------------------------

  matrix.begin();
  matrix.clear();


  // ----------------------------------------------------------
  // PIN MODES
  // ----------------------------------------------------------

  pinMode(
    MOTION_SENSOR_PIN,
    INPUT
  );


  pinMode(
    MOTION_LED_PIN,
    OUTPUT
  );


  pinMode(
    DOOR_SENSOR_PIN,
    INPUT_PULLUP
  );


  pinMode(
    DOOR_LED_PIN,
    OUTPUT
  );


  // ----------------------------------------------------------
  // INITIAL LED STATES
  // ----------------------------------------------------------

  digitalWrite(
    MOTION_LED_PIN,
    LOW
  );


  digitalWrite(
    DOOR_LED_PIN,
    LOW
  );


  // ----------------------------------------------------------
  // DHT11
  // ----------------------------------------------------------

  dht11.begin();


  // DHT11 needs time to stabilize.
  delay(2000);


  // ----------------------------------------------------------
  // INITIAL DHT11 READING
  // ----------------------------------------------------------

  float humidity =
    dht11.readHumidity();

  float temperature_c =
    dht11.readTemperature();

  float temperature_f =
    dht11.readTemperature(true);


  if (
    !isnan(humidity) &&
    !isnan(temperature_c) &&
    !isnan(temperature_f)
  )
  {
    last_humidity =
      humidity;

    last_temp_c =
      temperature_c;

    last_temp_f =
      temperature_f;


    Monitor.print(
      "Initial DHT11: "
    );

    Monitor.print(
      temperature_c,
      1
    );

    Monitor.print(
      " C | "
    );

    Monitor.print(
      humidity,
      1
    );

    Monitor.println(
      " %"
    );
  }
  else
  {
    Monitor.println(
      "Initial DHT11 reading failed"
    );
  }


  // ----------------------------------------------------------
  // INITIAL MOTION STATE
  // ----------------------------------------------------------

  motion_state =
    digitalRead(
      MOTION_SENSOR_PIN
    );

  motion_state_prev =
    motion_state;


  // ----------------------------------------------------------
  // INITIAL DOOR STATE
  // ----------------------------------------------------------

  door_state =
    digitalRead(
      DOOR_SENSOR_PIN
    );

  door_state_prev =
    door_state;


  // ----------------------------------------------------------
  // INITIAL DOOR LED
  // ----------------------------------------------------------

  if (
    door_state == HIGH
  )
  {
    last_door_status =
      "Door open";


    digitalWrite(
      DOOR_LED_PIN,
      HIGH
    );
  }
  else
  {
    last_door_status =
      "Door closed";


    digitalWrite(
      DOOR_LED_PIN,
      LOW
    );
  }


  // ==========================================================
  // REGISTER DHT11 BRIDGE FUNCTIONS
  // ==========================================================

  Bridge.provide(
    "get_humidity",
    get_humidity
  );


  Bridge.provide(
    "get_temp_c",
    get_temp_c
  );


  Bridge.provide(
    "get_temp_f",
    get_temp_f
  );


  Bridge.provide(
    "get_temperature_status",
    get_temperature_status
  );


  // ==========================================================
  // REGISTER MOTION FUNCTIONS
  // ==========================================================

  Bridge.provide_safe(
    "check_motion",
    check_motion
  );


  Bridge.provide_safe(
    "check_motion_detected",
    check_motion_detected
  );


  Bridge.provide_safe(
    "get_motion_status",
    get_motion_status
  );


  Bridge.provide_safe(
    "set_motion_led",
    set_motion_led
  );


  Bridge.provide_safe(
    "toggle_motion_led",
    toggle_motion_led
  );


  // ==========================================================
  // REGISTER DOOR FUNCTIONS
  // ==========================================================

  Bridge.provide(
    "get_door_state",
    get_door_state
  );


  Bridge.provide(
    "get_door_led_state",
    get_door_led_state
  );


  Bridge.provide_safe(
    "set_door_led",
    set_door_led
  );


  Bridge.provide(
    "get_door_event",
    get_door_event
  );


  Bridge.provide(
    "get_door_status",
    get_door_status
  );


  // ==========================================================
  // REGISTER UNIFIED SECURITY LED FUNCTIONS
  // ==========================================================

  Bridge.provide_safe(
    "set_security_led",
    set_security_led
  );


  Bridge.provide_safe(
    "set_led_command",
    set_led_command
  );


  Bridge.provide(
    "get_led_state",
    get_led_state
  );


  // ==========================================================
  // STARTUP INFORMATION
  // ==========================================================

  Monitor.println();

  Monitor.println(
    "========================================"
  );

  Monitor.println(
    "  UNO Q UNIFIED HOME MONITORING BRIDGE"
  );

  Monitor.println(
    "========================================"
  );

  Monitor.println(
    "DHT11        : D2"
  );

  Monitor.println(
    "Motion PIR   : D8"
  );

  Monitor.println(
    "Motion LED   : D3"
  );

  Monitor.println(
    "Door Sensor  : D9"
  );

  Monitor.println(
    "Door LED     : D4"
  );

  Monitor.println(
    "LED Matrix   : Q1/Q2 (Built-in)"
  );

  Monitor.println(
    "========================================"
  );

  Monitor.println(
    "DHT11        : ENABLED"
  );

  Monitor.println(
    "PIR Motion   : ENABLED"
  );

  Monitor.println(
    "Door Sensor  : ENABLED"
  );

  Monitor.println(
    "LED Matrix   : ENABLED"
  );

  Monitor.println(
    "MCU -> Python: PUSH"
  );

  Monitor.println(
    "========================================"
  );

  Monitor.println(
    "Bridge ready."
  );

  Monitor.println(
    door_state == HIGH
      ? "Door is OPEN"
      : "Door is CLOSED"
  );

  Monitor.println(
    motion_state == HIGH
      ? "Motion is ACTIVE"
      : "Motion is IDLE"
  );

  Monitor.println(
    "========================================"
  );
}


// ============================================================
// MAIN LOOP
// ============================================================

void loop()
{
  unsigned long currentMillis = millis();

  // ----------------------------------------------------------
  // DHT11
  // ----------------------------------------------------------

  send_dht_data();


  // ----------------------------------------------------------
  // PIR
  // ----------------------------------------------------------

  update_motion();


  // ----------------------------------------------------------
  // DOOR
  // ----------------------------------------------------------

  update_door();


  // ----------------------------------------------------------
  // UPDATE AQI/WEATHER ON LED MATRIX
  // ----------------------------------------------------------

  if (currentMillis - previousEnvironmentMillis >= ENVIRONMENT_INTERVAL) {
    previousEnvironmentMillis = currentMillis;
    updateEnvironmentDisplay();
  }


  // ----------------------------------------------------------
  // SHORT LOOP DELAY
  // ----------------------------------------------------------

  delay(50);
}