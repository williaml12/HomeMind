const ui = new WebUI();

// ============================================================
// STATE
// ============================================================

const state = {
    temperature: null,
    temperature_c: null,
    humidity: null,
    heat_index: null,
    dew_point: null,
    absolute_humidity: null,
    comfort_level: null,
    sensor_ok: false,
    pir_active: false,
    door_open: false,
    system_armed: true,
    security_led: false,
    notifications_enabled: true,
    last_sensor_update: null,
    aqi: null,
    aqi_level: "Unknown",
    weather: "unknown",
    weather_description: "Unknown"
};

let history = [];
let events = [];

// ============================================================
// HELPER
// ============================================================

function $(id) {
    return document.getElementById(id);
}

function formatNumber(value, decimals = 1) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
        return "--";
    }
    return Number(value).toFixed(decimals);
}

function formatTime(ts) {
    if (!ts) return "--";
    return new Date(ts).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit"
    });
}

function capitalize(text) {
    if (!text) return "";
    return text.charAt(0).toUpperCase() + text.slice(1);
}

// Convert Celsius to Fahrenheit
function celsiusToFahrenheit(celsius) {
    if (celsius === null || celsius === undefined || Number.isNaN(Number(celsius))) {
        return null;
    }
    return (Number(celsius) * 9 / 5) + 32;
}

// Get the temperature in Fahrenheit
function getTemperatureF() {
    if (state.temperature !== null && Number.isFinite(Number(state.temperature))) {
        return Number(state.temperature);
    }
    if (state.temperature_c !== null && Number.isFinite(Number(state.temperature_c))) {
        return celsiusToFahrenheit(state.temperature_c);
    }
    return null;
}

// ============================================================
// COMFORT LEVEL CALCULATION
// ============================================================

function calculateComfortLevel(tempF, humidity) {
    if (tempF === null || humidity === null || 
        !Number.isFinite(tempF) || !Number.isFinite(humidity)) {
        return { level: "Unknown", emoji: "❓", description: "Waiting for data..." };
    }

    let comfortScore = 0;
    let level = "";
    let emoji = "";
    let description = "";

    // Temperature comfort (0-50 points)
    if (tempF >= 68 && tempF <= 72) {
        comfortScore += 50;
    } else if (tempF >= 64 && tempF <= 76) {
        comfortScore += 40;
    } else if (tempF >= 60 && tempF <= 80) {
        comfortScore += 25;
    } else if (tempF >= 55 && tempF <= 85) {
        comfortScore += 15;
    } else {
        comfortScore += 5;
    }

    // Humidity comfort (0-50 points)
    if (humidity >= 40 && humidity <= 60) {
        comfortScore += 50;
    } else if (humidity >= 35 && humidity <= 65) {
        comfortScore += 40;
    } else if (humidity >= 30 && humidity <= 70) {
        comfortScore += 25;
    } else if (humidity >= 25 && humidity <= 75) {
        comfortScore += 15;
    } else {
        comfortScore += 5;
    }

    if (comfortScore >= 90) {
        level = "Excellent";
        emoji = "🏆";
        description = "Perfect indoor climate!";
    } else if (comfortScore >= 75) {
        level = "Good";
        emoji = "🌟";
        description = "Comfortable environment";
    } else if (comfortScore >= 60) {
        level = "Fair";
        emoji = "👍";
        description = "Acceptable conditions";
    } else if (comfortScore >= 40) {
        level = "Poor";
        emoji = "🌡️";
        description = "Could be more comfortable";
    } else {
        level = "Bad";
        emoji = "🚨";
        description = "Uncomfortable conditions";
    }

    return { level, emoji, description, score: comfortScore };
}

// ============================================================
// CHART DATA STRUCTURES
// ============================================================

function newChartData(borderColor, backgroundColor) {
    return {
        labels: [],
        datasets: [{
            data: [],
            borderColor: borderColor,
            backgroundColor: backgroundColor,
            fill: true,
        }],
    };
}

