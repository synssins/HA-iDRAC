import requests
import urllib3
import re

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class RedfishClient:
    SYSTEM_PATH = "/redfish/v1/Systems/System.Embedded.1"
    CHASSIS_PATH = "/redfish/v1/Chassis/System.Embedded.1"

    def __init__(self, host, username, password, verify_ssl=False, log_level="info"):
        self.host = host
        self.base_url = f"https://{host}"
        self.log_level = log_level.lower()
        self.session = requests.Session()
        self.session.auth = (username, password)
        self.session.verify = verify_ssl
        self.session.headers.update({"Content-Type": "application/json"})

    def _log(self, level, msg):
        levels = {"trace": -1, "debug": 0, "info": 1, "warning": 2, "error": 3}
        if levels.get(self.log_level, 1) <= levels.get(level, 1):
            print(f"[{level.upper()}] Redfish ({self.host}): {msg}", flush=True)

    def _get(self, path, timeout=30):
        try:
            r = self.session.get(f"{self.base_url}{path}", timeout=timeout)
            if r.status_code == 404:
                self._log("debug", f"Not found: {path}")
                return None
            r.raise_for_status()
            return r.json()
        except Exception as e:
            self._log("error", f"GET {path}: {e}")
            return None

    def _post(self, path, payload, timeout=30):
        try:
            r = self.session.post(f"{self.base_url}{path}", json=payload, timeout=timeout)
            if r.status_code in (200, 202, 204):
                self._log("info", f"POST {path}: {r.status_code}")
                return True
            self._log("error", f"POST {path}: {r.status_code} {r.text[:300]}")
            return False
        except Exception as e:
            self._log("error", f"POST {path}: {e}")
            return False

    def is_available(self):
        data = self._get("/redfish/v1/")
        if data:
            self._log("info", f"Redfish available: v{data.get('RedfishVersion', '?')}")
        return data is not None

    def get_system_info(self):
        data = self._get(self.SYSTEM_PATH)
        if not data:
            return None

        dell = data.get("Oem", {}).get("Dell", {}).get("DellSystem", {})
        proc = data.get("ProcessorSummary", {})
        mem = data.get("MemorySummary", {})

        return {
            "manufacturer": data.get("Manufacturer", "Unknown"),
            "model": data.get("Model", "Unknown"),
            "bios_version": data.get("BiosVersion"),
            "hostname": data.get("HostName"),
            "service_tag": data.get("SKU"),
            "serial_number": data.get("SerialNumber"),
            "power_state": data.get("PowerState"),
            "health": data.get("Status", {}).get("Health"),
            "health_rollup": data.get("Status", {}).get("HealthRollup"),
            "cpu_model": proc.get("Model"),
            "cpu_count": proc.get("Count"),
            "cpu_cores": proc.get("CoreCount"),
            "total_memory_gb": mem.get("TotalSystemMemoryGiB"),
            "memory_health": mem.get("Status", {}).get("Health"),
            "generation": dell.get("SystemGeneration"),
            "express_service_code": dell.get("ExpressServiceCode"),
            "chassis_height_u": dell.get("ChassisSystemHeightUnit"),
            "max_pcie_slots": dell.get("MaxPCIeSlots"),
            "populated_pcie_slots": dell.get("PopulatedPCIeSlots"),
            "populated_dimm_slots": dell.get("PopulatedDIMMSlots"),
            "max_dimm_slots": dell.get("MaxDIMMSlots"),
            "estimated_exhaust_temp_c": dell.get("EstimatedExhaustTemperatureCelsius"),
            "estimated_airflow_cfm": dell.get("EstimatedSystemAirflowCFM"),
            "rollup_health": dell.get("SystemHealthRollupStatus"),
            "rollup_cpu": dell.get("CPURollupStatus"),
            "rollup_storage": dell.get("StorageRollupStatus"),
            "rollup_memory": dell.get("SysMemPrimaryStatus"),
            "rollup_fan": dell.get("FanRollupStatus"),
            "rollup_psu": dell.get("PSRollupStatus"),
            "rollup_temp": dell.get("TempRollupStatus"),
            "rollup_voltage": dell.get("VoltRollupStatus"),
            "rollup_intrusion": dell.get("IntrusionRollupStatus"),
            "rollup_licensing": dell.get("LicensingRollupStatus"),
            "rollup_sel": dell.get("SELRollupStatus"),
        }

    def get_power_state(self):
        data = self._get(self.SYSTEM_PATH)
        return data.get("PowerState") if data else None

    def get_thermal(self):
        data = self._get(f"{self.CHASSIS_PATH}/Thermal")
        if not data:
            return None

        result = {"cpu_temps": [], "inlet_temp": None, "exhaust_temp": None, "fans": []}

        for t in data.get("Temperatures", []):
            reading = t.get("ReadingCelsius")
            if reading is None:
                continue
            name = t.get("Name", "")
            reading = int(reading)
            nl = name.lower()
            if "inlet" in nl:
                result["inlet_temp"] = reading
            elif "exhaust" in nl:
                result["exhaust_temp"] = reading
            elif any(k in nl for k in ("cpu", "temp")):
                result["cpu_temps"].append({"name": name, "temp": reading})

        for f in data.get("Fans", []):
            rpm = f.get("Reading")
            if rpm is None:
                continue
            result["fans"].append({
                "name": f.get("FanName", f.get("Name", "Unknown")),
                "rpm": int(rpm),
                "health": f.get("Status", {}).get("Health", "Unknown"),
            })

        return result

    def get_power(self):
        data = self._get(f"{self.CHASSIS_PATH}/Power")
        if not data:
            return None

        result = {
            "consumption_watts": None, "avg_watts": None, "max_watts": None,
            "min_watts": None, "capacity_watts": None, "psus": [],
        }

        for pc in data.get("PowerControl", []):
            consumed = pc.get("PowerConsumedWatts")
            result["consumption_watts"] = int(consumed) if consumed is not None else None
            cap = pc.get("PowerCapacityWatts")
            result["capacity_watts"] = int(cap) if cap is not None else None
            m = pc.get("PowerMetrics", {})
            avg = m.get("AverageConsumedWatts")
            result["avg_watts"] = int(avg) if avg is not None else None
            mx = m.get("MaxConsumedWatts")
            result["max_watts"] = int(mx) if mx is not None else None
            mn = m.get("MinConsumedWatts")
            result["min_watts"] = int(mn) if mn is not None else None

        for ps in data.get("PowerSupplies", []):
            psu = {
                "name": ps.get("Name", "Unknown"),
                "model": ps.get("Model"),
                "manufacturer": ps.get("Manufacturer"),
                "serial": ps.get("SerialNumber"),
                "firmware": ps.get("FirmwareVersion"),
                "input_watts": ps.get("PowerInputWatts"),
                "output_watts": ps.get("PowerOutputWatts"),
                "capacity_watts": ps.get("PowerCapacityWatts"),
                "health": ps.get("Status", {}).get("Health", "Unknown"),
                "state": ps.get("Status", {}).get("State", "Unknown"),
                "line_input_voltage": ps.get("LineInputVoltage"),
            }
            result["psus"].append(psu)

        return result

    def get_storage(self):
        collection = self._get(f"{self.SYSTEM_PATH}/Storage")
        if not collection:
            return None

        result = {"controllers": [], "drives": []}

        for member in collection.get("Members", []):
            ctrl_path = member.get("@odata.id")
            ctrl_data = self._get(ctrl_path)
            if not ctrl_data:
                continue

            dell_ctrl = ctrl_data.get("Oem", {}).get("Dell", {}).get("DellController", {})

            controller = {
                "name": ctrl_data.get("Name", "Unknown"),
                "id": ctrl_data.get("Id"),
                "health": ctrl_data.get("Status", {}).get("Health", "Unknown"),
                "health_rollup": ctrl_data.get("Status", {}).get("HealthRollup", "Unknown"),
                "firmware": dell_ctrl.get("ControllerFirmwareVersion"),
                "cache_mb": dell_ctrl.get("CacheSizeInMB", 0),
                "alarm_state": dell_ctrl.get("AlarmState"),
                "pci_slot": dell_ctrl.get("PCISlot"),
                "encryption_mode": dell_ctrl.get("EncryptionMode"),
                "security_status": dell_ctrl.get("SecurityStatus"),
                "patrol_read_state": dell_ctrl.get("PatrolReadState"),
                "rollup_status": dell_ctrl.get("RollupStatus"),
                "battery_state": None,
                "battery_charge_pct": None,
            }

            # ponytail: battery endpoint only exists on PERC controllers with cache, 404 otherwise
            if controller["cache_mb"] and controller["cache_mb"] > 0:
                batt = self._get(f"{ctrl_path}/Oem/Dell/DellControllerBattery")
                if batt:
                    controller["battery_state"] = batt.get("PrimaryStatus")
                    controller["battery_charge_pct"] = batt.get("RAIDState")

            result["controllers"].append(controller)

            for drive_ref in ctrl_data.get("Drives", []):
                drive_path = drive_ref.get("@odata.id")
                drive_data = self._get(drive_path)
                if not drive_data:
                    continue

                dell_disk = (drive_data.get("Oem", {}).get("Dell", {})
                             .get("DellPhysicalDisk", {}))

                loc = (drive_data.get("PhysicalLocation", {})
                       .get("PartLocation", {}))

                drive = {
                    "name": drive_data.get("Name", "Unknown"),
                    "id": drive_data.get("Id"),
                    "model": drive_data.get("Model"),
                    "manufacturer": drive_data.get("Manufacturer"),
                    "serial": drive_data.get("SerialNumber"),
                    "firmware": drive_data.get("Revision"),
                    "capacity_bytes": drive_data.get("CapacityBytes"),
                    "media_type": drive_data.get("MediaType"),
                    "protocol": drive_data.get("Protocol"),
                    "health": drive_data.get("Status", {}).get("Health", "Unknown"),
                    "state": drive_data.get("Status", {}).get("State", "Unknown"),
                    "media_life_pct": drive_data.get("PredictedMediaLifeLeftPercent"),
                    "failure_predicted": drive_data.get("FailurePredicted", False),
                    "capable_speed_gbs": drive_data.get("CapableSpeedGbs"),
                    "negotiated_speed_gbs": drive_data.get("NegotiatedSpeedGbs"),
                    "controller": ctrl_data.get("Name"),
                    "controller_id": ctrl_data.get("Id"),
                    "slot": loc.get("LocationOrdinalValue"),
                    "raid_status": dell_disk.get("RaidStatus"),
                    "power_status": dell_disk.get("PowerStatus"),
                    "form_factor": dell_disk.get("DriveFormFactor"),
                    "encryption_status": drive_data.get("EncryptionStatus"),
                }
                result["drives"].append(drive)

        return result

    def get_memory(self):
        collection = self._get(f"{self.SYSTEM_PATH}/Memory")
        if not collection:
            return None

        result = {"total_gb": 0, "dimms": []}

        for member in collection.get("Members", []):
            dimm_data = self._get(member.get("@odata.id"))
            if not dimm_data:
                continue
            if dimm_data.get("Status", {}).get("State") == "Absent":
                continue

            size_mb = dimm_data.get("CapacityMiB", 0)
            size_gb = round(size_mb / 1024) if size_mb else 0

            dimm = {
                "name": dimm_data.get("Name", dimm_data.get("Id", "Unknown")),
                "id": dimm_data.get("Id"),
                "size_gb": size_gb,
                "type": dimm_data.get("MemoryDeviceType"),
                "speed_mhz": dimm_data.get("OperatingSpeedMhz"),
                "manufacturer": dimm_data.get("Manufacturer"),
                "serial": dimm_data.get("SerialNumber"),
                "part_number": dimm_data.get("PartNumber"),
                "health": dimm_data.get("Status", {}).get("Health", "Unknown"),
                "state": dimm_data.get("Status", {}).get("State", "Unknown"),
                "rank_count": dimm_data.get("RankCount"),
                "bus_width_bits": dimm_data.get("BusWidthBits"),
                "error_correction": dimm_data.get("ErrorCorrection"),
            }
            result["dimms"].append(dimm)
            result["total_gb"] += size_gb

        return result

    def get_gpu_sensors(self):
        data = self._get(f"{self.SYSTEM_PATH}/Oem/Dell/DellGPUSensors")
        if not data:
            return []

        gpus = []
        for m in data.get("Members", []):
            gpu = {
                "id": m.get("Id"),
                "name": "GPU",
                "temp_c": m.get("PrimaryGPUTemperatureCelsius"),
                "power_mw": m.get("PowerConsumptionmW"),
                "max_temp_c": m.get("MaximumGPUOperatingTemperatureCelsius"),
                "shutdown_temp_c": m.get("GPUShutdownTemperatureCelsius"),
                "slowdown_temp_c": m.get("MinimumGPUHardwareSlowdownTemperatureCelsius"),
                "thermal_alert": m.get("ThermalAlertStatus"),
                "power_brake": m.get("PowerBrakeStatus"),
                "power_supply_status": m.get("PowerSupplyStatus"),
                "memory_temp_c": m.get("MemoryTemperatureCelsius"),
                "board_temp_c": m.get("BoardTemperatureCelsius"),
            }
            gpus.append(gpu)
        return gpus

    def get_pcie_inventory(self):
        collection = self._get(f"{self.CHASSIS_PATH}/PCIeDevices")
        if not collection:
            return []

        devices = []
        for member in collection.get("Members", []):
            dev = self._get(member.get("@odata.id"))
            if not dev:
                continue

            slot = dev.get("Slot", {})
            pcie_if = dev.get("PCIeInterface", {})
            loc = slot.get("Location", {}).get("PartLocation", {})

            devices.append({
                "id": dev.get("Id"),
                "name": dev.get("Name", "Unknown"),
                "manufacturer": dev.get("Manufacturer"),
                "model": dev.get("Model"),
                "description": dev.get("Description"),
                "serial": dev.get("SerialNumber"),
                "part_number": dev.get("PartNumber"),
                "firmware": dev.get("FirmwareVersion"),
                "health": dev.get("Status", {}).get("Health", "Unknown"),
                "slot": loc.get("LocationOrdinalValue"),
                "pcie_type": pcie_if.get("PCIeType"),
                "lanes_in_use": pcie_if.get("LanesInUse"),
                "max_lanes": pcie_if.get("MaxLanes"),
            })
        return devices

    def get_network_adapters(self):
        collection = self._get(f"{self.CHASSIS_PATH}/NetworkAdapters")
        if not collection:
            return []

        adapters = []
        for member in collection.get("Members", []):
            nic = self._get(member.get("@odata.id"))
            if not nic:
                continue
            adapters.append({
                "id": nic.get("Id"),
                "name": nic.get("Name", "Unknown"),
                "manufacturer": nic.get("Manufacturer"),
                "model": nic.get("Model"),
                "serial": nic.get("SerialNumber"),
                "part_number": nic.get("PartNumber"),
                "health": nic.get("Status", {}).get("Health", "Unknown"),
            })
        return adapters

    def enrich_gpu_names(self, gpu_sensors, pcie_devices):
        for gpu in gpu_sensors:
            gpu_id = gpu.get("id", "")
            slot_match = re.search(r"\.Slot\.(\d+)-", gpu_id)
            if not slot_match:
                continue
            gpu_slot = int(slot_match.group(1))
            for pcie in pcie_devices:
                if pcie.get("slot") == gpu_slot:
                    gpu["name"] = pcie.get("model") or pcie.get("name") or "GPU"
                    gpu["manufacturer"] = pcie.get("manufacturer")
                    gpu["pcie_type"] = pcie.get("pcie_type")
                    gpu["lanes"] = pcie.get("lanes_in_use")
                    break

    def power_action(self, action):
        valid = {"On", "ForceOff", "GracefulShutdown", "GracefulRestart",
                 "ForceRestart", "PowerCycle", "PushPowerButton", "Nmi"}
        if action not in valid:
            self._log("error", f"Invalid power action: {action}")
            return False

        self._log("info", f"Executing power action: {action}")
        return self._post(
            f"{self.SYSTEM_PATH}/Actions/ComputerSystem.Reset",
            {"ResetType": action}
        )
