import paho.mqtt.client as mqtt
import json
import re
import time
import threading


class MqttClient:
    def __init__(self, client_id="ha_idrac_controller"):
        self.client_id = client_id
        self.client = mqtt.Client(client_id=self.client_id, protocol=mqtt.MQTTv311)
        self.broker_address = "core-mosquitto"
        self.port = 1883
        self.is_connected = False
        self.log_level = "info"

        self.base_topic = "ha_idrac_controller"
        self.availability_topic = f"{self.base_topic}/status"
        self.device_info_dict = None

        self.power_pin = ""
        self.power_unlock_timeout = 60
        self._power_unlocked = False
        self._power_unlock_time = 0
        self._lock_timer = None

        self.power_callback = None
        self.message_callback = None
        self.discovered_entities = set()

        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message_handler

    def _log(self, level, msg):
        levels = {"trace": -1, "debug": 0, "info": 1, "warning": 2, "error": 3}
        if levels.get(self.log_level, 1) <= levels.get(level, 1):
            print(f"[{level.upper()}] MQTT ({self.client_id}): {msg}", flush=True)

    def configure_broker(self, host, port, username, password, log_level="info"):
        self.broker_address = host
        self.port = int(port)
        self.log_level = log_level.lower()
        if username:
            self.client.username_pw_set(username, password)

    def set_device_info(self, server_alias, manufacturer, model, ip_address):
        safe = re.sub(r'[^a-zA-Z0-9_-]+', '_', server_alias)
        self.base_topic = f"ha_idrac_controller/{safe}"
        self.availability_topic = f"{self.base_topic}/status"
        self.device_info_dict = {
            "identifiers": [f"idrac_controller_{safe}"],
            "name": f"iDRAC ({server_alias})",
            "model": model or "PowerEdge Server",
            "manufacturer": manufacturer or "Dell Inc.",
            "configuration_url": f"https://{ip_address}" if ip_address else None,
        }

    def configure_power_pin(self, pin, timeout=60):
        self.power_pin = str(pin)
        self.power_unlock_timeout = timeout

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self._log("info", f"Connected to {self.broker_address}:{self.port}")
            self.is_connected = True
            self._subscribe_commands()
        else:
            self._log("error", f"Connection failed: rc={rc}")
            self.is_connected = False

    def _on_disconnect(self, client, userdata, rc):
        self._log("info", f"Disconnected: rc={rc}")
        self.is_connected = False

    def _subscribe_commands(self):
        topics = [
            f"{self.base_topic}/lock/power_control/set",
            f"{self.base_topic}/command/power/+",
        ]
        for t in topics:
            self.client.subscribe(t)
            self._log("debug", f"Subscribed: {t}")

    def _on_message_handler(self, client, userdata, msg):
        topic = msg.topic
        payload = msg.payload.decode("utf-8")

        if topic.endswith("/lock/power_control/set"):
            self._handle_lock_command(payload)
            return

        if "/command/power/" in topic:
            action = topic.rsplit("/", 1)[-1]
            self._handle_power_command(action, payload)
            return

        if self.message_callback:
            self.message_callback(topic, payload)

    def _handle_lock_command(self, payload):
        try:
            data = json.loads(payload)
            action = data.get("action", "")
            code = str(data.get("code", ""))
        except (json.JSONDecodeError, TypeError):
            action = payload.strip()
            code = ""

        if action == "LOCK":
            self._lock_power()
            return

        if action == "UNLOCK":
            if not self.power_pin:
                self._unlock_power()
                return
            if code == self.power_pin:
                self._unlock_power()
            else:
                self._log("warning", "Power unlock: invalid PIN")
                self._publish_lock_state()

    def _unlock_power(self):
        self._power_unlocked = True
        self._power_unlock_time = time.time()
        self._publish_lock_state()
        self._log("info", f"Power controls UNLOCKED for {self.power_unlock_timeout}s")

        if self._lock_timer:
            self._lock_timer.cancel()
        self._lock_timer = threading.Timer(self.power_unlock_timeout, self._auto_lock)
        self._lock_timer.daemon = True
        self._lock_timer.start()

    def _lock_power(self):
        self._power_unlocked = False
        if self._lock_timer:
            self._lock_timer.cancel()
        self._publish_lock_state()
        self._log("info", "Power controls LOCKED")

    def _auto_lock(self):
        self._power_unlocked = False
        self._publish_lock_state()
        self._log("info", "Power controls auto-locked (timeout)")

    def _publish_lock_state(self):
        state = "UNLOCKED" if self._power_unlocked else "LOCKED"
        self.publish(f"{self.base_topic}/lock/power_control/state", state, retain=True)

    def is_power_unlocked(self):
        if not self._power_unlocked:
            return False
        if time.time() - self._power_unlock_time > self.power_unlock_timeout:
            self._lock_power()
            return False
        return True

    def _handle_power_command(self, action, payload):
        if payload != "PRESS":
            return

        if not self.is_power_unlocked():
            self._log("warning", f"Power command '{action}' REJECTED: controls locked")
            return

        action_map = {
            "on": "On",
            "force_off": "ForceOff",
            "graceful_shutdown": "GracefulShutdown",
            "graceful_restart": "GracefulRestart",
            "force_restart": "ForceRestart",
            "power_cycle": "PowerCycle",
        }
        redfish_action = action_map.get(action)
        if not redfish_action:
            self._log("error", f"Unknown power command: {action}")
            return

        self._log("info", f"Power command '{redfish_action}' ACCEPTED (controls unlocked)")
        if self.power_callback:
            self.power_callback(redfish_action)

        self._lock_power()

    def connect(self):
        if self.is_connected:
            return
        self._log("info", f"Connecting to {self.broker_address}:{self.port}...")
        try:
            self.client.will_set(self.availability_topic, payload="offline", qos=1, retain=True)
            self.client.connect(self.broker_address, self.port, 60)
            self.client.loop_start()
        except Exception as e:
            self._log("error", f"Connection failed: {e}")

    def disconnect(self):
        if not self.is_connected:
            return
        self.publish(self.availability_topic, "offline", retain=True)
        if self._lock_timer:
            self._lock_timer.cancel()
        self.client.loop_stop()
        self.client.disconnect()
        self._log("info", "Disconnected")
        self.is_connected = False

    def publish(self, topic, payload, retain=False, qos=0):
        if not self.is_connected:
            return
        try:
            self.client.publish(topic, payload, qos=qos, retain=retain)
        except Exception as e:
            self._log("error", f"Publish {topic}: {e}")

    def publish_discovery(self, component, slug, name, device_class=None,
                          unit=None, icon=None, cmd_topic=None, state_class=None,
                          entity_category=None, extra_config=None):
        if not self.device_info_dict:
            return

        uid = f"{self.device_info_dict['identifiers'][0]}_{slug}"
        config_topic = f"homeassistant/{component}/{uid}/config"

        payload = {
            "name": name,
            "unique_id": uid,
            "device": self.device_info_dict,
            "availability_topic": self.availability_topic,
        }

        if component == "sensor":
            payload["state_topic"] = f"{self.base_topic}/sensor/{slug}"
            payload["value_template"] = "{{ value_json.state }}"
        elif component == "binary_sensor":
            payload["state_topic"] = f"{self.base_topic}/binary_sensor/{slug}"
            payload["payload_on"] = "ON"
            payload["payload_off"] = "OFF"
        elif component == "button":
            payload["command_topic"] = cmd_topic or f"{self.base_topic}/command/power/{slug}"
            payload["payload_press"] = "PRESS"
        elif component == "lock":
            payload["command_topic"] = f"{self.base_topic}/lock/{slug}/set"
            payload["state_topic"] = f"{self.base_topic}/lock/{slug}/state"
            payload["payload_lock"] = "LOCK"
            payload["payload_unlock"] = "UNLOCK"
            payload["state_locked"] = "LOCKED"
            payload["state_unlocked"] = "UNLOCKED"
            payload["optimistic"] = False

        if device_class:
            payload["device_class"] = device_class
        if unit:
            payload["unit_of_measurement"] = unit
        if icon:
            payload["icon"] = icon
        if state_class:
            payload["state_class"] = state_class
        if entity_category:
            payload["entity_category"] = entity_category
        if extra_config:
            payload.update(extra_config)

        self.publish(config_topic, json.dumps(payload), retain=True)

    def publish_state(self, component, slug, state):
        if not self.is_connected:
            return
        topic = f"{self.base_topic}/{component}/{slug}"
        if component == "sensor":
            self.publish(topic, json.dumps({"state": state}))
        else:
            self.publish(topic, str(state))

    def discover_and_publish_all(self, status):
        self._discover_lock()
        self._discover_power_buttons()
        self._discover_power_state(status)
        self._discover_system_health(status)
        self._discover_thermal(status)
        self._discover_power_sensors(status)
        self._discover_gpus(status)
        self._discover_storage(status)
        self._discover_memory(status)

    def _ensure_discovered(self, key, discover_fn):
        if key not in self.discovered_entities:
            discover_fn()
            self.discovered_entities.add(key)

    def _discover_lock(self):
        key = "lock_power_control"
        if key in self.discovered_entities:
            return
        extra = {}
        if self.power_pin:
            extra["code_format"] = r"^\d{4,8}$"
            extra["command_template"] = '{ "action": "{{ value }}", "code": "{{ code }}" }'
        self.publish_discovery(
            "lock", "power_control", "Power Control",
            icon="mdi:shield-lock", entity_category="config",
            extra_config=extra
        )
        self._publish_lock_state()
        self.discovered_entities.add(key)

    def _discover_power_buttons(self):
        buttons = [
            ("on", "Power On", "mdi:power"),
            ("graceful_shutdown", "Graceful Shutdown", "mdi:power-off"),
            ("force_off", "Force Power Off", "mdi:power-plug-off"),
            ("graceful_restart", "Graceful Restart", "mdi:restart"),
            ("force_restart", "Force Restart", "mdi:restart-alert"),
            ("power_cycle", "Power Cycle", "mdi:power-cycle"),
        ]
        for slug, name, icon in buttons:
            key = f"button_{slug}"
            if key in self.discovered_entities:
                continue
            self.publish_discovery(
                "button", slug, name, icon=icon,
                cmd_topic=f"{self.base_topic}/command/power/{slug}"
            )
            self.discovered_entities.add(key)

    def _discover_power_state(self, status):
        key = "bs_power_state"
        self._ensure_discovered(key, lambda: self.publish_discovery(
            "binary_sensor", "power_state", "Power State",
            device_class="power", icon="mdi:server"
        ))
        ps = status.get("system_info", {}).get("power_state")
        self.publish_state("binary_sensor", "power_state",
                           "ON" if ps == "On" else "OFF")

    def _discover_system_health(self, status):
        info = status.get("system_info")
        if not info:
            return

        rollups = [
            ("health", "System Health", "mdi:heart-pulse"),
            ("rollup_cpu", "CPU Health", "mdi:cpu-64-bit"),
            ("rollup_storage", "Storage Health", "mdi:harddisk"),
            ("rollup_memory", "Memory Health", "mdi:memory"),
            ("rollup_fan", "Fan Health", "mdi:fan"),
            ("rollup_psu", "PSU Health", "mdi:flash"),
            ("rollup_temp", "Temperature Health", "mdi:thermometer"),
        ]
        for field, name, icon in rollups:
            slug = f"health_{field}"
            self._ensure_discovered(slug, lambda n=name, i=icon: self.publish_discovery(
                "sensor", slug, n, icon=i, entity_category="diagnostic"
            ))
            val = info.get(field)
            if val:
                self.publish_state("sensor", slug, val)

    def _discover_thermal(self, status):
        thermal = status.get("thermal")
        if not thermal:
            return

        simple_temps = [
            ("hottest_cpu_temp", "Hottest CPU"),
            ("inlet_temp", "Inlet Temp"),
            ("exhaust_temp", "Exhaust Temp"),
        ]
        for slug, name in simple_temps:
            self._ensure_discovered(slug, lambda n=name: self.publish_discovery(
                "sensor", slug, n, device_class="temperature", unit="°C"
            ))

        hottest = max((c["temp"] for c in thermal.get("cpu_temps", [])), default=None)
        self.publish_state("sensor", "hottest_cpu_temp", hottest)
        self.publish_state("sensor", "inlet_temp", thermal.get("inlet_temp"))
        self.publish_state("sensor", "exhaust_temp", thermal.get("exhaust_temp"))

        for i, cpu in enumerate(thermal.get("cpu_temps", [])):
            slug = f"cpu_{i}_temp"
            self._ensure_discovered(slug, lambda n=cpu["name"]: self.publish_discovery(
                "sensor", slug, f"{n}", device_class="temperature", unit="°C"
            ))
            self.publish_state("sensor", slug, cpu["temp"])

        for fan in thermal.get("fans", []):
            slug = f"fan_{_safe_slug(fan['name'])}_rpm"
            self._ensure_discovered(slug, lambda n=fan["name"]: self.publish_discovery(
                "sensor", slug, f"{n} RPM", unit="RPM", icon="mdi:fan"
            ))
            self.publish_state("sensor", slug, fan["rpm"])

        target = status.get("target_fan_speed")
        self._ensure_discovered("target_fan_speed", lambda: self.publish_discovery(
            "sensor", "target_fan_speed", "Target Fan Speed",
            unit="%", icon="mdi:fan-chevron-up"
        ))
        self.publish_state("sensor", "target_fan_speed", target)

    def _discover_power_sensors(self, status):
        power = status.get("power")
        if not power:
            return

        power_sensors = [
            ("power_consumption", "Power Consumption", "W", "power", "mdi:flash", "measurement"),
            ("power_avg", "Avg Power (1min)", "W", "power", "mdi:flash", "measurement"),
            ("power_max", "Peak Power", "W", "power", "mdi:flash-triangle", None),
            ("power_min", "Min Power", "W", "power", "mdi:flash-outline", None),
            ("power_capacity", "Power Capacity", "W", "power", "mdi:flash-auto", None),
        ]
        field_map = {
            "power_consumption": "consumption_watts",
            "power_avg": "avg_watts",
            "power_max": "max_watts",
            "power_min": "min_watts",
            "power_capacity": "capacity_watts",
        }
        for slug, name, unit, dc, icon, sc in power_sensors:
            self._ensure_discovered(slug, lambda n=name, u=unit, d=dc, i=icon, s=sc:
                self.publish_discovery("sensor", slug, n, device_class=d, unit=u, icon=i, state_class=s))
            self.publish_state("sensor", slug, power.get(field_map[slug]))

        for psu in power.get("psus", []):
            psu_slug = _safe_slug(psu["name"])

            bs_slug = f"psu_{psu_slug}_health"
            self._ensure_discovered(bs_slug, lambda n=psu["name"]: self.publish_discovery(
                "binary_sensor", bs_slug, f"{n} Status",
                device_class="problem", entity_category="diagnostic"
            ))
            health = psu.get("health", "Unknown")
            self.publish_state("binary_sensor", bs_slug,
                               "ON" if health not in ("OK", "Unknown") else "OFF")

            if psu.get("input_watts") is not None:
                s = f"psu_{psu_slug}_input_watts"
                self._ensure_discovered(s, lambda n=psu["name"]: self.publish_discovery(
                    "sensor", s, f"{n} Input Power",
                    device_class="power", unit="W", icon="mdi:flash",
                    state_class="measurement", entity_category="diagnostic"
                ))
                self.publish_state("sensor", s, int(psu["input_watts"]))

    def _discover_gpus(self, status):
        gpus = status.get("gpus", [])
        for i, gpu in enumerate(gpus):
            name = gpu.get("name", f"GPU {i}")

            if gpu.get("temp_c") is not None:
                slug = f"gpu_{i}_temp"
                self._ensure_discovered(slug, lambda n=name: self.publish_discovery(
                    "sensor", slug, f"{n} Temperature",
                    device_class="temperature", unit="°C"
                ))
                self.publish_state("sensor", slug, gpu["temp_c"])

            if gpu.get("power_mw") is not None:
                slug = f"gpu_{i}_power"
                self._ensure_discovered(slug, lambda n=name: self.publish_discovery(
                    "sensor", slug, f"{n} Power",
                    device_class="power", unit="W", icon="mdi:flash",
                    state_class="measurement"
                ))
                self.publish_state("sensor", slug, round(gpu["power_mw"] / 1000, 1))

            if gpu.get("thermal_alert") is not None:
                slug = f"gpu_{i}_thermal_alert"
                self._ensure_discovered(slug, lambda n=name: self.publish_discovery(
                    "binary_sensor", slug, f"{n} Thermal Alert",
                    device_class="heat", entity_category="diagnostic"
                ))
                alert = gpu["thermal_alert"] not in ("NotPending", None)
                self.publish_state("binary_sensor", slug, "ON" if alert else "OFF")

    def _discover_storage(self, status):
        storage = status.get("storage")
        if not storage:
            return

        for ctrl in storage.get("controllers", []):
            cs = f"ctrl_{_safe_slug(ctrl['name'])}"

            slug = f"{cs}_health"
            self._ensure_discovered(slug, lambda n=ctrl["name"]: self.publish_discovery(
                "sensor", slug, f"{n} Health",
                icon="mdi:expansion-card", entity_category="diagnostic"
            ))
            self.publish_state("sensor", slug, ctrl.get("health"))

            if ctrl.get("cache_mb") and ctrl["cache_mb"] > 0:
                slug = f"{cs}_cache"
                self._ensure_discovered(slug, lambda n=ctrl["name"]: self.publish_discovery(
                    "sensor", slug, f"{n} Cache",
                    unit="MB", icon="mdi:memory", entity_category="diagnostic"
                ))
                self.publish_state("sensor", slug, ctrl["cache_mb"])

            if ctrl.get("battery_state") is not None:
                slug = f"{cs}_battery"
                self._ensure_discovered(slug, lambda n=ctrl["name"]: self.publish_discovery(
                    "sensor", slug, f"{n} Battery",
                    icon="mdi:battery", entity_category="diagnostic"
                ))
                self.publish_state("sensor", slug, ctrl["battery_state"])

        for drive in storage.get("drives", []):
            ds = f"drive_{_safe_slug(drive.get('id', drive['name']))}"

            slug = f"{ds}_health"
            self._ensure_discovered(slug, lambda n=drive["name"]: self.publish_discovery(
                "binary_sensor", slug, f"{n} Status",
                device_class="problem", entity_category="diagnostic"
            ))
            health = drive.get("health", "Unknown")
            self.publish_state("binary_sensor", slug,
                               "ON" if health not in ("OK", "Unknown") else "OFF")

            if drive.get("media_life_pct") is not None:
                slug = f"{ds}_life"
                self._ensure_discovered(slug, lambda n=drive["name"]: self.publish_discovery(
                    "sensor", slug, f"{n} Media Life",
                    unit="%", icon="mdi:harddisk", entity_category="diagnostic"
                ))
                self.publish_state("sensor", slug, drive["media_life_pct"])

            if drive.get("failure_predicted"):
                slug = f"{ds}_failure"
                self._ensure_discovered(slug, lambda n=drive["name"]: self.publish_discovery(
                    "binary_sensor", slug, f"{n} Failure Predicted",
                    device_class="problem"
                ))
                self.publish_state("binary_sensor", slug,
                                   "ON" if drive["failure_predicted"] else "OFF")

    def _discover_memory(self, status):
        mem = status.get("memory")
        if not mem:
            return

        slug = "memory_total"
        self._ensure_discovered(slug, lambda: self.publish_discovery(
            "sensor", slug, "Total Memory", unit="GB",
            icon="mdi:memory", entity_category="diagnostic"
        ))
        self.publish_state("sensor", slug, mem.get("total_gb"))

        for dimm in mem.get("dimms", []):
            ds = _safe_slug(dimm.get("id", dimm["name"]))
            slug = f"dimm_{ds}_health"
            self._ensure_discovered(slug, lambda n=dimm["name"]: self.publish_discovery(
                "binary_sensor", slug, f"{n} Status",
                device_class="problem", entity_category="diagnostic"
            ))
            health = dimm.get("health", "Unknown")
            self.publish_state("binary_sensor", slug,
                               "ON" if health not in ("OK", "Unknown") else "OFF")


def _safe_slug(name):
    return re.sub(r'[^a-zA-Z0-9_]+', '_', str(name)).strip('_').lower()