// Live charts data
const temperatureLive = { canvas: null, chart: null, data: newChartData('#e67e22', 'rgba(230,126,34,0.08)'), unit: '°F' };
const humidityLive = { canvas: null, chart: null, data: newChartData('#008b8b', 'rgba(0,139,139,0.08)'), unit: '%' };
const dewPointLive = { canvas: null, chart: null, data: newChartData('#3498db', 'rgba(52,152,219,0.08)'), unit: '°F' };
const heatIndexLive = { canvas: null, chart: null, data: newChartData('#e74c3c', 'rgba(231,76,60,0.08)'), unit: '°F' };
const absHumidityLive = { canvas: null, chart: null, data: newChartData('#8e44ad', 'rgba(142,68,173,0.06)'), unit: 'g/m³' };

// 1 Hour charts data
const temperature1h = { canvas: null, chart: null, data: newChartData('#e67e22', 'rgba(230,126,34,0.08)'), unit: '°F' };
const humidity1h = { canvas: null, chart: null, data: newChartData('#008b8b', 'rgba(0,139,139,0.08)'), unit: '%' };
const dewPoint1h = { canvas: null, chart: null, data: newChartData('#3498db', 'rgba(52,152,219,0.08)'), unit: '°F' };
const heatIndex1h = { canvas: null, chart: null, data: newChartData('#e74c3c', 'rgba(231,76,60,0.08)'), unit: '°F' };
const absHumidity1h = { canvas: null, chart: null, data: newChartData('#8e44ad', 'rgba(142,68,173,0.06)'), unit: 'g/m³' };

// 1 Day charts data
const temperature1d = { canvas: null, chart: null, data: newChartData('#e67e22', 'rgba(230,126,34,0.08)'), unit: '°F' };
const humidity1d = { canvas: null, chart: null, data: newChartData('#008b8b', 'rgba(0,139,139,0.08)'), unit: '%' };
const dewPoint1d = { canvas: null, chart: null, data: newChartData('#3498db', 'rgba(52,152,219,0.08)'), unit: '°F' };
const heatIndex1d = { canvas: null, chart: null, data: newChartData('#e74c3c', 'rgba(231,76,60,0.08)'), unit: '°F' };
const absHumidity1d = { canvas: null, chart: null, data: newChartData('#8e44ad', 'rgba(142,68,173,0.06)'), unit: 'g/m³' };

// ============================================================
// NO DATA HANDLING
// ============================================================

let liveCircleTimeout = null;
const noDataTimeout = 10000;

// ============================================================
// WEBUI CONNECT
// ============================================================

ui.on_connect(() => {
    console.log("✅ WebUI connected");

    $("connection").textContent = "● Connected";
    $("connection").className = "connection connected";

    const host = window.location.hostname;
    const cameraURL = "http://" + host + ":4912/embed";

    $("camera-frame").src = cameraURL;
    $("camera-frame").onload = () => {
        $("camera-status").textContent = "● Live";
        $("camera-placeholder").style.display = "none";
    };

    ui.send_message("control", { action: "request_sync" });
});

ui.on_disconnect(() => {
    $("connection").textContent = "● Disconnected";
    $("connection").className = "connection disconnected";
    $("camera-status").textContent = "● Offline";
});

// ============================================================
// RENDER DASHBOARD STATE
// ============================================================

