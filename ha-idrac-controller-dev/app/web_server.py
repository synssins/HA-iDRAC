from flask import Flask, render_template, request, redirect, flash, url_for, jsonify
from markupsafe import Markup
import os
import json
import logging
import threading
import time
from collections import deque

log = logging.getLogger('werkzeug')
app = Flask(__name__)
app.secret_key = os.urandom(24)

class IngressMiddleware:
    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        ingress_path = environ.get('HTTP_X_INGRESS_PATH', '')
        if ingress_path:
            environ['SCRIPT_NAME'] = ingress_path
            path_info = environ.get('PATH_INFO', '')
            if path_info.startswith(ingress_path):
                environ['PATH_INFO'] = path_info[len(ingress_path):]
        return self.wsgi_app(environ, start_response)

app.wsgi_app = IngressMiddleware(app.wsgi_app)

STATUS_FILE = None
SERVERS_CONFIG_FILE = "/data/servers_config.json"
status_lock = None
config_lock = threading.Lock()
global_config = {}

MAX_HISTORY_POINTS = 360
server_history = {}
history_lock = threading.Lock()

def record_history():
    while True:
        try:
            statuses = load_all_servers_status()
            ts = time.time()
            with history_lock:
                for s in statuses:
                    alias = s.get('alias', '')
                    if not alias:
                        continue
                    if alias not in server_history:
                        server_history[alias] = deque(maxlen=MAX_HISTORY_POINTS)
                    server_history[alias].append({
                        't': ts,
                        'cpu': s.get('hottest_cpu_temp_c'),
                        'inlet': s.get('inlet_temp_c'),
                        'exhaust': s.get('exhaust_temp_c'),
                        'power': s.get('power_consumption_watts'),
                        'fan': s.get('target_fan_speed_percent'),
                    })
        except Exception:
            pass
        time.sleep(30)

def load_servers_config():
    with config_lock:
        if not os.path.exists(SERVERS_CONFIG_FILE): return []
        try:
            with open(SERVERS_CONFIG_FILE, 'r') as f: return json.load(f)
        except (json.JSONDecodeError, IOError): return []

def save_servers_config(servers):
    with config_lock:
        try:
            with open(SERVERS_CONFIG_FILE, 'w') as f:
                json.dump(servers, f, indent=4)
            restart_url = "/hassio/dashboard"
            message = Markup(f"Configuration saved! <a href='{restart_url}' target='_top'>Click here to go to the Add-ons dashboard to RESTART</a> for changes to take effect.")
            flash(message, "success")
            return True
        except IOError:
            flash("Error: Could not write to config file.", "error")
            return False

def load_all_servers_status():
    if STATUS_FILE and os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, 'r') as f: return json.load(f)
        except (json.JSONDecodeError, IOError): pass
    return []

@app.route('/')
def index():
    all_statuses = load_all_servers_status()
    all_statuses.sort(key=lambda x: x.get('alias', ''))
    return render_template('index.html', servers=all_statuses, active_page='dashboard')

@app.route('/servers')
def manage_servers():
    servers = load_servers_config()
    return render_template('servers.html', servers=servers, defaults=global_config, active_page='servers')

@app.route('/servers/add', methods=['POST'])
def add_server():
    servers = load_servers_config()
    new_alias = request.form.get('alias')
    if any(s['alias'] == new_alias for s in servers):
        flash(f"Server alias '{new_alias}' already exists.", "error")
        return redirect(url_for('manage_servers'))
    new_server = {
        "alias": new_alias,
        "idrac_ip": request.form.get('idrac_ip'),
        "idrac_username": request.form.get('idrac_username'),
        "idrac_password": request.form.get('idrac_password'),
        "enabled": True,
        "fan_control_enabled": request.form.get('fan_control_enabled') == 'true',
        "fan_mode": "simple",
        "base_fan_speed_percent": int(request.form.get('base_fan_speed_percent')),
        "low_temp_threshold": int(request.form.get('low_temp_threshold')),
        "high_temp_fan_speed_percent": int(request.form.get('high_temp_fan_speed_percent')),
        "critical_temp_threshold": int(request.form.get('critical_temp_threshold'))
    }
    servers.append(new_server)
    save_servers_config(servers)
    return redirect(url_for('manage_servers'))

@app.route('/servers/edit/<alias>')
def edit_server_form(alias):
    servers = load_servers_config()
    server_to_edit = next((s for s in servers if s['alias'] == alias), None)
    if server_to_edit:
        server_to_edit.setdefault('fan_mode', 'simple')
        server_to_edit.setdefault('fan_curve', [])
        server_to_edit.setdefault('pid_config', {})
        server_to_edit.setdefault('target_temp', 55)
        server_to_edit.setdefault('fan_control_enabled', True)
        return render_template('edit_server.html', server=server_to_edit, active_page='servers')
    flash(f"Server '{alias}' not found.", "error")
    return redirect(url_for('manage_servers'))

@app.route('/servers/update/<alias>', methods=['POST'])
def update_server(alias):
    servers = load_servers_config()
    server_to_update = next((s for s in servers if s['alias'] == alias), None)
    if not server_to_update:
        flash(f"Server '{alias}' not found.", "error")
        return redirect(url_for('manage_servers'))
    server_to_update['idrac_ip'] = request.form.get('idrac_ip')
    server_to_update['idrac_username'] = request.form.get('idrac_username')
    new_password = request.form.get('idrac_password')
    if new_password:
        server_to_update['idrac_password'] = new_password
    server_to_update['enabled'] = request.form.get('enabled') == 'true'
    server_to_update['fan_control_enabled'] = request.form.get('fan_control_enabled') == 'true'
    server_to_update['fan_mode'] = request.form.get('fan_mode')
    server_to_update['base_fan_speed_percent'] = int(request.form.get('base_fan_speed_percent'))
    server_to_update['low_temp_threshold'] = int(request.form.get('low_temp_threshold'))
    server_to_update['high_temp_fan_speed_percent'] = int(request.form.get('high_temp_fan_speed_percent'))
    server_to_update['critical_temp_threshold'] = int(request.form.get('critical_temp_threshold'))
    server_to_update['pid_config'] = {
        "target_temp": int(request.form.get('target_temp')),
        "kp": float(request.form.get('pid_kp')),
        "ki": float(request.form.get('pid_ki')),
        "kd": float(request.form.get('pid_kd'))
    }
    fan_curve = []
    i = 0
    while True:
        temp = request.form.get(f'curve_temp_{i}')
        speed = request.form.get(f'curve_speed_{i}')
        if temp and speed:
            fan_curve.append({'temp': int(temp), 'speed': int(speed)})
            i += 1
        else:
            break
    server_to_update['fan_curve'] = sorted(fan_curve, key=lambda p: p['temp'])
    save_servers_config(servers)
    return redirect(url_for('manage_servers'))

@app.route('/servers/delete/<alias>', methods=['POST'])
def delete_server(alias):
    servers = load_servers_config()
    servers_to_keep = [s for s in servers if s['alias'] != alias]
    if len(servers_to_keep) < len(servers):
        save_servers_config(servers_to_keep)
    else:
        flash(f"Server '{alias}' not found.", "error")
    return redirect(url_for('manage_servers'))

@app.route('/api/history')
def api_history():
    with history_lock:
        return jsonify({a: list(r) for a, r in server_history.items()})

@app.route('/api/status')
def api_status():
    return jsonify(load_all_servers_status())

def run_web_server(port, status_file_path, lock):
    global STATUS_FILE, status_lock
    STATUS_FILE = status_file_path
    status_lock = lock
    threading.Thread(target=record_history, daemon=True).start()
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
