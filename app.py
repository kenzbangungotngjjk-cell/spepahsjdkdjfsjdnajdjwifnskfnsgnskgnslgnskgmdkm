import os
import json
import threading
import time
import shutil
import zipfile
import uuid
import tarfile
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_from_directory
from datetime import timedelta
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "i_thought_no_one_care_about_secret_key_but_why_are_you_here_nigga?")
app.permanent_session_lifetime = timedelta(days=30)
USERS_FILE = "users.json"
SERVERS_FILE = "servers.json"
SETTINGS_FILE = "settings.json"
BASE_SERVER_DIR = "servers"

# --- Global Resource Limits ---
# These can be overridden via environment variables. Set to 0 or "unlimited" to disable enforcement.
def _parse_limit(env_name, default):
    v = os.environ.get(env_name)
    if v is None:
        return default
    s = str(v).strip().lower()
    if s in ("0", "0mb", "unlimited", "unli", "none", "-1", "inf", "infinite"):
        return 0
    try:
        # allow floats like "6.5" or values with units accidentally provided
        if s.endswith('mb'):
            return int(float(s[:-2].strip()))
        return int(float(s))
    except Exception:
        return default

MAX_RAM_MB = _parse_limit("MAX_RAM_MB", 6 * 1024)
MAX_CPU_PERCENT = _parse_limit("MAX_CPU_PERCENT", 400)
MAX_DISK_MB = _parse_limit("MAX_DISK_MB", 50 * 1024)

# ----------------- Utilities -----------------
def load_settings():
    """Loads settings from settings.json, creates with default if not found."""
    if not os.path.exists(SETTINGS_FILE):
        default_settings = {"registration_enabled": True, "splitter_enabled": True}
        with open(SETTINGS_FILE, "w") as f:
            json.dump(default_settings, f, indent=4)
        return default_settings
    with open(SETTINGS_FILE, "r") as f:
        try:
            settings = json.load(f)
            # Ensure new settings are present without overwriting the file
            if "splitter_enabled" not in settings:
                settings["splitter_enabled"] = True
            return settings
        except json.JSONDecodeError:
            return {"registration_enabled": True, "splitter_enabled": True} # Return default if file is corrupt

def save_settings(settings):
    """Saves the settings dictionary to settings.json."""
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=4)

def load_users():
    if not os.path.exists(USERS_FILE):
        with open(USERS_FILE, "w") as f:
            json.dump([], f)
    with open(USERS_FILE, "r") as f:
        try:
            return json.load(f)
        except:
            return []

def save_users(users):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=4)

def load_servers():
    if not os.path.exists(SERVERS_FILE):
        with open(SERVERS_FILE, "w") as f:
            json.dump([], f)
    with open(SERVERS_FILE, "r") as f:
        try:
            return json.load(f)
        except:
            return []

def save_servers(servers):
    with open(SERVERS_FILE, "w") as f:
        json.dump(servers, f, indent=4)

def ensure_admin():
    users = load_users()
    if not any(u["username"]=="JoshPogi" for u in users):
        users.append({"username":"JoshPogi","password":"Josh15roxas@"})
        save_users(users)

def server_folder(owner, server_name):
    path = os.path.join(BASE_SERVER_DIR, owner, server_name)
    os.makedirs(path, exist_ok=True)
    return path

def get_folder_size(path):
    """Return folder size in MB with floating point precision."""
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            if os.path.exists(fp):
                total += os.path.getsize(fp)
    return total / (1024 * 1024)

def format_ram(mb):
    """Formats RAM in MB to a human-readable string (MB or GB)."""
    if mb is None: return "0 MB"
    mb = float(mb)
    if mb < 1024:
        return f"{mb:.0f} MB"
    else:
        return f"{mb / 1024:.2f} GB"

def format_size(mb):
    """Formats size in MB to a human-readable string (KB, MB, GB)."""
    if mb is None: return "0 KB"
    mb = float(mb)
    if mb < 1:
        return f"{mb * 1024:.2f} KB"
    elif mb < 1024:
        return f"{mb:.2f} MB"
    else:
        return f"{mb / 1024:.2f} GB"

# --- NEW UTILITY FUNCTIONS FOR METRICS ---
def format_uptime(seconds):
    """Formats seconds into a human-readable uptime string."""
    if seconds is None:
        return "N/A"
    seconds = int(seconds)
    days = seconds // (24 * 3600)
    hours = (seconds % (24 * 3600)) // 3600
    minutes = (seconds % 3600) // 60
    return f"{days}d {hours}h {minutes}m"

def format_bytes(bytes_val):
    """Formats bytes into a human-readable string (B, KB, MB, GB)."""
    if bytes_val is None:
        return "0 B"
    bytes_val = float(bytes_val)
    if bytes_val < 1024:
        return f"{bytes_val:.2f} B"
    elif bytes_val < 1024**2:
        return f"{bytes_val / 1024:.2f} KB"
    elif bytes_val < 1024**3:
        return f"{bytes_val / 1024**2:.2f} MB"
    else:
        return f"{bytes_val / 1024**3:.2f} GB"

# ============ NOTE: SERVERLESS ENVIRONMENT ============
# Vercel serverless functions do NOT support:
# - Long-running subprocesses (all processes terminated after 10 seconds)
# - WebSocket connections (no persistent connections)
# - Process monitoring with psutil
# The following functions are stubs for Vercel compatibility
# =====================================================

server_processes = {}
console_logs = {}