function renderState() {
    const tempF = getTemperatureF();
    
    // Climate metrics
    $("temperature-value").textContent = tempF !== null ? `${formatNumber(tempF)} °F` : "--.- °F";
    $("temperature-time").textContent = state.last_sensor_update ? formatTime(state.last_sensor_update) : "Waiting...";
    
    $("humidity-value").textContent = Number.isFinite(state.humidity) ? `${formatNumber(state.humidity)} %` : "--.- %";
    $("humidity-time").textContent = state.last_sensor_update ? formatTime(state.last_sensor_update) : "Waiting...";
    
    $("heat-index-value").textContent = Number.isFinite(state.heat_index) ? `${formatNumber(state.heat_index)} °F` : "--.- °F";
    $("dew-point-value").textContent = Number.isFinite(state.dew_point) ? `${formatNumber(state.dew_point)} °F` : "--.- °F";
    $("absolute-humidity-value").textContent = Number.isFinite(state.absolute_humidity) ? `${formatNumber(state.absolute_humidity)} g/m³` : "--.- g/m³";
    
    // Comfort Level
    const comfort = calculateComfortLevel(tempF, state.humidity);
    state.comfort_level = comfort;
    $("comfort-value").textContent = `${comfort.emoji} ${comfort.level}`;
    $("comfort-description").textContent = comfort.description;
    updateComfortStyle(comfort.level);
    
    // AQI
    const aqi = Number(state.aqi);
    $("aqi-value").textContent = Number.isFinite(aqi) ? aqi : "--";
    $("aqi-level").textContent = state.aqi_level || "Waiting...";
    updateAQIStyle(state.aqi_level);
    
    // Weather
    $("weather-category").textContent = state.weather ? capitalize(state.weather) : "--";
    $("weather-description").textContent = state.weather_description || "Loading...";
    
    // Security Status
    if (state.pir_active) {
        $("pir").textContent = "MOTION DETECTED";
        $("pir").className = "big-status motion-active";
    } else {
        $("pir").textContent = "NO MOTION";
        $("pir").className = "big-status";
    }
    
    $("door").textContent = state.door_open ? "OPEN" : "CLOSED";
    $("security").textContent = state.system_armed ? "ARMED" : "DISARMED";
    $("security-description").textContent = state.system_armed ? "AI monitoring active" : "AI monitoring paused";
    $("security-led-status").textContent = state.security_led ? "ON" : "OFF";
    
    // Controls
    $("armed-toggle").checked = !!state.system_armed;
    $("led-toggle").checked = !!state.security_led;
    $("notifications-toggle").checked = !!state.notifications_enabled;
    
    if (state.last_sensor_update) {
        $("last-update").textContent = "Last sensor update: " + 
            new Date(state.last_sensor_update).toLocaleString();
    }
}

// ============================================================
// COMFORT STYLE
// ============================================================

function updateComfortStyle(level) {
    const card = $("comfort-card");
    if (!card) return;
    card.classList.remove("comfort-excellent", "comfort-good", "comfort-fair", "comfort-poor", "comfort-bad");
    
    switch (level) {
        case "Excellent": card.classList.add("comfort-excellent"); break;
        case "Good": card.classList.add("comfort-good"); break;
        case "Fair": card.classList.add("comfort-fair"); break;
        case "Poor": card.classList.add("comfort-poor"); break;
        case "Bad": card.classList.add("comfort-bad"); break;
    }
}

// ============================================================
// AQI STYLE
// ============================================================

function updateAQIStyle(level) {
    const card = $("aqi-card");
    if (!card) return;
    card.classList.remove("aqi-good", "aqi-moderate", "aqi-sensitive", "aqi-unhealthy", "aqi-very-unhealthy", "aqi-hazardous");
    
    switch (level) {
        case "Good": card.classList.add("aqi-good"); break;
        case "Moderate": card.classList.add("aqi-moderate"); break;
        case "Unhealthy for Sensitive Groups": card.classList.add("aqi-sensitive"); break;
        case "Unhealthy": card.classList.add("aqi-unhealthy"); break;
        case "Very Unhealthy": card.classList.add("aqi-very-unhealthy"); break;
        case "Hazardous": card.classList.add("aqi-hazardous"); break;
    }
}

// ============================================================
// WEATHER ICON
// ============================================================

function weatherIcon(category) {
    switch (category.toLowerCase()) {
        case "sunny": return "☀️";
        case "cloudy": return "☁️";
        case "rainy": return "🌧️";
        case "snowy": return "❄️";
        case "foggy": return "🌫️";
        default: return "🌤️";
    }
}

// ============================================================
// RENDER CHART DATA
// ============================================================

