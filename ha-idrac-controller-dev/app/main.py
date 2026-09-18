import os
import time
import signal
import threading
import json
from .redfish_client import RedfishClient
from .ipmi_manager import IPMIManager
from .mqtt_client import MqttClient
from .pid_controller import PIDController
from . import web_server

running = True
threads = []
status_lock = threading.Lock()
ALL_SERVERS_STATUS = {}
STATUS_FILE = "/data/current_status.json"
PID_STATE_FILE = "/data/pid_states.json"
HARDWARE_POLL_EVERY = 10


def graceful_shutdown(signum, frame):
    global running
    print("[MAIN] Shutdown signal received.", flush=True)
    running = False


signal.signal(signal.SIGTERM, graceful_shutdown)
signal.signal(signal.SIGINT, graceful_shutdown)


class ServerWorker:
    def __init__(self, server_config, global_opts):
        self.config = server_config
        self.global_opts = global_opts
        self.alias = self.config["alias"]
        self.log_level = self.global_opts["log_level"]
        self.running = True

        self.redfish = None
        self.ipmi = None
        self.client = None
        self.use_redfish = False
        self.use_ipmi = False

        self.mqtt = MqttClient(client_id=f"ha_idrac_{self.alias}")
        self.pid = PIDController()
        self.server_info = {}
        self.pcie_inventory = []
        self.cycle_count = 0

    def _log(self, level, msg):
        print(f"[{level.upper()}] [{self.alias}] {msg}", flush=True)

    def _init_backends(self):
        ip = self.config["idrac_ip"]
        user = self.config["idrac_username"]
        pwd = self.config["idrac_password"]

        self.redfish = RedfishClient(ip, user, pwd, log_level=self.log_level)
        self.ipmi = IPMIManager(ip, user, pwd, log_level=self.log_level)

        self._log("info", "Probing Redfish API...")
        if self.redfish.is_available():
            self.use_redfish = True
            self.client = self.redfish
            self._log("info", "Redfish available — using as primary backend")
        else:
            self._log("info", "Redfish not available, trying IPMI...")

        if not self.use_redfish:
            if self.ipmi.is_available():
                self.use_ipmi = True
                self.client = self.ipmi
                self._log("info", "IPMI available — using as fallback backend")
            else:
                self._log("error", "Neither Redfish nor IPMI available")
                return False

        if self.use_redfish and self.ipmi.is_available():
            self.use_ipmi = True
            self._log("info", "IPMI also available — fan control enabled")

        return True

    def _init_mqtt(self):
        self.mqtt.configure_broker(
            self.global_opts["mqtt_host"], self.global_opts["mqtt_port"],
            self.global_opts["mqtt_username"], self.global_opts["mqtt_password"],
            self.log_level
        )

        info = self.client.get_system_info()
        if info:
            self.server_info = info

        self.mqtt.set_device_info(
            self.alias,
            self.server_info.get("manufacturer"),
            self.server_info.get("model"),
            self.config.get("idrac_ip")
        )

        pin = self.global_opts.get("power_control_pin", "")
        timeout = self.global_opts.get("power_unlock_timeout", 60)
        self.mqtt.configure_power_pin(pin, timeout)
        self.mqtt.power_callback = self._on_power_command
        self.mqtt.connect()

        for _ in range(10):
            if self.mqtt.is_connected:
                return True
            time.sleep(1)
        return False

    def _init_pid(self):
        pid_config = self.config.get("pid_config", {})
        self.pid.setpoint = pid_config.get("target_temp", 55)
        self.pid.set_gains(
            pid_config.get("kp", 4.0),
            pid_config.get("ki", 0.2),
            pid_config.get("kd", 0.1)
        )
        if os.path.exists(PID_STATE_FILE):
            try:
                with open(PID_STATE_FILE, "r") as f:
                    states = json.load(f)
                if self.alias in states:
                    self.pid.load_state(states[self.alias])
            except (json.JSONDecodeError, IOError):
                pass

    def _on_power_command(self, action):
        self._log("info", f"Executing power action: {action}")
        result = self.client.power_action(action)
        if result:
            self._log("info", f"Power action '{action}' succeeded")
        else:
            self._log("error", f"Power action '{action}' failed")

    def _poll_fast(self):
        thermal = self.client.get_thermal()
        power = self.client.get_power()
        gpus = self.client.get_gpu_sensors()
        power_state = self.server_info.get("power_state")

        if self.use_redfish:
            ps = self.redfish.get_power_state()
            if ps:
                power_state = ps
                self.server_info["power_state"] = ps

        if self.use_redfish and gpus and not any(g.get("manufacturer") for g in gpus):
            if not self.pcie_inventory:
                self.pcie_inventory = self.client.get_pcie_inventory()
            self.client.enrich_gpu_names(gpus, self.pcie_inventory)

        return thermal, power, gpus

    def _poll_slow(self):
        storage = self.client.get_storage()
        memory = self.client.get_memory()

        if self.cycle_count == 0:
            info = self.client.get_system_info()
            if info:
                self.server_info.update(info)

        return storage, memory

    def _do_fan_control(self, thermal):
        if not self.config.get("fan_control_enabled", True):
            return None

        if not self.use_ipmi:
            return None

        cpu_temps = [c["temp"] for c in thermal.get("cpu_temps", [])]
        hottest = max(cpu_temps) if cpu_temps else None
        if hottest is None:
            self.ipmi.apply_dell_fan_control_profile()
            return None

        crit = self.config.get("critical_temp_threshold", 65)
        if hottest >= crit:
            self.ipmi.apply_dell_fan_control_profile()
            return None

        fan_mode = self.config.get("fan_mode", "simple")
        target = None

        if fan_mode == "simple":
            low = self.config.get("low_temp_threshold", 45)
            if hottest >= low:
                target = self.config.get("high_temp_fan_speed_percent", 50)
            else:
                target = self.config.get("base_fan_speed_percent", 20)

        elif fan_mode == "target":
            base = self.config.get("base_fan_speed_percent", 20)
            target = self.pid.update(hottest, base)

        elif fan_mode == "curve":
            curve = self.config.get("fan_curve", [])
            if len(curve) >= 2:
                lower, upper = curve[0], curve[-1]
                for j in range(len(curve) - 1):
                    if curve[j]["temp"] <= hottest < curve[j + 1]["temp"]:
                        lower, upper = curve[j], curve[j + 1]
                        break
                if hottest < lower["temp"]:
                    target = lower["speed"]
                elif hottest >= upper["temp"]:
                    target = upper["speed"]
                else:
                    tr = upper["temp"] - lower["temp"]
                    sr = upper["speed"] - lower["speed"]
                    target = lower["speed"] + (
                        (hottest - lower["temp"]) / tr * sr if tr > 0 else 0
                    )
                target = int(target)

        if target is not None:
            self.ipmi.apply_user_fan_control_profile(target)

        return target

    def run(self):
        if not self._init_backends():
            return
        if not self._init_mqtt():
            self._log("error", "MQTT init failed")
            return
        self._init_pid()

        self._log("info", "Worker started")

        while self.running and running:
            start = time.time()

            thermal, power, gpus = self._poll_fast()

            if thermal is None:
                self.mqtt.publish(self.mqtt.availability_topic, "offline", retain=True)
                time.sleep(60)
                continue

            self.mqtt.publish(self.mqtt.availability_topic, "online", retain=True)

            storage, memory = None, None
            if self.cycle_count % HARDWARE_POLL_EVERY == 0:
                storage, memory = self._poll_slow()

            target_fan = self._do_fan_control(thermal) if thermal else None

            status = {
                "system_info": self.server_info,
                "thermal": thermal,
                "power": power,
                "gpus": gpus,
                "storage": storage,
                "memory": memory,
                "target_fan_speed": target_fan,
            }

            self.mqtt.discover_and_publish_all(status)

            cpu_temps = [c["temp"] for c in thermal.get("cpu_temps", [])]
            hottest = max(cpu_temps) if cpu_temps else None

            with status_lock:
                ALL_SERVERS_STATUS[self.alias] = {
                    "alias": self.alias,
                    "ip": self.config["idrac_ip"],
                    "last_updated": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
                    "backend": "Redfish" if self.use_redfish else "IPMI",
                    "power_state": self.server_info.get("power_state"),
                    "model": self.server_info.get("model"),
                    "health": self.server_info.get("health"),
                    "hottest_cpu_temp_c": hottest,
                    "inlet_temp_c": thermal.get("inlet_temp"),
                    "exhaust_temp_c": thermal.get("exhaust_temp"),
                    "power_consumption_watts": power.get("consumption_watts") if power else None,
                    "target_fan_speed_percent": target_fan,
                    "gpu_count": len(gpus),
                    "drive_count": len(storage["drives"]) if storage else 0,
                }

            self.cycle_count += 1
            elapsed = time.time() - start
            sleep_time = max(0.1, self.global_opts["check_interval_seconds"] - elapsed)
            time.sleep(sleep_time)

    def cleanup(self):
        self._log("info", "Cleaning up...")
        if self.use_ipmi and self.config.get("fan_control_enabled", True):
            self.ipmi.apply_dell_fan_control_profile()

        if self.mqtt.is_connected:
            self.mqtt.disconnect()

        try:
            all_states = {}
            if os.path.exists(PID_STATE_FILE):
                with open(PID_STATE_FILE, "r") as f:
                    all_states = json.load(f)
            all_states[self.alias] = self.pid.get_state()
            with open(PID_STATE_FILE, "w") as f:
                json.dump(all_states, f, indent=2)
        except (IOError, json.JSONDecodeError):
            pass

    def stop(self):
        self.running = False