def run_server(owner, server_name):
    """Stub: Vercel serverless cannot run long-running processes"""
    key = f"{owner}_{server_name}"
    console_logs.setdefault(key, []).append("⚠️ Server start requested, but Vercel serverless cannot run persistent processes. Use a VPS or dedicated server instead.")
    server_processes[key] = {"status": "stub", "uptime": 0}

def stop_server(owner, server_name):
    """Stub: No actual process to stop"""
    key = f"{owner}_{server_name}"
    console_logs.setdefault(key, []).append("Server stop requested.")
    server_processes.pop(key, None)

# ----------------- Routes & API Endpoints -----------------
@app.route("/")
def home():
    return redirect(url_for("login"))

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        username = request.form["username"]
        password = request.form["password"]
        users = load_users()
        for u in users:
            if u["username"]==username and u["password"]==password:
                session["username"] = username
                session.permanent = True
                return redirect(url_for("dashboard") if username != "JoshPogi" else url_for("admin"))
        flash("Invalid credentials!", "danger")
    
    settings = load_settings()
    registration_enabled = settings.get("registration_enabled", True)
    return render_template("login.html", registration_enabled=registration_enabled)

@app.route("/register", methods=["POST"])
def register():
    settings = load_settings()
    if not settings.get("registration_enabled", True):
        flash("Registration is currently disabled by the administrator.", "danger")
        return redirect(url_for("login"))

    username = request.form["username"].strip()
    password = request.form["password"]
    confirm_password = request.form["confirm_password"]
    users = load_users()

    if any(u["username"] == username for u in users):
        flash("Username already exists.", "danger")
    elif password != confirm_password:
        flash("Passwords do not match.", "warning")
    elif len(password) < 6:
        flash("Password must be at least 6 characters long.", "warning")
    else:
        users.append({"username": username, "password": password})
        save_users(users)
        flash("Account created successfully! You can now log in.", "success")
    
    return redirect(url_for("login"))

@app.route("/logout")
def logout():
    session.pop("username", None)
    flash("Logged out successfully.", "info")
    return redirect(url_for("login"))

@app.route("/dashboard")
def dashboard():
    if "username" not in session: return redirect(url_for("login"))
    all_servers = load_servers()
    user_servers = [s for s in all_servers if s["owner"] == session["username"]]
    for server in user_servers:
        # On Vercel serverless, servers are always offline (no subprocess support)
        server['status'] = 'Offline'
    return render_template("dashboard.html", username=session["username"], servers=user_servers, page="dashboard")

@app.route("/account", methods=["GET", "POST"])
def account():
    if "username" not in session: return redirect(url_for("login"))
    username = session["username"]
    if request.method == "POST":
        current_password = request.form.get("current_password")
        new_password = request.form.get("new_password")
        confirm_password = request.form.get("confirm_password")
        users = load_users()
        user_to_update = next((u for u in users if u["username"] == username), None)
        if not user_to_update or user_to_update["password"] != current_password:
            flash("Your current password was incorrect.", "danger")
        elif not new_password or new_password != confirm_password:
            flash("New passwords do not match.", "warning")
        elif len(new_password) < 6:
            flash("New password must be at least 6 characters long.", "warning")
        else:
            user_to_update["password"] = new_password
            save_users(users)
            flash("Your password has been updated successfully!", "success")
        return redirect(url_for("account"))
    return render_template("account.html", username=username, page="account")

@app.route("/settings")
def settings():
    if "username" not in session: return redirect(url_for("login"))
    username = session["username"]
    user_servers = [s for s in load_servers() if s["owner"] == username]
    stats = {
        "servers": len(user_servers),
        "ram": sum(int(s.get("ram", 0)) for s in user_servers),
        "cpu": sum(int(s.get("cpu", 0)) for s in user_servers),
        "disk": sum(int(s.get("disk", 0)) for s in user_servers),
    }
    return render_template("settings.html", username=username, stats=stats, page="settings")