function renderChartData(obj, messages, maxPoints = 20, showMinutes = true, showSeconds = true) {
    if (!messages || messages.length === 0) {
        return;
    }

    const noDataDiv = document.getElementById((obj.canvas && obj.canvas.id) + '-nodata');
    const liveCircle = document.getElementById('live-circle');
    const isLiveChart = obj.canvas && obj.canvas.id && obj.canvas.id.endsWith('-live-chart');

    if (!isLiveChart) {
        obj.data.labels = [];
        obj.data.datasets[0].data = [];
    }

    for (const message of messages) {
        if (!message.ts) {
            console.warn('Invalid message format:', message);
            continue;
        }

        let date = new Date(message.ts);
        if (showMinutes && showSeconds) {
            date = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        } else if (showMinutes) {
            date = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        } else {
            date = date.toLocaleTimeString([], { hour: '2-digit' });
        }

        obj.data.labels.push(date);
        obj.data.datasets[0].data.push(message.value);

        if (obj.data.labels.length > maxPoints) {
            obj.data.labels.shift();
            obj.data.datasets[0].data.shift();
        }

        if (obj.data.labels.length === 0 || obj.data.datasets[0].data.length === 0) {
            if (obj.canvas) obj.canvas.style.display = 'none';
            if (noDataDiv) noDataDiv.style.display = 'flex';
            if (isLiveChart && liveCircle) {
                liveCircle.style.display = 'none';
                liveCircle.classList.remove('flash');
                if (liveCircleTimeout) {
                    clearTimeout(liveCircleTimeout);
                    liveCircleTimeout = null;
                }
            }
            if (obj.chart) {
                obj.chart.destroy();
                obj.chart = null;
            }
        } else {
            if (obj.canvas) obj.canvas.style.display = 'block';
            if (noDataDiv) noDataDiv.style.display = 'none';
            if (isLiveChart && liveCircle) {
                liveCircle.style.display = 'flex';
                liveCircle.classList.add('flash');
                if (liveCircleTimeout) clearTimeout(liveCircleTimeout);
                liveCircleTimeout = setTimeout(() => {
                    liveCircle.classList.remove('flash');
                    liveCircle.style.display = 'none';
                }, noDataTimeout);
            }
            if (!obj.chart) {
                obj.chart = newChart(obj.canvas.getContext('2d'), obj);
            } else {
                obj.chart.update();
            }
        }
    }
}

// ============================================================
// CHART CREATION
// ============================================================

function newChart(ctx, obj) {
    return new Chart(ctx, {
        type: 'line',
        data: obj.data,
        options: {
            responsive: true,
            animation: false,
            scales: {
                y: obj.unit === '%' ? { min: 0, max: 100 } : {},
                x: {
                    grid: { display: false },
                    ticks: {
                        maxRotation: 45,
                        minRotation: 45,
                    },
                },
            },
            interaction: {
                mode: 'index',
                intersect: false,
            },
            plugins: {
                legend: { display: false },
                tooltip: {
                    displayColors: false,
                    callbacks: {
                        title: function() { return ''; },
                        label: function(context) {
                            const unit = obj.unit || '';
                            return `${context.label} - ${context.parsed.y.toFixed(1)} ${unit}`;
                        },
                    },
                },
                noDataMessage: true,
            },
        },
    });
}

// ============================================================
// INITIALIZE CANVASES
// ============================================================

// Live charts
temperatureLive.canvas = document.getElementById('temperature-live-chart');
humidityLive.canvas = document.getElementById('humidity-live-chart');
dewPointLive.canvas = document.getElementById('dew-live-chart');
heatIndexLive.canvas = document.getElementById('heat-live-chart');
absHumidityLive.canvas = document.getElementById('absolute-live-chart');

// 1 Hour charts
temperature1h.canvas = document.getElementById('temperature-hour-chart');
humidity1h.canvas = document.getElementById('humidity-hour-chart');
dewPoint1h.canvas = document.getElementById('dew-hour-chart');
heatIndex1h.canvas = document.getElementById('heat-hour-chart');
absHumidity1h.canvas = document.getElementById('absolute-hour-chart');

// 1 Day charts
temperature1d.canvas = document.getElementById('temperature-day-chart');
humidity1d.canvas = document.getElementById('humidity-day-chart');
dewPoint1d.canvas = document.getElementById('dew-day-chart');
heatIndex1d.canvas = document.getElementById('heat-day-chart');
absHumidity1d.canvas = document.getElementById('absolute-day-chart');

