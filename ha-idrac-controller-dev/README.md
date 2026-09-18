# HA iDRAC Controller

Home Assistant add-on for Dell iDRAC server monitoring and power control via Redfish API with IPMI fallback.

**DEVELOPMENT VERSION - USE WITH CAUTION.** Incorrect fan configuration could lead to server overheating if not carefully monitored.

---

## Supported Hardware

| iDRAC Generation | Servers | Protocol | Capabilities |
|-----------------|---------|----------|-------------|
| **iDRAC9** | 14G+ (R740, R750, etc) | Redfish (primary) | Full monitoring, power control, GPU sensors, drive health, memory status, PCIe inventory |
| **iDRAC8** | 13G (R730, etc) | Redfish (FW 2.40+) / IPMI fallback | Monitoring + power control. Some Dell OEM endpoints may be unavailable on older firmware |
| **iDRAC7** | 12G (R720, etc) | IPMI only | Basic monitoring (temps, fans, power, PSU status), fan control, power control. No drive/memory/GPU details |

## Protocol Auto-Detection

The add-on automatically detects the best available protocol per server:

1. **Try Redfish** (`https://<idrac>/redfish/v1/`) — if it responds, use Redfish for all monitoring and power control
2. **Try IPMI** — if Redfish is unavailable (iDRAC7 or Redfish disabled), fall back to IPMI via `ipmitool`
3. **Both available** — Redfish used for monitoring/power, IPMI used for Dell-specific fan raw commands (manual fan speed control)

Fan speed control (`0x30 0x30` raw commands) is **IPMI-only**. If IPMI is disabled on your iDRAC, fan control stays on Dell Auto.

## Features

### Monitoring
- **Temperatures** — per-CPU, inlet, exhaust, GPU (iDRAC8/9)
- **Fan Speeds** — per-fan RPM with health status
- **Power** — current/average/peak/min watts, per-PSU health and input power
- **Storage** — per-drive health, SSD media life %, SMART failure prediction, RAID controller status, cache battery state
- **Memory** — per-DIMM health status
- **GPU Sensors** — temperature, power draw, thermal alerts (Dell OEM, iDRAC8/9)
- **System Health** — subsystem rollup status (CPU, storage, memory, fans, PSU, temperature, voltage)
- **Power State** — server on/off as binary sensor

### Power Control (PIN-Protected)
All power actions require unlocking a PIN-protected lock entity first:
- **Power On** — turn server on
- **Graceful Shutdown** — ACPI shutdown (clean OS shutdown)
- **Force Power Off** — immediate power cut
- **Graceful Restart** — ACPI restart
- **Force Restart** — immediate hardware reset
- **Power Cycle** — full power off then on

### Fan Control (requires IPMI)
Three modes, configurable per-server:
- **Simple Thresholds** — base/high/critical temperature tiers
- **Multi-Point Curve** — custom temperature-to-fan-speed curve with linear interpolation
- **Target Temperature (PID)** — automatic PID-controlled fan speed to maintain target CPU temp

### Multi-Server Support
Monitor and control multiple servers from a single add-on instance. Each server gets its own HA device with all entities.

## PIN Code Security

Power control actions (on/off/restart) require unlocking via PIN code:

1. Set `power_control_pin` in add-on configuration (4-8 digits)
2. In Home Assistant, find the **Power Control** lock entity for your server
3. Enter PIN to unlock — power buttons become active
4. After executing a power action OR timeout expiry, controls automatically re-lock
5. If no PIN is configured, power controls work without authentication

The PIN is validated server-side in the add-on. The lock entity uses HA's native PIN input UI via MQTT `code_format` and `command_template`.

## Configuration

### Add-on Options