@app.route("/console/<owner>/<server_name>", methods=["GET", "POST"])
def console(owner, server_name):
    if "username" not in session: return redirect(url_for("login"))
    
    settings = load_settings() # Load settings
    splitter_enabled = settings.get("splitter_enabled", True)

    servers = load_servers()
    server = next((s for s in servers if s["owner"]==owner and s["name"]==server_name), None)
    if not server:
        flash("Server not found.", "danger")
        return redirect(url_for("dashboard"))

    key = f"{owner}_{server_name}"
    if key not in console_logs: console_logs[key] = []
    
    if session["username"] != owner and session["username"] != "JoshPogi":
        flash("No permission!", "danger")
        return redirect(url_for("dashboard"))

    if request.method=="POST":
        if "start" in request.form:
            console_logs[key].append("⚠️ Note: Server start not supported on Vercel serverless. Deploy to a VPS for actual server running.")
            run_server(owner, server_name)
        elif "restart" in request.form:
            stop_server(owner, server_name)
            time.sleep(0.5)
            console_logs[key].append("⚠️ Note: Restart not supported on Vercel serverless.")
            run_server(owner, server_name)
        elif "stop" in request.form: 
            stop_server(owner, server_name)
            console_logs[key].clear()
        
        elif "split_server" in request.form:
            if not splitter_enabled:
                flash("Server splitting is currently disabled by the administrator.", "danger")
                return redirect(url_for("console", owner=owner, server_name=server_name))

            new_name = request.form.get("new_server_name", "").strip()
            
            try:
                new_ram = int(request.form.get("new_server_ram"))
                new_cpu = int(request.form.get("new_server_cpu"))
                new_disk = int(request.form.get("new_server_disk"))
            except (ValueError, TypeError):
                flash("Invalid resource values. Please enter numbers.", "danger")
                return redirect(url_for("console", owner=owner, server_name=server_name))

            original_ram = int(server["ram"])
            original_cpu = int(server["cpu"])
            original_disk = int(server["disk"])

            # Validation
            if not new_name:
                flash("New server name cannot be empty.", "danger")
            elif any(s["owner"] == owner and s["name"] == new_name for s in servers):
                flash(f"A server with the name '{new_name}' already exists.", "danger")
            elif not (0 < new_ram < original_ram):
                flash(f"New RAM must be between 1 and {original_ram - 1} MB.", "danger")
            elif not (0 < new_disk < original_disk):
                flash(f"New Disk must be between 1 and {original_disk - 1} MB.", "danger")
            elif not (0 < new_cpu < original_cpu): # Check CPU minimum (1%) and upper limit
                flash(f"New CPU must be between 1 and {original_cpu - 1}%.", "danger")
            else:
                # If all checks pass: Update original server and create new server
                server["ram"] = str(original_ram - new_ram)
                server["cpu"] = str(original_cpu - new_cpu) 
                server["disk"] = str(original_disk - new_disk)

                # Create new server
                new_server = {
                    "owner": owner, "name": new_name,
                    "ram": str(new_ram), "cpu": str(new_cpu), "disk": str(new_disk),
                    "parent_server": server_name
                }
                servers.append(new_server)
                save_servers(servers)
                server_folder(owner, new_name) # Create the new server's directory
                flash(f"Server '{server_name}' was split successfully. Created new server '{new_name}'.", "success")
                return redirect(url_for("dashboard"))
            
            # Redirect back to console on validation failure
            return redirect(url_for("console", owner=owner, server_name=server_name))

        return redirect(url_for("console", owner=owner, server_name=server_name, path=request.args.get('path','')))
    
    return render_template("console.html", owner=owner, server_name=server_name, server=server, console_lines=console_logs.get(key, []), path=request.args.get('path',''), page="console", splitter_enabled=splitter_enabled)

# WebSocket routes not supported on Vercel serverless - use REST API fallback

# --- REST API FALLBACK FOR CONSOLE LOGS (Polling instead of WebSocket) ---
@app.route("/api/console_logs/<owner>/<server_name>")
def api_console_logs(owner, server_name):
    """REST endpoint that returns console logs instead of WebSocket"""
    key = f"{owner}_{server_name}"
    logs = console_logs.get(key, [])
    return jsonify({"logs": logs[-100:]})  # Return last 100 lines

# --- SERVER STATS ENDPOINT (Stub for Vercel) ---
@app.route("/server_stats/<owner>/<server_name>")
def server_stats(owner, server_name):
    """Returns stub stats - actual subprocess monitoring not supported on Vercel"""
    servers = load_servers()
    server = next((s for s in servers if s["owner"]==owner and s["name"]==server_name), None)
    if not server: 
        return jsonify({"error": "Server not found"}), 404
    
    key = f"{owner}_{server_name}"
    process = server_processes.get(key)
    
    return jsonify({
        "status": "Offline",  # Always offline on Vercel (no subprocess support)
        "ram_used": "0 MB", 
        "ram_limit": "Unlimited",
        "cpu": 0.0, 
        "cpu_limit": "Unlimited",
        "disk_used": "0 MB", 
        "disk_limit": "Unlimited",
        "uptime": "N/A",
        "avg_cpu_load": "0.00",
        "network_in": "0 B/s",
        "network_out": "0 B/s",
        "disk_read": "0 B/s",
        "disk_write": "0 B/s",
        "note": "Server monitoring disabled on Vercel serverless. Use a VPS for actual server deployment."
    })

@app.route('/api/list_files/<owner>/<server_name>')
def api_list_files(owner, server_name):
    path = request.args.get('path', '')
    server_dir = server_folder(owner, server_name)
    current_folder = os.path.join(server_dir, path)
    if not os.path.abspath(current_folder).startswith(os.path.abspath(server_dir)):
        return jsonify({"error": "Access Denied"}), 403
    try:
        items = os.listdir(current_folder)
        dirs = sorted([d for d in items if os.path.isdir(os.path.join(current_folder, d))])
        files = sorted([f for f in items if os.path.isfile(os.path.join(current_folder, f))])
        return jsonify({"dirs": dirs, "files": files, "current_path": path})
    except FileNotFoundError:
        return jsonify({"error": "Directory not found"}), 404

@app.route('/console/<owner>/<server_name>/download')
def download_file(owner, server_name):
    path = request.args.get('path', '')
    filename = request.args.get('filename', '')
    if ".." in path or ".." in filename: return "Invalid path", 400
    server_dir = server_folder(owner, server_name)
    full_path = os.path.join(server_dir, path)
    return send_from_directory(full_path, filename, as_attachment=True)