const liveCircle = document.getElementById('live-circle');
if (liveCircle) liveCircle.style.display = 'none';

// ============================================================
// MESSAGE HANDLERS
// ============================================================

// Climate metrics
ui.on_message('temperature', message => {
    console.log("📊 Temperature message:", message);
    state.temperature = Number(message.value);
    renderState();
    renderChartData(temperatureLive, [message]);
});

ui.on_message('humidity', message => {
    console.log("📊 Humidity message:", message);
    state.humidity = Number(message.value);
    renderState();
    renderChartData(humidityLive, [message]);
});

ui.on_message('dew_point', message => {
    console.log("📊 Dew Point message:", message);
    state.dew_point = Number(message.value);
    renderState();
    renderChartData(dewPointLive, [message]);
});

ui.on_message('heat_index', message => {
    console.log("📊 Heat Index message:", message);
    state.heat_index = Number(message.value);
    renderState();
    renderChartData(heatIndexLive, [message]);
});

ui.on_message('absolute_humidity', message => {
    console.log("📊 Absolute Humidity message:", message);
    state.absolute_humidity = Number(message.value);
    renderState();
    renderChartData(absHumidityLive, [message]);
});

// AQI and Weather
ui.on_message("air_quality", message => {
    console.log("🌫️ AQI message:", message);
    state.aqi = Number(message.aqi);
    state.aqi_level = message.level || "Unknown";
    renderState();
});

ui.on_message("weather", message => {
    console.log("🌤️ Weather message:", message);
    state.weather = message.category || "unknown";
    state.weather_description = message.description || message.category || "Unknown";
    renderState();
});

// Sensor update
ui.on_message("sensor_update", data => {
    console.log("📊 Sensor update:", data);
    state.temperature_c = Number(data.temperature);
    state.humidity = Number(data.humidity);
    state.heat_index = Number(data.heat_index);
    state.sensor_ok = true;
    state.last_sensor_update = data.timestamp;

    if (state.temperature === null) {
        state.temperature = celsiusToFahrenheit(data.temperature);
    }

    history.push({
        timestamp: data.timestamp,
        temperature: Number(data.temperature),
        humidity: Number(data.humidity)
    });
    history = history.slice(-1000);

    renderState();
});

// ============================================================
// SECURITY MESSAGE HANDLERS
// ============================================================

ui.on_message("pir_state", data => {
    console.log("🚨 PIR State:", data);
    state.pir_active = !!data.active;
    renderState();
});

ui.on_message("door_state", data => {
    console.log("🚪 Door State:", data);
    state.door_open = !!data.open;
    renderState();
});

ui.on_message("led_state", data => {
    console.log("💡 LED State:", data);
    state.security_led = !!data.state;
    renderState();
});

// ============================================================
// SECURITY EVENT HANDLER
// ============================================================

ui.on_message("security_event", event => {
    console.log("🚨 SECURITY EVENT RECEIVED:", event);
    
    if (!event.type) {
        console.warn("Security event missing type:", event);
        return;
    }
    
    events.push(event);
    
    while (events.length > 50) {
        events.shift();
    }
    
    renderEvents();
    console.log(`📋 Total events: ${events.length}`);
});

// ============================================================
// FULL SYNC
// ============================================================

ui.on_message("sync", data => {
    console.log("🔄 SYNC received:", data);
    
    if (data.state) {
        Object.assign(state, data.state);
    }
    
    if (Array.isArray(data.history)) {
        history = data.history.map(row => ({
            timestamp: row.timestamp,
            temperature: Number(row.temperature ?? row.temperature_c),
            humidity: Number(row.humidity ?? row.humidity_pct)
        }));
    }
    
    if (Array.isArray(data.events)) {
        events = data.events;
        console.log(`📋 Loaded ${events.length} events from sync`);
    }
    
    renderState();
    renderEvents();
    console.log("✅ Sync complete");
});

