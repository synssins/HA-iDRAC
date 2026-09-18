#!/bin/bash
echo "[RUN.SH] >>> Add-on execution started at $(date)"

# Default values
CHECK_INTERVAL_SECONDS_DEFAULT=30
LOG_LEVEL_DEFAULT="info"
TEMPERATURE_UNIT_DEFAULT="C"
BASE_FAN_SPEED_PERCENT_DEFAULT=20
LOW_TEMP_THRESHOLD_DEFAULT=45
HIGH_TEMP_FAN_SPEED_PERCENT_DEFAULT=50
CRITICAL_TEMP_THRESHOLD_DEFAULT=65
MQTT_HOST_DEFAULT="core-mosquitto"
MQTT_PORT_DEFAULT=1883
MQTT_USERNAME_DEFAULT=""
MQTT_PASSWORD_DEFAULT=""
POWER_CONTROL_PIN_DEFAULT=""
POWER_UNLOCK_TIMEOUT_DEFAULT=60

if [ -f /data/options.json ]; then
    echo "[RUN.SH] Reading configuration from /data/options.json"
    export MASTER_ENCRYPTION_KEY=$(jq -r '.master_encryption_key // empty' /data/options.json)
    export CHECK_INTERVAL_SECONDS=$(jq -r '.check_interval_seconds // "'"$CHECK_INTERVAL_SECONDS_DEFAULT"'"' /data/options.json)
    export LOG_LEVEL=$(jq -r '.log_level // "'"$LOG_LEVEL_DEFAULT"'"' /data/options.json)
    export TEMPERATURE_UNIT=$(jq -r '.temperature_unit // "'"$TEMPERATURE_UNIT_DEFAULT"'"' /data/options.json)
    export BASE_FAN_SPEED_PERCENT=$(jq -r '.base_fan_speed_percent // "'"$BASE_FAN_SPEED_PERCENT_DEFAULT"'"' /data/options.json)
    export LOW_TEMP_THRESHOLD=$(jq -r '.low_temp_threshold // "'"$LOW_TEMP_THRESHOLD_DEFAULT"'"' /data/options.json)
    export HIGH_TEMP_FAN_SPEED_PERCENT=$(jq -r '.high_temp_fan_speed_percent // "'"$HIGH_TEMP_FAN_SPEED_PERCENT_DEFAULT"'"' /data/options.json)
    export CRITICAL_TEMP_THRESHOLD=$(jq -r '.critical_temp_threshold // "'"$CRITICAL_TEMP_THRESHOLD_DEFAULT"'"' /data/options.json)
    export MQTT_HOST=$(jq -r '.mqtt_host // "'"$MQTT_HOST_DEFAULT"'"' /data/options.json)
    export MQTT_PORT=$(jq -r '.mqtt_port // '$MQTT_PORT_DEFAULT /data/options.json)
    export MQTT_USERNAME=$(jq -r '.mqtt_username // empty' /data/options.json)
    export MQTT_PASSWORD=$(jq -r '.mqtt_password // empty' /data/options.json)
    export POWER_CONTROL_PIN=$(jq -r '.power_control_pin // empty' /data/options.json)
    export POWER_UNLOCK_TIMEOUT=$(jq -r '.power_unlock_timeout // "'"$POWER_UNLOCK_TIMEOUT_DEFAULT"'"' /data/options.json)
else
    echo "[RUN.SH] WARNING: /data/options.json not found. Using defaults."
    export CHECK_INTERVAL_SECONDS="$CHECK_INTERVAL_SECONDS_DEFAULT"
    export LOG_LEVEL="$LOG_LEVEL_DEFAULT"
    export TEMPERATURE_UNIT="$TEMPERATURE_UNIT_DEFAULT"
    export BASE_FAN_SPEED_PERCENT="$BASE_FAN_SPEED_PERCENT_DEFAULT"
    export LOW_TEMP_THRESHOLD="$LOW_TEMP_THRESHOLD_DEFAULT"
    export HIGH_TEMP_FAN_SPEED_PERCENT="$HIGH_TEMP_FAN_SPEED_PERCENT_DEFAULT"
    export CRITICAL_TEMP_THRESHOLD="$CRITICAL_TEMP_THRESHOLD_DEFAULT"
    export MQTT_HOST="$MQTT_HOST_DEFAULT"
    export MQTT_PORT="$MQTT_PORT_DEFAULT"
    export MQTT_USERNAME="$MQTT_USERNAME_DEFAULT"
    export MQTT_PASSWORD="$MQTT_PASSWORD_DEFAULT"
    export POWER_CONTROL_PIN="$POWER_CONTROL_PIN_DEFAULT"
    export POWER_UNLOCK_TIMEOUT="$POWER_UNLOCK_TIMEOUT_DEFAULT"
fi

echo "[RUN.SH] Configuration:"
echo "[RUN.SH]   LOG_LEVEL: ${LOG_LEVEL}"
echo "[RUN.SH]   CHECK_INTERVAL: ${CHECK_INTERVAL_SECONDS}s"
echo "[RUN.SH]   TEMP_UNIT: ${TEMPERATURE_UNIT}"
echo "[RUN.SH]   MQTT: ${MQTT_HOST}:${MQTT_PORT}"
echo "[RUN.SH]   POWER_PIN: $([ -n "$POWER_CONTROL_PIN" ] && echo 'SET' || echo 'NOT SET')"
echo "[RUN.SH]   POWER_UNLOCK_TIMEOUT: ${POWER_UNLOCK_TIMEOUT}s"

echo "[RUN.SH] Starting Python application..."
cd /
exec python3 -m app.main

echo "[RUN.SH] CRITICAL: python3 -m app.main failed to start" >&2
exit 1