@app.route('/console/<owner>/<server_name>/delete', methods=['POST'])
def delete_item(owner, server_name):
    path = request.form.get('path', '')
    item_name = request.form.get('item_name', '')
    if ".." in path or ".." in item_name: return jsonify({"error": "Invalid path"}), 400
    item_path = os.path.join(server_folder(owner, server_name), path, item_name)
    try:
        if os.path.isfile(item_path): os.remove(item_path)
        elif os.path.isdir(item_path): shutil.rmtree(item_path)
        return jsonify({"success": True})
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route('/console/<owner>/<server_name>/rename', methods=['POST'])
def rename_item(owner, server_name):
    path = request.form.get('path', '')
    old_name = request.form.get('old_name', '')
    new_name = request.form.get('new_name', '')
    if ".." in path or ".." in old_name or ".." in new_name or not new_name: return jsonify({"error": "Invalid name"}), 400
    base_dir = server_folder(owner, server_name)
    old_path = os.path.join(base_dir, path, old_name)
    new_path = os.path.join(base_dir, path, new_name)
    try:
        os.rename(old_path, new_path)
        return jsonify({"success": True})
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route('/console/<owner>/<server_name>/move', methods=['POST'])
def move_item(owner, server_name):
    source_path = request.form.get('source_path', '')
    new_path = request.form.get('new_path', '')
    item_name = request.form.get('item_name', '')
    
    # Security check to prevent path traversal
    if ".." in source_path or ".." in new_path or ".." in item_name:
        return jsonify({"error": "Invalid path"}), 400
        
    base_dir = server_folder(owner, server_name)
    
    old_item_path = os.path.join(base_dir, source_path, item_name)
    new_item_path = os.path.join(base_dir, new_path, item_name)
    
    # Ensure paths are within the server's directory
    if not os.path.abspath(old_item_path).startswith(os.path.abspath(base_dir)) or \
       not os.path.abspath(new_item_path).startswith(os.path.abspath(base_dir)):
        return jsonify({"error": "Access denied"}), 403

    # Check if the source item exists before attempting to move
    if not os.path.exists(old_item_path):
        return jsonify({"error": f"Source item not found at path: {old_item_path}"}), 404

    try:
        shutil.move(old_item_path, new_item_path)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/console/<owner>/<server_name>/copy', methods=['POST'])
def copy_item(owner, server_name):
    source_path = request.form.get('source_path', '')
    new_path = request.form.get('new_path', '')
    item_name = request.form.get('item_name', '')

    if ".." in source_path or ".." in new_path or ".." in item_name:
        return jsonify({"error": "Invalid path"}), 400
    
    base_dir = server_folder(owner, server_name)
    
    old_item_path = os.path.join(base_dir, source_path, item_name)
    new_item_path = os.path.join(base_dir, new_path, item_name)
    
    if not os.path.abspath(old_item_path).startswith(os.path.abspath(base_dir)) or \
       not os.path.abspath(new_item_path).startswith(os.path.abspath(base_dir)):
        return jsonify({"error": "Access denied"}), 403

    if not os.path.exists(old_item_path):
        return jsonify({"error": "Source item not found at path: {old_item_path}"}), 404
        
    if os.path.exists(new_item_path):
        return jsonify({"error": f"Destination already exists: {new_item_path}"}), 409

    try:
        if os.path.isfile(old_item_path):
            shutil.copy2(old_item_path, new_item_path)
        elif os.path.isdir(old_item_path):
            shutil.copytree(old_item_path, new_item_path)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/get_file_stats/<owner>/<server_name>')
def get_file_stats(owner, server_name):
    path = request.args.get('path', '')
    item_name = request.args.get('item_name', '')
    
    if ".." in path or ".." in item_name:
        return jsonify({"error": "Invalid path"}), 400
    
    base_dir = server_folder(owner, server_name)
    item_path = os.path.join(base_dir, path, item_name)
    
    if not os.path.exists(item_path):
        return jsonify({"error": "Item not found"}), 404
    
    if not os.path.abspath(item_path).startswith(os.path.abspath(base_dir)):
        return jsonify({"error": "Access denied"}), 403

    try:
        stats = os.stat(item_path)
        
        if os.path.isdir(item_path):
            size = get_folder_size(item_path)
            size_formatted = format_size(size)
        else:
            size_bytes = stats.st_size
            size_formatted = format_bytes(size_bytes)
        
        return jsonify({
            "success": True,
            "path": os.path.join(path, item_name),
            "size": size_formatted,
            "modified": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(stats.st_mtime)),
            "created": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(stats.st_ctime))
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/console/<owner>/<server_name>/archive', methods=['POST'])
def archive_files(owner, server_name):
    path = request.form.get('path', '')
    files_to_archive = request.form.getlist('selected_files')
    if ".." in path or any(".." in f for f in files_to_archive): 
        return jsonify({"error": "Invalid path"}), 400
    base_dir = server_folder(owner, server_name)
    zip_filename = f"archive-{uuid.uuid4().hex[:8]}.zip"
    zip_path = os.path.join(base_dir, path, zip_filename)
    try:
        with zipfile.ZipFile(zip_path, 'w') as zipf:
            for item_name in files_to_archive:
                item_path = os.path.join(base_dir, path, item_name)
                if os.path.exists(item_path): 
                    zipf.write(item_path, arcname=item_name)
        # Instead of sending the file, return a JSON response with the filename
        return jsonify({"success": True, "filename": zip_filename, "path": path})
    except Exception as e: 
        # Return a JSON error message on failure
        return jsonify({"error": f"Failed to create archive: {str(e)}"}), 500
    