// ============================================================
// RENDER SECURITY EVENTS WITH SEVERITY COLORS
// ============================================================
// Severity Color Mapping:
//   Critical (🔴 Red #ef4444): Stranger Detected
//   Warning  (🟠 Orange #f59e0b): Motion Detected, Door Open
//   Info     (🔵 Blue #3b82f6): Family Member, Door Closed
// ============================================================

// Severity mapping for event types (in case Python doesn't set severity)
const EVENT_SEVERITY_MAP = {
    "stranger detected": "critical",
    "motion detected": "warning",
    "door open": "warning",
    "family member": "info",
    "door closed": "info",
    "uncertain": "warning"
};

// Get the severity for an event (prefer Python's severity, fallback to map)
function getEventSeverity(event) {
    // If event has severity, use it
    if (event.severity) {
        const sev = event.severity.toLowerCase();
        if (["critical", "warning", "info"].includes(sev)) {
            return sev;
        }
    }
    // Otherwise, look up by type
    const type = (event.type || "").toLowerCase().trim();
    return EVENT_SEVERITY_MAP[type] || "info";
}

// Get icon for event type
function getEventIcon(eventType) {
    const type = (eventType || "").toLowerCase();
    if (type.includes("motion")) return "🚨";
    if (type.includes("door open")) return "🚪";
    if (type.includes("door closed")) return "🔒";
    if (type.includes("family")) return "👨‍👩‍👧";
    if (type.includes("stranger")) return "👤";
    if (type.includes("uncertain")) return "❓";
    return "🔔";
}

function renderEvents() {
    const container = $("events");
    if (!container) {
        console.error("Events container not found!");
        return;
    }
    
    container.innerHTML = "";

    if (!events || events.length === 0) {
        container.innerHTML = `
            <div class="empty">
                <span style="font-size: 24px;">🔒</span><br>
                No security events yet.
            </div>
        `;
        return;
    }

    console.log(`📋 Rendering ${events.length} events`);

    // Sort newest first
    const sortedEvents = [...events].reverse();
    
    sortedEvents.forEach(event => {
        const severity = getEventSeverity(event);
        
        const item = document.createElement("div");
        item.className = `event event-${severity}`;

        // Event image
        if (event.image) {
            const img = document.createElement("img");
            img.className = "event-image";
            img.src = event.image;
            img.alt = "Security event image";
            item.appendChild(img);
        }

        // Content
        const content = document.createElement("div");

        // Title
        const title = document.createElement("div");
        title.className = "event-title";
        title.textContent = `${getEventIcon(event.type)} ${event.type || "Security Event"}`;
        content.appendChild(title);

        // Meta with severity badge
        const meta = document.createElement("div");
        meta.className = "event-meta";
        meta.textContent = (event.display_time || event.timestamp || "Unknown time") + " · " + severity;
        content.appendChild(meta);

        // Message
        const message = document.createElement("div");
        message.className = "event-message";
        message.textContent = event.message || "No details available";
        content.appendChild(message);

        // Confidence
        if (event.confidence !== null && event.confidence !== undefined) {
            const confidence = document.createElement("div");
            confidence.className = "event-confidence";
            confidence.textContent = "Confidence: " + (Number(event.confidence) * 100).toFixed(2) + "%";
            content.appendChild(confidence);
        }

        item.appendChild(content);
        container.appendChild(item);
    });
}

// ============================================================
// HISTORICAL DATA
// ============================================================

async function loadSamples(resource, start, window) {
    try {
        const response = await fetch(`/get_samples/${resource}/${start}/${window}`);
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        const data = await response.json();
        if (!Array.isArray(data)) {
            console.error(`Invalid data for ${resource}:`, data);
            return [];
        }
        return data;
    } catch (error) {
        console.error(`Failed to load ${resource}:`, error);
        return [];
    }
}

// ============================================================
// TAB SWITCHING
// ============================================================

