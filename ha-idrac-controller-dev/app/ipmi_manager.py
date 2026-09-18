import subprocess
import time
import re


class IPMIManager:
    def __init__(self, ip, user, password, conn_type="lanplus", log_level="info"):
        self.ip = ip
        self.user = user
        self.password = password
        self.log_level = log_level.lower()
        self.base_args = self._build_base_args(conn_type)

    def _build_base_args(self, conn_type):
        if conn_type.lower() in ("local", "open"):
            return ["-I", "open"]
        return ["-I", "lanplus", "-H", self.ip, "-U", self.user, "-P", self.password]

    def _log(self, level, msg):
        levels = {"trace": -1, "debug": 0, "info": 1, "warning": 2, "error": 3}
        if levels.get(self.log_level, 1) <= levels.get(level, 1):
            print(f"[{level.upper()}] IPMI ({self.ip}): {msg}", flush=True)

    def _run(self, args, raw=True, timeout=15):
        if not self.base_args:
            return None
        cmd = ["ipmitool"] + self.base_args + (["raw"] + args if raw else args)
        self._log("debug", f"Exec: {' '.join(cmd)}")
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=timeout)
            if r.returncode != 0:
                self._log("error", f"Failed: {r.stderr.strip()}")
                return None
            return r.stdout.strip()
        except FileNotFoundError:
            self._log("error", "ipmitool not found in PATH")
        except subprocess.TimeoutExpired:
            self._log("error", f"Command timed out after {timeout}s")
        except Exception as e:
            self._log("error", str(e))
        return None

    def is_available(self):
        result = self._run(["chassis", "status"], raw=False, timeout=10)
        if result:
            self._log("info", "IPMI connection verified")
        return result is not None

    def get_system_info(self):
        fru = self._run(["fru"], raw=False, timeout=20)
        if not fru:
            return None

        info = {"manufacturer": "Unknown", "model": "Unknown"}
        for key, primary, fallback in [
            ("manufacturer", r"Product Manufacturer\s*:\s*(.*)", r"Board Mfg\s*:\s*(.*)"),
            ("model", r"Product Name\s*:\s*(.*)", r"Board Product\s*:\s*(.*)")
        ]:
            m = re.search(primary, fru, re.IGNORECASE)
            if not m:
                m = re.search(fallback, fru, re.IGNORECASE)
            if m:
                info[key] = m.group(1).strip()

        if "dell" in info["manufacturer"].lower():
            info["manufacturer"] = "Dell Inc."

        info.update({
            "bios_version": None, "hostname": None, "service_tag": None,
            "serial_number": None, "health": None, "health_rollup": None,
            "cpu_model": None, "cpu_count": None, "cpu_cores": None,
            "total_memory_gb": None, "memory_health": None,
            "generation": None, "express_service_code": None,
        })

        status = self._run(["chassis", "status"], raw=False)
        if status:
            m = re.search(r"System Power\s*:\s*(on|off)", status, re.IGNORECASE)
            if m:
                info["power_state"] = m.group(1).capitalize()

        return info

    def get_thermal(self):
        result = {"cpu_temps": [], "inlet_temp": None, "exhaust_temp": None, "fans": []}

        sdr = self._run(["sdr", "type", "temperature"], raw=False)
        if sdr:
            regex = re.compile(
                r"^(.*?)\s*\|\s*[\da-fA-F]+h\s*\|\s*ok\s*.*?\|\s*([-+]?\d*\.?\d+)\s*degrees C",
                re.IGNORECASE
            )
            for line in sdr.splitlines():
                m = regex.match(line.strip())
                if not m:
                    continue
                name, val = m.group(1).strip(), int(float(m.group(2)))
                nl = name.lower()
                if "inlet" in nl:
                    result["inlet_temp"] = val
                elif "exhaust" in nl:
                    result["exhaust_temp"] = val
                elif any(k in nl for k in ("cpu", "temp")):
                    result["cpu_temps"].append({"name": name, "temp": val})

        sdr = self._run(["sdr", "type", "fan"], raw=False, timeout=10)
        if sdr:
            regex = re.compile(
                r"^(.*?)\s*\|\s*[\da-fA-F]+h\s*\|\s*ok\s*.*?\|\s*([\d\.]+)\s*RPM",
                re.IGNORECASE
            )
            for line in sdr.splitlines():
                m = regex.match(line.strip())
                if not m:
                    continue
                result["fans"].append({
                    "name": m.group(1).strip(),
                    "rpm": int(float(m.group(2))),
                    "health": "OK",
                })

        return result

    def get_power(self):
        result = {
            "consumption_watts": None, "avg_watts": None, "max_watts": None,
            "min_watts": None, "capacity_watts": None, "psus": [],
        }

        sdr = self._run(["sdr", "elist"], raw=False, timeout=20)
        if not sdr:
            return result

        for line in sdr.splitlines():
            m = re.search(
                r"(Pwr Consumption|System Level.*?)\s*\|.*?\s*([\d\.]+)\s*Watts",
                line, re.IGNORECASE
            )
            if m:
                result["consumption_watts"] = int(float(m.group(2)))
                break

        psus = {}
        for line in sdr.splitlines():
            m = re.search(r"Status\s*\|\s*[\da-fA-F]+h\s*\|\s*ok\s*\|\s*10\.(\d)\s*\|\s*Presence detected", line)
            if m:
                psus.setdefault(m.group(1), {})["present"] = True
                continue
            m = re.search(r"Voltage (\d)\s*\|\s*[\da-fA-F]+h\s*\|\s*ok\s*.*?\|\s*([\d\.]+)\s*Volts", line)
            if m:
                psus.setdefault(m.group(1), {})["voltage"] = float(m.group(2))
                continue
            m = re.search(r"PS(\d) PG Fail\s*\|\s*[\da-fA-F]+h\s*\|\s*(ok|nr)", line)
            if m:
                psus.setdefault(m.group(1), {})["fault"] = m.group(2) not in ("ok", "nr")

        for idx, data in sorted(psus.items()):
            ok = (data.get("present", False) and
                  data.get("voltage", 0) > 100 and
                  not data.get("fault", True))
            result["psus"].append({
                "name": f"PSU {idx}",
                "health": "OK" if ok else "Critical",
                "state": "Enabled" if data.get("present") else "Absent",
                "model": None, "manufacturer": None, "serial": None,
                "firmware": None, "input_watts": None, "output_watts": None,
                "capacity_watts": None, "line_input_voltage": None,
            })

        return result

    def get_storage(self):
        return None

    def get_memory(self):
        return None

    def get_gpu_sensors(self):
        sdr = self._run(["sdr", "type", "temperature"], raw=False)
        if not sdr:
            return []

        gpus = []
        regex = re.compile(
            r"^(.*?)\s*\|\s*[\da-fA-F]+h\s*\|\s*ok\s*.*?\|\s*([-+]?\d*\.?\d+)\s*degrees C",
            re.IGNORECASE
        )
        for line in sdr.splitlines():
            m = regex.match(line.strip())
            if not m:
                continue
            name = m.group(1).strip()
            if "gpu" in name.lower():
                gpus.append({
                    "id": name, "name": name,
                    "temp_c": int(float(m.group(2))),
                    "power_mw": None, "max_temp_c": None,
                    "shutdown_temp_c": None, "slowdown_temp_c": None,
                    "thermal_alert": None, "power_brake": None,
                    "power_supply_status": None, "memory_temp_c": None,
                    "board_temp_c": None,
                })
        return gpus

    def get_pcie_inventory(self):
        return []

    def get_network_adapters(self):
        return []

    def enrich_gpu_names(self, gpu_sensors, pcie_devices):
        pass

    def power_action(self, action):
        action_map = {
            "On": ["chassis", "power", "on"],
            "ForceOff": ["chassis", "power", "off"],
            "GracefulShutdown": ["chassis", "power", "soft"],
            "GracefulRestart": ["chassis", "power", "reset"],
            "ForceRestart": ["chassis", "power", "reset"],
            "PowerCycle": ["chassis", "power", "cycle"],
        }
        cmd = action_map.get(action)
        if not cmd:
            self._log("error", f"Unsupported IPMI power action: {action}")
            return False
        self._log("info", f"Power action: {action}")
        return self._run(cmd, raw=False) is not None

    def apply_dell_fan_control_profile(self):
        self._log("info", "Reverting to Dell auto fan control")
        return self._run(["0x30", "0x30", "0x01", "0x01"]) is not None

    def apply_user_fan_control_profile(self, speed_pct):
        hex_speed = f"0x{max(0, min(100, int(speed_pct))):02x}"
        self._log("info", f"Setting fan speed: {speed_pct}% ({hex_speed})")
        if self._run(["0x30", "0x30", "0x01", "0x00"]) is None:
            return False
        time.sleep(0.5)
        return self._run(["0x30", "0x30", "0x02", "0xff", hex_speed]) is not None