@app.route('/console/<owner>/<server_name>/unarchive', methods=['POST'])
def unarchive_item(owner, server_name):
    path = request.form.get('path', '')
    filename = request.form.get('filename', '')
    if ".." in path or ".." in filename: return jsonify({"error": "Invalid path"}), 400
    base_dir = server_folder(owner, server_name)
    file_path = os.path.join(base_dir, path, filename)
    extract_path = os.path.join(base_dir, path)
    try:
        if filename.endswith('.zip'):
            with zipfile.ZipFile(file_path, 'r') as zip_ref:
                zip_ref.extractall(extract_path)
        elif filename.endswith(('.tar', '.tar.gz', '.tgz')):
            with tarfile.open(file_path, 'r:*') as tar_ref:
                tar_ref.extractall(path=extract_path)
        else:
            return jsonify({"error": "Unsupported archive format. Only .zip and .tar are supported."}), 400
    except Exception as e: return jsonify({"error": str(e)}), 500
    return jsonify({"success": True, "message": f"Successfully unarchived {filename}"})

@app.route('/console/<owner>/<server_name>/install', methods=['POST'])
def install_requirements(owner, server_name):
    if "username" not in session: return jsonify({"error": "Unauthorized"}), 401
    if session["username"] != owner and session["username"] != "JoshPogi":
        return jsonify({"error": "Forbidden"}), 403
    key = f"{owner}_{server_name}"
    server_root = server_folder(owner, server_name)
    requirements_path = os.path.join(server_root, 'requirements.txt')
    if not os.path.exists(requirements_path):
        return jsonify({"error": "requirements.txt not found"}), 404
    
    # Stub for Vercel - pip installation not supported on serverless
    console_logs.get(key, []).append("⚠️ Package installation not supported on Vercel serverless.")
    console_logs.get(key, []).append("Deploy to a VPS with persistent storage for package management.")
    
    return jsonify({"success": True, "message": "Installation feature disabled on Vercel serverless"})

@app.route('/console/<owner>/<server_name>/delete_multiple', methods=['POST'])
def delete_multiple(owner, server_name):
    path = request.form.get('path', '')
    files_to_delete = request.form.getlist('selected_files')
    if ".." in path or any(".." in f for f in files_to_delete): return jsonify({"error": "Invalid path"}), 400
    base_dir = server_folder(owner, server_name)
    for item_name in files_to_delete:
        item_path = os.path.join(base_dir, path, item_name)
        try:
            if os.path.isfile(item_path): os.remove(item_path)
            elif os.path.isdir(item_path): shutil.rmtree(item_path)
        except Exception: pass
    return jsonify({"success": True})

@app.route('/console/<owner>/<server_name>/create_file', methods=['POST'])
def create_file(owner, server_name):
    path = request.form.get('path', '')
    filename = request.form.get('file_name', '').strip()
    if not filename or ".." in path or ".." in filename or "/" in filename:
        return jsonify({"error": "Invalid file name"}), 400
    base_dir = server_folder(owner, server_name)
    file_path = os.path.join(base_dir, path, filename)
    if os.path.exists(file_path):
        return jsonify({"error": "File already exists"}), 400
    try:
        open(file_path, 'a').close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/console/<owner>/<server_name>/create_dir', methods=['POST'])
def create_dir(owner, server_name):
    path = request.form.get('path', '')
    dirname = request.form.get('dir_name', '').strip()
    if not dirname or ".." in path or ".." in dirname or "/" in dirname:
        return jsonify({"error": "Invalid directory name"}), 400
    base_dir = server_folder(owner, server_name)
    dir_path = os.path.join(base_dir, path, dirname)
    if os.path.exists(dir_path):
        return jsonify({"error": "Directory already exists"}), 400
    try:
        os.makedirs(dir_path)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/console/<owner>/<server_name>/upload', methods=['POST'])
def upload_file_route(owner, server_name):
    # This route has been improved for better security and error handling.
    
    # 1. Check for valid user session and permissions
    if "username" not in session or (session["username"] != owner and session["username"] != "JoshPogi"):
        return jsonify({"error": "Unauthorized"}), 401

    # 2. Get the path and validate it
    path = request.form.get('path', '')
    if ".." in path:
        return jsonify({"error": "Invalid path"}), 400

    # 3. Check for file in the request
    if 'files' not in request.files:
        return jsonify({"error": "No file part"}), 400
    
    files = request.files.getlist('files')
    if not files or files[0].filename == '':
        return jsonify({"error": "No selected file"}), 400

    # 4. Sanitize the filename and define the upload directory
    base_dir = server_folder(owner, server_name)
    upload_path = os.path.join(base_dir, path)
    
    # 5. Ensure the upload directory exists and is within the server's directory
    if not os.path.abspath(upload_path).startswith(os.path.abspath(base_dir)):
        return jsonify({"error": "Access Denied"}), 403
    os.makedirs(upload_path, exist_ok=True)
    
    # 6. Save the file and handle potential exceptions
    try:
        uploaded_filenames = []
        for file in files:
            filename = secure_filename(file.filename)
            file.save(os.path.join(upload_path, filename))
            uploaded_filenames.append(filename)
            
        message = f"Successfully uploaded {len(uploaded_filenames)} file(s)."
        if len(uploaded_filenames) == 1:
            message = f"File '{uploaded_filenames[0]}' uploaded successfully."
            
        return jsonify({"success": True, "message": message})
    except Exception as e:
        return jsonify({"error": f"File upload failed: {str(e)}"}), 500

@app.route("/api/get_file_content/<owner>/<server_name>")
def get_file_content(owner, server_name):
    path = request.args.get('path', '')
    filename = request.args.get('filename', '')
    if ".." in path or ".." in filename: return jsonify({"error": "Invalid path"}), 400
    base_dir = server_folder(owner, server_name)
    file_path = os.path.join(base_dir, path, filename)
    if not os.path.isfile(file_path) or not os.path.abspath(file_path).startswith(os.path.abspath(base_dir)):
        return jsonify({"error": "File not found or access denied"}), 404
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        return jsonify({"success": True, "content": content})
    except Exception as e: return jsonify({"error": f"Error reading file: {str(e)}"}), 500