document.querySelector('.tab[data-tab="hour"]').addEventListener('click', async () => {
    const temp = await loadSamples('temperature', '-1h', '5m');
    renderChartData(temperature1h, temp, 12, true, false);
    const hum = await loadSamples('humidity', '-1h', '5m');
    renderChartData(humidity1h, hum, 12, true, false);
    const dew = await loadSamples('dew_point', '-1h', '5m');
    renderChartData(dewPoint1h, dew, 12, true, false);
    const heat = await loadSamples('heat_index', '-1h', '5m');
    renderChartData(heatIndex1h, heat, 12, true, false);
    const abs = await loadSamples('absolute_humidity', '-1h', '5m');
    renderChartData(absHumidity1h, abs, 12, true, false);
});

document.querySelector('.tab[data-tab="day"]').addEventListener('click', async () => {
    const temp = await loadSamples('temperature', '-1d', '1h');
    renderChartData(temperature1d, temp, 24, false, false);
    const hum = await loadSamples('humidity', '-1d', '1h');
    renderChartData(humidity1d, hum, 24, false, false);
    const dew = await loadSamples('dew_point', '-1d', '1h');
    renderChartData(dewPoint1d, dew, 24, false, false);
    const heat = await loadSamples('heat_index', '-1d', '1h');
    renderChartData(heatIndex1d, heat, 24, false, false);
    const abs = await loadSamples('absolute_humidity', '-1d', '1h');
    renderChartData(absHumidity1d, abs, 24, false, false);
});

document.querySelectorAll(".tab").forEach(tab => {
    tab.addEventListener("click", async function() {
        document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
        document.querySelectorAll(".tab-content").forEach(content => content.classList.remove("active"));
        this.classList.add("active");
        const target = document.getElementById(this.dataset.tab);
        if (target) {
            target.classList.add("active");
        }
    });
});

// ============================================================
// CONTROLS
// ============================================================

$("armed-toggle").addEventListener("change", event => {
    ui.send_message("control", {
        action: "set_armed",
        enabled: event.target.checked
    });
});

$("led-toggle").addEventListener("change", event => {
    ui.send_message("control", {
        action: "set_led",
        enabled: event.target.checked
    });
});

$("notifications-toggle").addEventListener("change", event => {
    ui.send_message("control", {
        action: "set_notifications",
        enabled: event.target.checked
    });
});

// ============================================================
// POPOVER LOGIC
// ============================================================

const tempPopoverText = 'Shows temperature readings in °F. Data is average per 5 minutes (1H view) or per 1 hour (1D view)';
const humidityPopoverText = 'Shows relative humidity percentage. Data is average per 5 minutes (1H view) or per 1 hour (1D view)';
const dewPointPopoverText = 'Dew Point is the temperature at which air becomes saturated with moisture. It indicates the absolute humidity level.';
const heatIndexPopoverText = 'Heat Index combines air temperature and relative humidity to determine the perceived temperature.';
const absHumidityPopoverText = 'Absolute Humidity is the total amount of water vapor present in the air, expressed in grams per cubic meter (g/m³).';

document.querySelectorAll('.info-btn.temp').forEach(img => {
    img.style.position = 'relative';
    const popover = img.nextElementSibling;
    img.addEventListener('mouseenter', () => {
        popover.textContent = tempPopoverText;
        popover.style.display = 'block';
    });
    img.addEventListener('mouseleave', () => {
        popover.style.display = 'none';
    });
});

document.querySelectorAll('.info-btn.humidity').forEach(img => {
    img.style.position = 'relative';
    const popover = img.nextElementSibling;
    img.addEventListener('mouseenter', () => {
        popover.textContent = humidityPopoverText;
        popover.style.display = 'block';
    });
    img.addEventListener('mouseleave', () => {
        popover.style.display = 'none';
    });
});

document.querySelectorAll('.info-btn.dew').forEach(img => {
    img.style.position = 'relative';
    const popover = img.nextElementSibling;
    img.addEventListener('mouseenter', () => {
        popover.textContent = dewPointPopoverText;
        popover.style.display = 'block';
    });
    img.addEventListener('mouseleave', () => {
        popover.style.display = 'none';
    });
});