| Option | Default | Description |
|--------|---------|-------------|
| `power_control_pin` | *(empty)* | 4-8 digit PIN for power control. Leave empty to disable |
| `power_unlock_timeout` | `60` | Seconds before power controls auto-lock (10-300) |
| `check_interval_seconds` | `30` | Polling interval in seconds |
| `log_level` | `info` | Logging verbosity (trace/debug/info/warning/error) |
| `mqtt_host` | `core-mosquitto` | MQTT broker hostname |
| `mqtt_port` | `1883` | MQTT broker port |
| `mqtt_username` | *(empty)* | MQTT username |
| `mqtt_password` | *(empty)* | MQTT password |
| `temperature_unit` | `C` | Temperature display unit |
| `base_fan_speed_percent` | `20` | Default fan speed in simple mode |
| `low_temp_threshold` | `45` | Temperature for fan ramp-up |
| `high_temp_fan_speed_percent` | `50` | Fan speed above threshold |
| `critical_temp_threshold` | `65` | Temperature to revert to Dell auto fans |

### Per-Server Configuration

Servers are configured via the add-on's web UI (ingress panel). Each server needs:

- **Alias** — friendly name (used in HA entity naming)
- **iDRAC IP** — hostname or IP address
- **Username/Password** — iDRAC credentials with Operator or Administrator rights
- **Fan Control** — enable/disable, mode selection, thresholds

## Polling Strategy

To avoid overloading the iDRAC, polling uses two tiers:

| Tier | Frequency | Data |
|------|-----------|------|
| **Fast** | Every interval (default 30s) | Thermal, power, GPU sensors, power state |
| **Slow** | Every 10th cycle (~5 min) | Storage health, memory status, system info |

## Entities Created in Home Assistant

For each server, a device is created with:

**Sensors:** Hottest CPU temp, per-CPU temps, inlet/exhaust temps, power consumption (current/avg/peak/min), power capacity, per-fan RPMs, target fan speed, per-GPU temp & power, drive media life %, controller health & cache, total memory, system health rollups (CPU/storage/memory/fan/PSU/temp)

**Binary Sensors:** Power state, per-PSU health, per-drive health, drive failure prediction, per-DIMM health, GPU thermal alerts

**Buttons:** Power On, Graceful Shutdown, Force Off, Graceful Restart, Force Restart, Power Cycle

**Lock:** Power Control (PIN-gated)

## Architecture

```
┌─────────────┐     ┌──────────────────┐
│  iDRAC 9    │◄────│  RedfishClient   │◄───┐
│  (Redfish)  │     │  (HTTPS/JSON)    │    │
└─────────────┘     └──────────────────┘    │     ┌──────────┐
                                            ├────►│ MQTT     │───► Home Assistant
┌─────────────┐     ┌──────────────────┐    │     │ (Auto-   │    (Sensors, Buttons,
│  iDRAC 7    │◄────│  IPMIManager     │◄───┘     │  Disco)  │     Lock, Binary Sensors)
│  (IPMI)     │     │  (ipmitool)      │          └──────────┘
└─────────────┘     └──────────────────┘
```

Both backends expose the same interface. The main loop auto-detects Redfish vs IPMI per server. IPMI is used alongside Redfish when fan raw commands are needed.

## Prerequisites

1. **Dell PowerEdge Server** with iDRAC7, iDRAC8, or iDRAC9
2. **iDRAC credentials** with Operator or Administrator privileges
3. **Network access** from HA to iDRAC (HTTPS port 443 for Redfish, or IPMI-over-LAN for IPMI)
4. **MQTT broker** — the `core-mosquitto` HA add-on is recommended
5. For **Redfish**: ensure the Redfish service is enabled in iDRAC settings
6. For **IPMI**: enable IPMI-over-LAN in iDRAC network settings
7. For **fan control**: IPMI must be enabled (Redfish cannot send Dell fan raw commands)

## Troubleshooting

- **Check the Add-on Log** — set `log_level` to `debug` or `trace` for detail
- **Redfish 401 errors** — verify iDRAC credentials and user permissions (Operator minimum)
- **No storage/GPU data on iDRAC8** — older firmware may not support Dell OEM Redfish extensions. Update firmware if possible
- **Fan control not working** — requires IPMI-over-LAN enabled. Check iDRAC network settings
- **MQTT errors** — verify broker credentials in add-on configuration
- **Power buttons don't work** — unlock the Power Control lock entity first (enter PIN)

## License

[MIT License](LICENSE)