@app.route("/api/save_file_content/<owner>/<server_name>", methods=['POST'])
def save_file_content(owner, server_name):
    path = request.form.get('path', '')
    filename = request.form.get('filename', '')
    content = request.form.get('content', '')
    if ".." in path or ".." in filename: return jsonify({"error": "Invalid path"}), 400
    base_dir = server_folder(owner, server_name)
    file_path = os.path.join(base_dir, path, filename)
    if not os.path.abspath(file_path).startswith(os.path.abspath(base_dir)):
        return jsonify({"error": "Access denied"}), 403
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return jsonify({"success": True, "message": "File saved successfully"})
    except Exception as e: return jsonify({"error": f"Error saving file: {str(e)}"}), 500

@app.route('/api/list_split_servers/<owner>/<server_name>')
def list_split_servers(owner, server_name):
    if "username" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    
    # Check if user has permission to view this server's data
    if session["username"] != owner and session["username"] != "JoshPogi":
        return jsonify({"error": "Forbidden"}), 403

    all_servers = load_servers()
    # Filter for servers that have the current server as their parent
    split_servers = [s for s in all_servers if s.get('parent_server') == server_name]
    
    return jsonify({"split_servers": split_servers})

@app.route('/api/update_split_server/<owner>/<server_name>', methods=['POST'])
def update_split_server(owner, server_name):
    if "username" not in session or session["username"] != owner:
        return jsonify({"error": "Unauthorized or Forbidden"}), 403
    
    servers = load_servers()
    target_server = next((s for s in servers if s["owner"] == owner and s["name"] == server_name), None)

    if not target_server or "parent_server" not in target_server:
        return jsonify({"error": "Server not found or is a main server."}), 404

    parent_server_name = target_server["parent_server"]
    parent_server = next((s for s in servers if s["owner"] == owner and s["name"] == parent_server_name), None)
    
    if not parent_server:
         return jsonify({"error": f"Parent server '{parent_server_name}' not found. Cannot return resources."}), 404

    try:
        new_ram = int(request.form.get('new_ram'))
        new_cpu = int(request.form.get('new_cpu'))
        new_disk = int(request.form.get('new_disk'))
        
        old_ram = int(target_server.get("ram", 0))
        old_cpu = int(target_server.get("cpu", 0))
        old_disk = int(target_server.get("disk", 0))

        # Calculate difference
        ram_diff = new_ram - old_ram
        cpu_diff = new_cpu - old_cpu
        disk_diff = new_disk - old_disk

        if new_ram < 1 or new_cpu < 1 or new_disk < 1:
            return jsonify({"error": "Minimum resource allocation is 1 for RAM, CPU, and Disk."}), 400

        # Check if parent has enough resources
        if int(parent_server["ram"]) - ram_diff < 0 or int(parent_server["cpu"]) - cpu_diff < 0 or int(parent_server["disk"]) - disk_diff < 0:
             return jsonify({"error": "Insufficient resources in parent server."}), 400

        # Update parent and target server
        parent_server["ram"] = str(int(parent_server["ram"]) + old_ram - new_ram)
        parent_server["cpu"] = str(int(parent_server["cpu"]) + old_cpu - new_cpu)
        parent_server["disk"] = str(int(parent_server["disk"]) + old_disk - new_disk)

        target_server["ram"] = str(new_ram)
        target_server["cpu"] = str(new_cpu)
        target_server["disk"] = str(new_disk)

        save_servers(servers)
        return jsonify({"success": True, "message": f"Server '{server_name}' updated successfully."})
    except (ValueError, TypeError) as e:
        return jsonify({"error": f"Invalid resource values: {str(e)}"}), 400

def get_all_descendants(parent_name, all_servers):
    """Recursively finds all descendant servers for a given parent."""
    descendants = []
    children = [s for s in all_servers if s.get('parent_server') == parent_name]
    for child in children:
        descendants.append(child)
        descendants.extend(get_all_descendants(child['name'], all_servers))
    return descendants


@app.route('/api/delete_split_server/<owner>/<server_name>', methods=['POST'])
def delete_split_server(owner, server_name):
    if "username" not in session or session["username"] != owner:
        return jsonify({"error": "Unauthorized or Forbidden"}), 403

    servers = load_servers()
    target_server = next((s for s in servers if s["owner"] == owner and s["name"] == server_name), None)

    if not target_server:
        return jsonify({"error": "Server not found."}), 404
    
    if "parent_server" not in target_server:
        return jsonify({"error": "This is a main server and cannot be deleted from this interface."}), 400
    
    parent_server_name = target_server["parent_server"]
    parent_server = next((s for s in servers if s["owner"] == owner and s["name"] == parent_server_name), None)

    if not parent_server:
        return jsonify({"error": f"Parent server '{parent_server_name}' not found. Cannot return resources."}), 404

    # Get all descendants of the target server
    descendants = get_all_descendants(server_name, servers)
    
    # Calculate total resources to return to the parent
    total_ram_to_return = int(target_server.get("ram", 0))
    total_cpu_to_return = int(target_server.get("cpu", 0))
    total_disk_to_return = int(target_server.get("disk", 0))

    for descendant in descendants:
        total_ram_to_return += int(descendant.get("ram", 0))
        total_cpu_to_return += int(descendant.get("cpu", 0))
        total_disk_to_return += int(descendant.get("disk", 0))
        
    # Return resources to the parent
    parent_server["ram"] = str(int(parent_server["ram"]) + total_ram_to_return)
    parent_server["cpu"] = str(int(parent_server["cpu"]) + total_cpu_to_return)
    parent_server["disk"] = str(int(parent_server["disk"]) + total_disk_to_return)

    # Delete all descendants and the target server
    servers_to_keep = [s for s in servers if s["name"] != server_name and s not in descendants]
    save_servers(servers_to_keep)
    
    # Delete directories for all of them
    servers_to_delete = [target_server] + descendants
    for s_to_del in servers_to_delete:
        server_path = os.path.join(BASE_SERVER_DIR, s_to_del["owner"], s_to_del["name"])
        if os.path.exists(server_path):
            shutil.rmtree(server_path)

    return jsonify({"success": True, "message": f"Server '{server_name}' and all its children have been deleted. Resources returned to '{parent_server_name}'."})