document.querySelectorAll('.info-btn.heat').forEach(img => {
    img.style.position = 'relative';
    const popover = img.nextElementSibling;
    img.addEventListener('mouseenter', () => {
        popover.textContent = heatIndexPopoverText;
        popover.style.display = 'block';
    });
    img.addEventListener('mouseleave', () => {
        popover.style.display = 'none';
    });
});

document.querySelectorAll('.info-btn.abs').forEach(img => {
    img.style.position = 'relative';
    const popover = img.nextElementSibling;
    img.addEventListener('mouseenter', () => {
        popover.textContent = absHumidityPopoverText;
        popover.style.display = 'block';
    });
    img.addEventListener('mouseleave', () => {
        popover.style.display = 'none';
    });
});

// ============================================================
// INITIAL LOAD
// ============================================================

setTimeout(async () => {
    console.log("🔄 Loading initial historical data...");
    
    const temp1h = await loadSamples('temperature', '-1h', '5m');
    renderChartData(temperature1h, temp1h, 12, true, false);
    const hum1h = await loadSamples('humidity', '-1h', '5m');
    renderChartData(humidity1h, hum1h, 12, true, false);
    const dew1h = await loadSamples('dew_point', '-1h', '5m');
    renderChartData(dewPoint1h, dew1h, 12, true, false);
    const heat1h = await loadSamples('heat_index', '-1h', '5m');
    renderChartData(heatIndex1h, heat1h, 12, true, false);
    const abs1h = await loadSamples('absolute_humidity', '-1h', '5m');
    renderChartData(absHumidity1h, abs1h, 12, true, false);

    const temp1d = await loadSamples('temperature', '-1d', '1h');
    renderChartData(temperature1d, temp1d, 24, false, false);
    const hum1d = await loadSamples('humidity', '-1d', '1h');
    renderChartData(humidity1d, hum1d, 24, false, false);
    const dew1d = await loadSamples('dew_point', '-1d', '1h');
    renderChartData(dewPoint1d, dew1d, 24, false, false);
    const heat1d = await loadSamples('heat_index', '-1d', '1h');
    renderChartData(heatIndex1d, heat1d, 24, false, false);
    const abs1d = await loadSamples('absolute_humidity', '-1d', '1h');
    renderChartData(absHumidity1d, abs1d, 24, false, false);
}, 1000);

// ============================================================
// PERIODIC HISTORICAL REFRESH
// ============================================================

setInterval(async () => {
    const activeTab = document.querySelector(".tab.active");
    if (!activeTab) return;

    if (activeTab.dataset.tab === "hour") {
        const temp = await loadSamples('temperature', '-1h', '5m');
        renderChartData(temperature1h, temp, 12, true, false);
        const hum = await loadSamples('humidity', '-1h', '5m');
        renderChartData(humidity1h, hum, 12, true, false);
        const dew = await loadSamples('dew_point', '-1h', '5m');
        renderChartData(dewPoint1h, dew, 12, true, false);
        const heat = await loadSamples('heat_index', '-1h', '5m');
        renderChartData(heatIndex1h, heat, 12, true, false);
        const abs = await loadSamples('absolute_humidity', '-1h', '5m');
        renderChartData(absHumidity1h, abs, 12, true, false);
    }

    if (activeTab.dataset.tab === "day") {
        const temp = await loadSamples('temperature', '-1d', '1h');
        renderChartData(temperature1d, temp, 24, false, false);
        const hum = await loadSamples('humidity', '-1d', '1h');
        renderChartData(humidity1d, hum, 24, false, false);
        const dew = await loadSamples('dew_point', '-1d', '1h');
        renderChartData(dewPoint1d, dew, 24, false, false);
        const heat = await loadSamples('heat_index', '-1d', '1h');
        renderChartData(heatIndex1d, heat, 24, false, false);
        const abs = await loadSamples('absolute_humidity', '-1d', '1h');
        renderChartData(absHumidity1d, abs, 24, false, false);
    }
}, 60000);

// ============================================================
// INITIAL
// ============================================================

renderState();
renderEvents();
console.log("✅ Dashboard initialized");
console.log(`📋 Events loaded: ${events.length}`);