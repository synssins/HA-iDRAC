# HA iDRAC Controller

Monitor and control Dell PowerEdge servers via iDRAC in Home Assistant. Supports iDRAC7 (IPMI), iDRAC8 (Redfish/IPMI), and iDRAC9 (Redfish) with automatic protocol detection.

## Quick Install

[![Add Repository to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fsynssins%2FHA-iDRAC)

Click the button above to add this repository to your Home Assistant instance, then install the add-on from the Add-on Store.

### Manual Install

1. Go to **Settings > Add-ons > Add-on Store**
2. Click **⋮ > Repositories**
3. Add: `https://github.com/synssins/HA-iDRAC`
4. Find **HA iDRAC Controller** in the store and click **Install**

## Available Add-ons

### HA iDRAC Controller (Development)

Full-featured server monitoring and PIN-protected power control:

- **Monitoring** — CPU/GPU temps, fan RPMs, power draw, PSU health, drive health & SSD life, memory status, PCIe inventory, system health rollups
- **Power Control** — On, Off, Restart, Power Cycle with PIN code security gate
- **Fan Control** — Simple thresholds, multi-point curve, or PID target temperature (requires IPMI)
- **Multi-Server** — manage multiple servers from one add-on instance
- **Auto-Discovery** — all entities created automatically in Home Assistant via MQTT

See the [Development Add-on README](./ha-idrac-controller-dev/README.md) for full documentation.

### HA iDRAC Controller (Stable)

Legacy version with basic IPMI-only monitoring. See [Stable README](./ha-idrac-controller/README.md).

## Supported Hardware

| iDRAC | Servers | Protocol | Notes |
|-------|---------|----------|-------|
| **iDRAC9** | 14G+ (R740, R750) | Redfish | Full feature set |
| **iDRAC8** | 13G (R730) | Redfish (FW 2.40+) / IPMI | Some Dell OEM endpoints may be limited |
| **iDRAC7** | 12G (R720) | IPMI only | Basic monitoring, no drive/GPU/memory details |

## License

[MIT License](./LICENSE)