# ----------------- Admin -----------------
@app.route("/admin", methods=["GET","POST"])
def admin():
    if "username" not in session or session["username"]!="JoshPogi":
        return redirect(url_for("login"))
    
    servers = load_servers()
    users = load_users()
    total_ram = sum(int(s.get("ram", 0)) for s in servers)
    total_cpu = sum(int(s.get("cpu", 0)) for s in servers)
    total_disk = sum(int(s.get("disk", 0)) for s in servers)

    if request.method=="POST":
        if "create_user" in request.form:
            new_user = request.form["new_username"].strip()
            new_pass = request.form["new_password"].strip()
            if new_user and new_pass and not any(u["username"]==new_user for u in users):
                users.append({"username":new_user,"password":new_pass})
                save_users(users)
                flash(f"User '{new_user}' created successfully.", "success")
        
        elif "delete_user_from_modal" in request.form:
            target = request.form.get("old_username")
            if target == 'JoshPogi':
                flash("The primary admin account cannot be deleted.", "danger")
            else:
                users = [u for u in users if u["username"]!=target]
                save_users(users)
                servers_to_keep = [s for s in servers if s["owner"]!=target]
                save_servers(servers_to_keep)
                user_server_dir = os.path.join(BASE_SERVER_DIR, target)
                if os.path.exists(user_server_dir):
                    shutil.rmtree(user_server_dir)
                flash(f"User '{target}' and their servers deleted.", "success")
        
        elif "create_server" in request.form:
            owner = request.form["server_owner"].strip()
            
            try:
                new_ram = int(request.form["server_ram"].strip())
                new_cpu = int(request.form["server_cpu"].strip())
                new_disk = int(request.form["server_disk"].strip())
            except (ValueError, TypeError):
                flash("Invalid resource values. Please enter numbers.", "danger")
                return redirect(url_for('admin'))
            
            # --- UPDATED CPU MINIMUM LOGIC (Revert to 1% min) ---
            if new_ram < 1 or new_cpu < 1 or new_disk < 1:
                flash("Minimum resource allocation is 1 for RAM, CPU, and Disk.", "danger")
            elif not any(u["username"] == owner for u in users):
                flash(f"Cannot create server: Owner username '{owner}' does not exist.", "danger")
            elif MAX_RAM_MB > 0 and total_ram + new_ram > MAX_RAM_MB:
                flash(f"Cannot create server: Exceeds total RAM limit of {format_ram(MAX_RAM_MB)}.", "danger")
            elif MAX_CPU_PERCENT > 0 and total_cpu + new_cpu > MAX_CPU_PERCENT:
                flash(f"Cannot create server: Exceeds total CPU limit of {MAX_CPU_PERCENT}%.", "danger")
            elif MAX_DISK_MB > 0 and total_disk + new_disk > MAX_DISK_MB:
                flash(f"Cannot create server: Exceeds total Disk limit of {format_size(MAX_DISK_MB)}.", "danger")
            # --- END UPDATED CPU MINIMUM LOGIC ---
            else:
                name = request.form["server_name"].strip()
                servers.append({"owner":owner, "name":name, "ram":str(new_ram), "cpu":str(new_cpu), "disk":str(new_disk)})
                save_servers(servers)
                flash(f"Server '{name}' for '{owner}' created.", "success")

        elif "delete_server_from_modal" in request.form:
            old_name = request.form.get("old_server_name")
            old_owner = request.form.get("old_owner")
            server_to_delete = next((s for s in servers if s["name"] == old_name and s["owner"] == old_owner), None)
            if server_to_delete:
                server_path = os.path.join(BASE_SERVER_DIR, old_owner, old_name)
                if os.path.exists(server_path):
                    shutil.rmtree(server_path)
                servers = [s for s in servers if not (s["name"] == old_name and s["owner"] == old_owner)]
                save_servers(servers)
                flash(f"Server '{old_name}' has been deleted.", "success")
            else:
                flash(f"Could not find server '{old_name}' to delete.", "danger")
        
        elif "edit_user" in request.form:
            old_username = request.form["old_username"]
            if old_username == 'JoshPogi':
                flash("The primary admin account cannot be modified.", "danger")
            else:
                new_username = request.form["new_username"].strip()
                new_password = request.form["new_password"].strip()
                user_to_edit = next((u for u in users if u["username"] == old_username), None)
                if not user_to_edit:
                    flash(f"User '{old_username}' not found.", "danger")
                else:
                    username_changed = new_username and new_username != old_username
                    if username_changed and any(u["username"] == new_username for u in users):
                        flash(f"Username '{new_username}' is already taken.", "warning")
                    else:
                        if username_changed:
                            user_to_edit["username"] = new_username
                        if new_password:
                            user_to_edit["password"] = new_password
                        save_users(users)
                        if username_changed:
                            for server in servers:
                                if server["owner"] == old_username:
                                    server["owner"] = new_username
                            save_servers(servers)
                            old_user_dir = os.path.join(BASE_SERVER_DIR, old_username)
                            new_user_dir = os.path.join(BASE_SERVER_DIR, new_username)
                            if os.path.exists(old_user_dir):
                                shutil.move(old_user_dir, new_user_dir)
                        flash("User updated successfully!", "success")

        elif "edit_server" in request.form:
            old_name = request.form.get("old_server_name")
            old_owner = request.form.get("old_owner")
            
            new_name = request.form.get("server_name")
            new_owner = request.form.get("server_owner")
            
            try:
                new_ram = int(request.form.get("server_ram"))
                new_cpu = int(request.form.get("server_cpu"))
                new_disk = int(request.form.get("server_disk"))
            except (ValueError, TypeError):
                flash("Invalid resource values. Please enter numbers.", "danger")
                return redirect(url_for('admin'))

            server_to_edit = next((s for s in servers if s["name"] == old_name and s["owner"] == old_owner), None)
            
            if server_to_edit:
                # Revert CPU minimum check
                if new_ram < 1 or new_cpu < 1 or new_disk < 1:
                    flash("Minimum resource allocation is 1 for RAM, CPU, and Disk.", "danger")
                else:
                    ram_diff = new_ram - int(server_to_edit.get("ram", 0))
                    cpu_diff = new_cpu - int(server_to_edit.get("cpu", 0))
                    disk_diff = new_disk - int(server_to_edit.get("disk", 0))

                    if MAX_RAM_MB > 0 and total_ram + ram_diff > MAX_RAM_MB:
                        flash(f"Cannot update server: Exceeds total RAM limit of {format_ram(MAX_RAM_MB)}.", "danger")
                    elif MAX_CPU_PERCENT > 0 and total_cpu + cpu_diff > MAX_CPU_PERCENT:
                        flash(f"Cannot update server: Exceeds total CPU limit of {MAX_CPU_PERCENT}%.", "danger")
                    elif MAX_DISK_MB > 0 and total_disk + disk_diff > MAX_DISK_MB:
                        flash(f"Cannot update server: Exceeds total Disk limit of {format_size(MAX_DISK_MB)}.", "danger")
                    else:
                        path_changed = new_name != old_name or new_owner != old_owner
                        
                        server_to_edit["name"] = new_name
                        server_to_edit["owner"] = new_owner
                        server_to_edit["ram"] = str(new_ram)
                        server_to_edit["cpu"] = str(new_cpu)
                        server_to_edit["disk"] = str(new_disk)
                    
                        save_servers(servers)

                        if path_changed:
                            old_path = os.path.join(BASE_SERVER_DIR, old_owner, old_name)
                            new_path_dir = os.path.join(BASE_SERVER_DIR, new_owner)
                            os.makedirs(new_path_dir, exist_ok=True)
                            new_path = os.path.join(new_path_dir, new_name)
                            if os.path.exists(old_path):
                                shutil.move(old_path, new_path)
                        flash(f"Server '{old_name}' updated successfully.", "success")
            else:
                flash(f"Could not find server '{old_name}' to update.", "danger")
        
        elif "update_settings" in request.form:
            settings = load_settings()
            # A checkbox sends a value if checked, and nothing if unchecked.
            settings['registration_enabled'] = 'registration_enabled' in request.form
            settings['splitter_enabled'] = 'splitter_enabled' in request.form
            save_settings(settings)
            flash("Settings updated successfully.", "success")

        return redirect(url_for('admin'))

    users = load_users()
    user_dict = {u["username"]: 0 for u in users}
    for s in servers:
        if s["owner"] in user_dict:
            user_dict[s["owner"]] += 1
    for u in users:
        u["servers"] = user_dict.get(u["username"], 0)
    
    settings = load_settings()
    admin_stats = {
        "total_ram": format_ram(total_ram),
        "max_ram": ("Unlimited" if MAX_RAM_MB == 0 else format_ram(MAX_RAM_MB)),
        "ram_percent": (total_ram / MAX_RAM_MB * 100) if MAX_RAM_MB > 0 else 0,
        "total_cpu": total_cpu,
        "max_cpu": ("Unlimited" if MAX_CPU_PERCENT == 0 else f"{MAX_CPU_PERCENT}%"),
        "cpu_percent": (total_cpu / MAX_CPU_PERCENT * 100) if MAX_CPU_PERCENT > 0 else 0,
        "total_disk": format_size(total_disk),
        "max_disk": ("Unlimited" if MAX_DISK_MB == 0 else format_size(MAX_DISK_MB)),
        "disk_percent": (total_disk / MAX_DISK_MB * 100) if MAX_DISK_MB > 0 else 0,
    }

    return render_template("admin.html", users=users, servers=servers, stats=admin_stats, registration_enabled=settings.get("registration_enabled", True), splitter_enabled=settings.get("splitter_enabled", True))

# ----------------- Main -----------------
if __name__=="__main__":
    ensure_admin()
    os.makedirs(BASE_SERVER_DIR, exist_ok=True)
    app.run(host="0.0.0.0", port=25620, debug=True)