if __name__ == "__main__":
    print("[MAIN] ===== HA iDRAC Controller Starting =====", flush=True)

    global_options = {
        "log_level": os.getenv("LOG_LEVEL", "info"),
        "check_interval_seconds": int(os.getenv("CHECK_INTERVAL_SECONDS", 60)),
        "mqtt_host": os.getenv("MQTT_HOST", "core-mosquitto"),
        "mqtt_port": int(os.getenv("MQTT_PORT", 1883)),
        "mqtt_username": os.getenv("MQTT_USERNAME", ""),
        "mqtt_password": os.getenv("MQTT_PASSWORD", ""),
        "power_control_pin": os.getenv("POWER_CONTROL_PIN", ""),
        "power_unlock_timeout": int(os.getenv("POWER_UNLOCK_TIMEOUT", 60)),
        "base_fan_speed_percent": int(os.getenv("BASE_FAN_SPEED_PERCENT", 20)),
        "low_temp_threshold": int(os.getenv("LOW_TEMP_THRESHOLD", 45)),
        "high_temp_fan_speed_percent": int(os.getenv("HIGH_TEMP_FAN_SPEED_PERCENT", 50)),
        "critical_temp_threshold": int(os.getenv("CRITICAL_TEMP_THRESHOLD", 65)),
    }

    servers_config_file = "/data/servers_config.json"
    servers_configs_list = []
    if not os.path.exists(servers_config_file):
        with open(servers_config_file, "w") as f:
            json.dump([], f)
    else:
        with open(servers_config_file, "r") as f:
            try:
                servers_configs_list = json.load(f)
            except json.JSONDecodeError:
                pass

    web_server.global_config = global_options
    web_port = int(os.getenv("INGRESS_PORT", 8099))
    web_thread = threading.Thread(
        target=web_server.run_web_server,
        args=(web_port, STATUS_FILE, status_lock),
        daemon=True
    )
    web_thread.start()

    workers = []
    for conf in servers_configs_list:
        if conf.get("enabled", False):
            worker = ServerWorker(conf, global_options)
            workers.append(worker)
            t = threading.Thread(target=worker.run, daemon=True)
            threads.append(t)
            t.start()

    try:
        while running:
            with status_lock:
                with open(STATUS_FILE, "w") as f:
                    json.dump(list(ALL_SERVERS_STATUS.values()), f, indent=2)
            time.sleep(2)
    except KeyboardInterrupt:
        graceful_shutdown(None, None)

    print("[MAIN] Shutting down workers...", flush=True)
    for w in workers:
        w.stop()
    for w in workers:
        w.cleanup()
    for t in threads:
        t.join(timeout=5)
    print("[MAIN] ===== HA iDRAC Controller Stopped =====", flush=True)
