import os
import sys
import getpass
import requests
import subprocess
import socket
import struct
import platform
import uuid
import json
import threading
import time
import hashlib
import tempfile
import traceback
import shutil
import zipfile
import io
from pathlib import Path
from urllib.parse import urlencode, urlparse
from pypdf import PdfReader, PdfWriter
from pypdf.generic import BooleanObject, DictionaryObject, NameObject, TextStringObject, ArrayObject, NumberObject
from pystray import Icon, Menu, MenuItem
from PIL import Image, ImageDraw

# [설정]
CLIENT_CONFIG_FILE = "client_config.json"

def load_client_config():
    path = Path(CLIENT_CONFIG_FILE)
    if not path.exists():
        raise FileNotFoundError(f"{CLIENT_CONFIG_FILE} 파일을 찾을 수 없습니다.")
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            return loaded
    except Exception:
        pass
    raise ValueError(f"{CLIENT_CONFIG_FILE} 파일을 읽을 수 없습니다.")

CLIENT_CONFIG = load_client_config()
AUTH_SERVER_URL = str(CLIENT_CONFIG["auth_server_url"])
# CLICK_REDIRECT_URL: when PDF is opened, it will call server endpoint to log view
CLICK_REDIRECT_URL = AUTH_SERVER_URL.rstrip('/') + "/pdf_open"
LOCK_PORT = int(CLIENT_CONFIG["lock_port"])  # 중복 실행 방지용 포트
AGENT_COMMAND_PORT = int(CLIENT_CONFIG["agent_command_port"])  # PDF 더블클릭 요청 전달용 로컬 포트
DRM_TRACKING_INTERVAL_SECONDS = int(CLIENT_CONFIG["drm_tracking_interval_seconds"])
DRM_ARTIFACT_LIMIT = int(CLIENT_CONFIG["drm_artifact_limit"])
DRM_SCAN_FILE_LIMIT = int(CLIENT_CONFIG["drm_scan_file_limit"])
ARTIFACT_SOCKET_PORT = int(CLIENT_CONFIG["artifact_socket_port"])
CLIENT_LOG_FILE = str(CLIENT_CONFIG["client_log_file"])
LOCAL_ARTIFACTS_DIR = str(CLIENT_CONFIG["local_artifacts_dir"])
DRM_VIEW_ICON_FILE = str(CLIENT_CONFIG["drm_view_icon_file"])
DRM_PDF_PROG_ID = str(CLIENT_CONFIG["drm_pdf_prog_id"])
DRM_REG_BACKUP_KEY = str(CLIENT_CONFIG["drm_reg_backup_key"])
DRM_CAPABILITIES_KEY = str(CLIENT_CONFIG["drm_capabilities_key"])

def send_local_agent_command(payload):
    try:
        data = json.dumps(payload).encode("utf-8")
        with socket.create_connection(("127.0.0.1", AGENT_COMMAND_PORT), timeout=2) as sock:
            sock.sendall(data)
            sock.shutdown(socket.SHUT_WR)
            response = sock.recv(4096).decode("utf-8", errors="replace")
        return json.loads(response) if response else {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

def read_reg_default(root, path):
    if os.name != "nt":
        return None
    try:
        import winreg
        with winreg.OpenKey(root, path) as key:
            value, _ = winreg.QueryValueEx(key, "")
            return value
    except Exception:
        return None

def read_reg_value(root, path, name):
    if os.name != "nt":
        return None
    try:
        import winreg
        with winreg.OpenKey(root, path) as key:
            value, _ = winreg.QueryValueEx(key, name)
            return value
    except Exception:
        return None

def read_original_pdf_command():
    if os.name != "nt":
        return None
    try:
        import winreg
        prog_id = read_reg_default(winreg.HKEY_CURRENT_USER, r"Software\Classes\.pdf")
        if prog_id == DRM_PDF_PROG_ID:
            prog_id = None
        if not prog_id:
            prog_id = read_reg_default(winreg.HKEY_CLASSES_ROOT, ".pdf")
        if not prog_id or prog_id == DRM_PDF_PROG_ID:
            return None
        command_path = f"{prog_id}\\shell\\open\\command"
        command = read_reg_default(winreg.HKEY_CURRENT_USER, fr"Software\Classes\{command_path}")
        if not command:
            command = read_reg_default(winreg.HKEY_CLASSES_ROOT, command_path)
        return {"prog_id": prog_id, "command": command}
    except Exception:
        return None

def expand_open_command(command, pdf_path):
    if not command:
        return None
    if "%1" in command:
        return command.replace("%1", pdf_path)
    if '"%L"' in command:
        return command.replace("%L", pdf_path)
    return f'{command} "{pdf_path}"'

def open_with_original_pdf_viewer(pdf_path):
    if os.name != "nt":
        return False
    try:
        import winreg
        command = read_reg_value(winreg.HKEY_CURRENT_USER, DRM_REG_BACKUP_KEY, "OriginalOpenCommand")
        if not command:
            backup = read_original_pdf_command()
            command = backup.get("command") if backup else None
        expanded = expand_open_command(command, pdf_path)
        if not expanded:
            return False
        subprocess.Popen(expanded, shell=True, **hidden_subprocess_kwargs())
        return True
    except Exception:
        client_log(f"open original viewer failed: {pdf_path}\n{traceback.format_exc()}")
        return False

def install_pdf_double_click_handler():
    if os.name != "nt":
        return False, "Windows에서만 지원됩니다."
    try:
        import winreg
        backup = read_original_pdf_command()
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, DRM_REG_BACKUP_KEY) as key:
            if backup:
                winreg.SetValueEx(key, "OriginalProgId", 0, winreg.REG_SZ, backup.get("prog_id") or "")
                winreg.SetValueEx(key, "OriginalOpenCommand", 0, winreg.REG_SZ, backup.get("command") or "")
            winreg.SetValueEx(key, "ClientPath", 0, winreg.REG_SZ, str(Path(__file__).resolve()))
        pythonw = sys.executable.replace("python.exe", "pythonw.exe")
        command = f'"{pythonw}" "{Path(__file__).resolve()}" --agent-open "%1"'
        client_path = str(Path(__file__).resolve())
        icon_path = str((Path(__file__).resolve().parent / DRM_VIEW_ICON_FILE).resolve())
        if not os.path.exists(icon_path):
            icon_path = client_path
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\.pdf") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, DRM_PDF_PROG_ID)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, fr"Software\Classes\{DRM_PDF_PROG_ID}") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "DRM Agent PDF Handler")
            winreg.SetValueEx(key, "FriendlyTypeName", 0, winreg.REG_SZ, "DRM Agent PDF")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, fr"Software\Classes\{DRM_PDF_PROG_ID}\DefaultIcon") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f'"{icon_path}"')
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, fr"Software\Classes\{DRM_PDF_PROG_ID}\shell\open\command") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, DRM_CAPABILITIES_KEY) as key:
            winreg.SetValueEx(key, "ApplicationName", 0, winreg.REG_SZ, "DRM Agent Viewer")
            winreg.SetValueEx(key, "ApplicationDescription", 0, winreg.REG_SZ, "DRM Agent secure PDF viewer")
            winreg.SetValueEx(key, "ApplicationIcon", 0, winreg.REG_SZ, icon_path)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, DRM_CAPABILITIES_KEY + r"\FileAssociations") as key:
            winreg.SetValueEx(key, ".pdf", 0, winreg.REG_SZ, DRM_PDF_PROG_ID)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\RegisteredApplications") as key:
            winreg.SetValueEx(key, "DRM Agent Viewer", 0, winreg.REG_SZ, DRM_CAPABILITIES_KEY)
        subprocess.run(["cmd", "/c", "assoc", ".pdf"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **hidden_subprocess_kwargs())
        return True, "DRM Agent가 PDF 기본 앱 후보로 등록되었습니다. .pdf 기본 앱 전환은 Windows 기본 앱 설정에서 DRM Agent Viewer를 직접 선택해야 합니다."
    except Exception as e:
        client_log(f"install pdf handler failed\n{traceback.format_exc()}")
        return False, str(e)

def uninstall_pdf_double_click_handler():
    if os.name != "nt":
        return False, "Windows에서만 지원됩니다."
    try:
        import winreg
        original_prog_id = read_reg_default(winreg.HKEY_CURRENT_USER, DRM_REG_BACKUP_KEY + r"\OriginalProgId")
        if original_prog_id:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\.pdf") as key:
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ, original_prog_id)
        for key_path in (r"Software\Classes\.drmpdf", r"Software\Classes\DRMAgentDRMPDF"):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
            except Exception:
                pass
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\RegisteredApplications", 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, "DRM Agent Viewer")
        except Exception:
            pass
        return True, "DRM 전용 더블클릭 연동이 해제되었습니다."
    except Exception as e:
        client_log(f"uninstall pdf handler failed\n{traceback.format_exc()}")
        return False, str(e)

def client_log(message):
    try:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        with open(CLIENT_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {message}\n")
    except Exception:
        pass

def show_agent_message(message, title="DRM Agent", error=False, warning=False):
    if os.name == "nt":
        try:
            import ctypes
            icon = 0x10 if error else 0x30 if warning else 0x40
            ctypes.windll.user32.MessageBoxW(None, str(message), str(title), icon | 0x0)
            return
        except Exception:
            pass
    try:
        print(f"[{title}] {message}")
    except Exception:
        pass

def prompt_login_gui():
    try:
        import tkinter as tk
        from tkinter import messagebox
    except Exception:
        client_log(f"login gui unavailable\n{traceback.format_exc()}")
        return None, None

    result = {"id": None, "pw": None}
    root = tk.Tk()
    root.title("DRM Agent Login")
    root.geometry("420x260")
    root.minsize(420, 260)
    root.resizable(False, False)
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass

    container = tk.Frame(root)
    container.pack(fill="both", expand=True, padx=24, pady=22)

    tk.Label(container, text="DRM Agent Login", font=("맑은 고딕", 13, "bold")).pack(anchor="w", pady=(0, 16))

    tk.Label(container, text="ID").pack(anchor="w")
    id_entry = tk.Entry(container)
    id_entry.pack(fill="x", pady=(4, 12), ipady=3)

    tk.Label(container, text="PW").pack(anchor="w")
    pw_entry = tk.Entry(container, show="*")
    pw_entry.pack(fill="x", pady=(4, 18), ipady=3)

    button_frame = tk.Frame(container)
    button_frame.pack(fill="x", pady=(4, 0))

    def submit(event=None):
        uid = id_entry.get().strip()
        upw = pw_entry.get().strip()
        if not uid or not upw:
            messagebox.showwarning("DRM Agent", "ID와 PW를 입력하세요.", parent=root)
            return
        result["id"] = uid
        result["pw"] = upw
        root.destroy()

    def cancel():
        root.destroy()

    tk.Button(button_frame, text="로그인", width=12, command=submit).pack(side="right")
    tk.Button(button_frame, text="취소", width=12, command=cancel).pack(side="right", padx=(0, 10))

    root.bind("<Return>", submit)
    root.protocol("WM_DELETE_WINDOW", cancel)
    id_entry.focus_set()
    root.mainloop()
    return result["id"], result["pw"]

def hidden_subprocess_kwargs():
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {
        "startupinfo": startupinfo,
        "creationflags": subprocess.CREATE_NO_WINDOW
    }

class FinalDRMAgent:
    def __init__(self, user_id, drm_key, role, dept):
        self.user_id, self.drm_key, self.role, self.dept = user_id, drm_key, role, dept
        self.icon = None
        self.lock_socket = None
        self.reported_locations = set()
        self.completed_task_ids = set()

    def hold_lock(self):
        """프로그램 중복 실행 방지를 위해 포트를 점유합니다."""
        try:
            self.lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.lock_socket.bind(('127.0.0.1', LOCK_PORT))
            self.lock_socket.listen(1)
        except socket.error:
            # 포트가 이미 사용 중이면 프로그램 종료
            client_log("Another DRM Agent instance is already running; lock port is occupied.")
            return False
        return True

    def start_command_server(self):
        def command_server():
            try:
                server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                server.bind(("127.0.0.1", AGENT_COMMAND_PORT))
                server.listen(5)
                client_log(f"agent command server listening: 127.0.0.1:{AGENT_COMMAND_PORT}")
            except Exception:
                client_log(f"agent command server failed\n{traceback.format_exc()}")
                return
            while True:
                try:
                    conn, addr = server.accept()
                    threading.Thread(target=self.handle_command_connection, args=(conn, addr), daemon=True).start()
                except Exception:
                    client_log(f"agent command accept failed\n{traceback.format_exc()}")
                    time.sleep(1)
        threading.Thread(target=command_server, daemon=True).start()

    def handle_command_connection(self, conn, addr):
        with conn:
            try:
                data = b""
                while True:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    data += chunk
                    if len(data) > 1024 * 1024:
                        raise ValueError("local command too large")
                command = json.loads(data.decode("utf-8"))
                action = command.get("action")
                if action == "secure_view":
                    pdf_path = command.get("path")
                    self.launch_secure_view_process(pdf_path, fallback_original=True)
                    conn.sendall(json.dumps({"success": True}).encode("utf-8"))
                else:
                    conn.sendall(json.dumps({"success": False, "error": "unknown command"}).encode("utf-8"))
            except Exception as e:
                client_log(f"agent command failed from {addr}\n{traceback.format_exc()}")
                try:
                    conn.sendall(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))
                except Exception:
                    pass

    def server_log(self, action, filename, extra=None):
        payload = {
            "user_id": self.user_id,
            "action": action,
            "filename": filename,
        }
        if extra:
            payload.update(extra)
        # attach system info for important actions
        if action in ("ENCRYPT", "DECRYPT", "CREATE", "MOVE", "LOCATION_SEEN", "VIEW", "OPEN", "PRESERVATION_COLLECTED"):
            payload.update(self.get_system_info())
        try:
            requests.post(f"{AUTH_SERVER_URL}/log_event", json=payload, timeout=4)
        except Exception:
            pass

    def get_system_info(self):
        try:
            hostname = socket.gethostname()
            os_name = platform.system()
            os_version = platform.version()
            ip = self._get_local_ip()
            mac = self._get_mac()
            geo = self._get_geo(ip)
            return {
                "agent_id": self.user_id,
                "os_name": os_name,
                "os_version": os_version,
                "hostname": hostname,
                "ip": ip,
                "mac": mac,
                "geo": geo,
                "dept": self.dept
            }
        except Exception:
            return {}

    def _get_local_ip(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def _get_mac(self):
        try:
            node = uuid.getnode()
            mac = ':'.join([f"{(node >> ele) & 0xff:02x}" for ele in range(0,8*6,8)][::-1])
            return mac
        except Exception:
            return "unknown"

    def _get_geo(self, ip):
        try:
            # ASN(Autonomous System Number) 및 ISP 정보를 포함하여 공인 IP 기반 위치 정보 획득
            fields = "status,message,country,regionName,city,lat,lon,zip,as,isp,query"
            target_url = f"http://ip-api.com/json/{ip}?fields={fields}" if ip != "127.0.0.1" else f"http://ip-api.com/json/?fields={fields}"
            r = requests.get(target_url, timeout=3)
            if r.status_code == 200:
                data = r.json()
                return {k: data.get(k) for k in ("country","regionName","city","lat","lon","zip", "as", "isp")}
        except Exception:
            pass
        return {}

    def build_file_id(self, input_pdf):
        try:
            stat = os.stat(input_pdf)
            source = f"{self.user_id}|{os.path.basename(input_pdf)}|{stat.st_size}|{int(stat.st_mtime)}"
            return str(uuid.uuid5(uuid.NAMESPACE_URL, source))
        except Exception:
            return str(uuid.uuid4())

    def build_pdf_open_url(self, output_pdf, file_id, trigger_kind="unknown"):
        params = {
            "file": os.path.basename(output_pdf),
            "file_id": file_id,
            "creator_agent_id": self.user_id,
            "trigger": trigger_kind,
        }
        return f"{CLICK_REDIRECT_URL}?{urlencode(params)}"

    def build_launch_action(self, url):
        return DictionaryObject({
            NameObject("/S"): NameObject("/JavaScript"),
            NameObject("/JS"): TextStringObject(f'app.launchURL("{url}", true);')
        })

    def build_uri_action(self, url):
        return DictionaryObject({
            NameObject("/S"): NameObject("/URI"),
            NameObject("/URI"): TextStringObject(url)
        })

    def build_submit_form_action(self, url):
        return DictionaryObject({
            NameObject("/S"): NameObject("/SubmitForm"),
            NameObject("/F"): TextStringObject(url),
            NameObject("/Flags"): NumberObject(4)
        })

    def read_drm_metadata(self, pdf_path):
        try:
            reader = PdfReader(pdf_path)
            metadata = reader.metadata or {}
            return {
                "file_id": str(metadata.get("/DRM_FileID", "")),
                "creator_agent_id": str(metadata.get("/DRM_Agent", "")),
                "creator_host": str(metadata.get("/DRM_Host", "")),
                "creator_os": str(metadata.get("/DRM_OS", "")),
                "creator_dept": str(metadata.get("/DRM_Dept", "")),
            }
        except Exception:
            return {}

    def get_artifact_search_roots(self):
        roots = [Path(".").resolve()]
        home = Path.home()
        for relative in ("Desktop", "Downloads", "Documents", "OneDrive/Desktop", "OneDrive/Documents"):
            candidate = home / relative
            if candidate.exists():
                roots.append(candidate.resolve())
        unique_roots = []
        seen = set()
        for root in roots:
            root_key = str(root).lower()
            if root_key not in seen:
                seen.add(root_key)
                unique_roots.append(root)
        return unique_roots

    def hash_file_head(self, path, size=1024 * 1024):
        try:
            digest = hashlib.sha256()
            with open(path, "rb") as f:
                digest.update(f.read(size))
            return digest.hexdigest()
        except Exception:
            return ""

    def collect_recent_items(self):
        recent_dir = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Recent"
        items = []
        try:
            for path in sorted(recent_dir.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)[:DRM_ARTIFACT_LIMIT]:
                stat = path.stat()
                items.append({
                    "name": path.name,
                    "path": str(path.resolve()),
                    "mtime": int(stat.st_mtime)
                })
        except Exception:
            pass
        return items

    def collect_process_snapshot(self):
        try:
            output = subprocess.check_output(
                ["tasklist", "/fo", "csv", "/nh"],
                text=True,
                encoding="mbcs" if os.name == "nt" else "utf-8",
                errors="replace",
                stderr=subprocess.DEVNULL,
                timeout=4,
                **hidden_subprocess_kwargs()
            )
            interesting = []
            keywords = ("AcroRd32", "Acrobat", "chrome", "msedge", "firefox", "explorer")
            for line in output.splitlines():
                if any(keyword.lower() in line.lower() for keyword in keywords):
                    interesting.append(line)
            return interesting[:DRM_ARTIFACT_LIMIT]
        except Exception:
            return []

    def collect_network_snapshot(self):
        try:
            output = subprocess.check_output(
                ["netstat", "-ano"],
                text=True,
                encoding="mbcs" if os.name == "nt" else "utf-8",
                errors="replace",
                stderr=subprocess.DEVNULL,
                timeout=4,
                **hidden_subprocess_kwargs()
            )
            return [
                line.strip()
                for line in output.splitlines()
                if "ESTABLISHED" in line
            ][:DRM_ARTIFACT_LIMIT]
        except Exception:
            return []

    def run_command_text(self, command, timeout=20):
        try:
            return subprocess.check_output(
                command,
                text=True,
                encoding="mbcs" if os.name == "nt" else "utf-8",
                errors="replace",
                stderr=subprocess.STDOUT,
                timeout=timeout,
                **hidden_subprocess_kwargs()
            )
        except Exception as e:
            return f"[COMMAND_FAILED] {' '.join(command)}\n{e}\n"

    def collect_windows_event_log(self, log_name, count=80):
        return self.run_command_text(
            ["wevtutil", "qe", log_name, "/c:" + str(count), "/rd:true", "/f:text"],
            timeout=12
        )

    def powershell_quote(self, value):
        return "'" + str(value).replace("'", "''") + "'"

    def run_powershell_script(self, script, timeout=60):
        return subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            **hidden_subprocess_kwargs()
        )

    def collect_text_command_to_file(self, base_dir, relative_path, command, timeout=60):
        target = Path(base_dir) / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        command_text = " ".join(command)
        script = (
            f"& {command_text} 2>&1 | "
            f"Out-File -LiteralPath {self.powershell_quote(target)} -Encoding UTF8 -Width 4096"
        )
        try:
            result = self.run_powershell_script(script, timeout=timeout)
            if result.returncode != 0 and not target.exists():
                self.write_text_file(base_dir, relative_path, f"[COMMAND_FAILED] {command_text}\n{result.stdout}")
        except Exception as e:
            self.write_text_file(base_dir, relative_path, f"[COMMAND_FAILED] {command_text}\n{e}\n")

    def collect_powershell_to_file(self, base_dir, relative_path, script_body, timeout=60):
        target = Path(base_dir) / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        script = (
            f"{script_body} | "
            f"Out-File -LiteralPath {self.powershell_quote(target)} -Encoding UTF8 -Width 4096"
        )
        try:
            result = self.run_powershell_script(script, timeout=timeout)
            if result.returncode != 0 and not target.exists():
                self.write_text_file(base_dir, relative_path, f"[POWERSHELL_FAILED]\n{script_body}\n{result.stdout}")
        except Exception as e:
            self.write_text_file(base_dir, relative_path, f"[POWERSHELL_FAILED]\n{script_body}\n{e}\n")

    def export_event_log(self, base_dir, log_name, relative_path):
        target = Path(base_dir) / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        script = (
            f"wevtutil epl {self.powershell_quote(log_name)} {self.powershell_quote(target)} /ow:true"
        )
        try:
            result = self.run_powershell_script(script, timeout=60)
            status_path = target.with_suffix(target.suffix + ".status.txt")
            if result.returncode == 0 and target.exists():
                status_path.write_text(f"Exported {log_name} to {target}\n", encoding="utf-8")
                return True
            status_path.write_text(
                f"[EVENT_LOG_EXPORT_FAILED] {log_name}\nreturn_code={result.returncode}\n{result.stdout}",
                encoding="utf-8",
                errors="replace"
            )
        except Exception as e:
            target.with_suffix(target.suffix + ".status.txt").write_text(
                f"[EVENT_LOG_EXPORT_FAILED] {log_name}\n{e}\n",
                encoding="utf-8",
                errors="replace"
            )
        return False

    def export_accessible_security_channels(self, base_dir):
        channels = {
            "Windows_PowerShell.evtx": "Windows PowerShell",
            "PowerShell_Operational.evtx": "Microsoft-Windows-PowerShell/Operational",
            "Defender_Operational.evtx": "Microsoft-Windows-Windows Defender/Operational",
            "TaskScheduler_Operational.evtx": "Microsoft-Windows-TaskScheduler/Operational",
            "TerminalServices_LocalSessionManager.evtx": "Microsoft-Windows-TerminalServices-LocalSessionManager/Operational",
            "WindowsUpdateClient_Operational.evtx": "Microsoft-Windows-WindowsUpdateClient/Operational",
            "CodeIntegrity_Operational.evtx": "Microsoft-Windows-CodeIntegrity/Operational",
            "AppLocker_EXE_DLL.evtx": "Microsoft-Windows-AppLocker/EXE and DLL",
            "AppLocker_MSI_Script.evtx": "Microsoft-Windows-AppLocker/MSI and Script",
        }
        exported = []
        for filename, channel in channels.items():
            if self.export_event_log(base_dir, channel, f"event_logs/accessible_security/{filename}"):
                exported.append(channel)
        self.write_json_file(base_dir, "event_logs/accessible_security/export_summary.json", {
            "exported_channels": exported,
            "note": "These channels are attempted with normal user permissions as a fallback when Security.evtx is not accessible."
        })

    def collect_user_mode_security_context(self, base_dir):
        self.collect_text_command_to_file(base_dir, "security_fallback/whoami_all.txt", ["whoami", "/all"], timeout=30)
        self.collect_text_command_to_file(base_dir, "security_fallback/query_user.txt", ["query", "user"], timeout=20)
        self.collect_text_command_to_file(base_dir, "security_fallback/net_user_current.txt", ["net", "user", getpass.getuser()], timeout=30)
        self.collect_text_command_to_file(base_dir, "security_fallback/net_localgroup_administrators.txt", ["net", "localgroup", "administrators"], timeout=30)
        self.collect_powershell_to_file(
            base_dir,
            "security_fallback/local_users.csv",
            "Get-LocalUser | Select-Object Name,Enabled,LastLogon,PasswordRequired,PasswordLastSet,SID | ConvertTo-Csv -NoTypeInformation",
            timeout=60
        )
        self.collect_powershell_to_file(
            base_dir,
            "security_fallback/local_groups.csv",
            "Get-LocalGroup | Select-Object Name,SID,Description | ConvertTo-Csv -NoTypeInformation",
            timeout=60
        )
        self.collect_powershell_to_file(
            base_dir,
            "security_fallback/logon_sessions.json",
            "Get-CimInstance Win32_LogonSession | Select-Object LogonId,LogonType,StartTime,AuthenticationPackage | ConvertTo-Json -Depth 4",
            timeout=60
        )
        self.collect_powershell_to_file(
            base_dir,
            "security_fallback/startup_commands.json",
            "Get-CimInstance Win32_StartupCommand | Select-Object Name,Command,Location,User | ConvertTo-Json -Depth 4",
            timeout=60
        )
        self.collect_powershell_to_file(
            base_dir,
            "security_fallback/scheduled_tasks.csv",
            "Get-ScheduledTask | Select-Object TaskName,TaskPath,State,Author | ConvertTo-Csv -NoTypeInformation",
            timeout=90
        )
        self.collect_powershell_to_file(
            base_dir,
            "security_fallback/defender_status.json",
            "Get-MpComputerStatus | ConvertTo-Json -Depth 4",
            timeout=60
        )
        self.collect_powershell_to_file(
            base_dir,
            "security_fallback/firewall_profiles.json",
            "Get-NetFirewallProfile | Select-Object Name,Enabled,DefaultInboundAction,DefaultOutboundAction,NotifyOnListen | ConvertTo-Json -Depth 4",
            timeout=60
        )

    def write_text_file(self, base_dir, relative_path, content):
        path = Path(base_dir) / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content or "", encoding="utf-8", errors="ignore")

    def write_json_file(self, base_dir, relative_path, data):
        path = Path(base_dir) / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def create_artifact_bundle(self, task, target_file_id=None):
        task_id = task.get("event_id") or str(uuid.uuid4())
        bundle_root = Path(tempfile.mkdtemp(prefix="drm_artifact_"))
        bundle_name = f"artifact_{self.user_id}_{target_file_id or 'unknown'}_{task_id}.zip"
        bundle_path = bundle_root / bundle_name
        manifest = {
            "agent_id": self.user_id,
            "dept": self.dept,
            "task_id": task_id,
            "file_id": target_file_id,
            "incident_event_id": task.get("incident_event_id"),
            "collection_time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "collection_scope": "security_incident_triage_metadata",
            "system_info": self.get_system_info(),
            "target_evidence": task.get("target_evidence", {})
        }
        lightweight = self.collect_lightweight_artifacts(target_file_id)
        self.write_json_file(bundle_root, "manifest.json", manifest)
        self.write_json_file(bundle_root, "drm_related_pdf_inventory.json", lightweight.get("tracked_pdfs", []))
        self.write_json_file(bundle_root, "recent_items.json", lightweight.get("recent_items", []))
        self.collect_text_command_to_file(bundle_root, "system/systeminfo.txt", ["systeminfo"], timeout=60)
        self.collect_text_command_to_file(bundle_root, "system/whoami_all.txt", ["whoami", "/all"], timeout=30)
        self.collect_text_command_to_file(bundle_root, "system/ipconfig_all.txt", ["ipconfig", "/all"], timeout=30)
        self.collect_text_command_to_file(bundle_root, "network/netstat_ano.txt", ["netstat", "-ano"], timeout=30)
        self.collect_powershell_to_file(
            bundle_root,
            "system/computer_info.json",
            "Get-ComputerInfo | ConvertTo-Json -Depth 3",
            timeout=60
        )
        self.collect_powershell_to_file(
            bundle_root,
            "process/process_list.json",
            "Get-CimInstance Win32_Process | Select-Object ProcessId,Name,ExecutablePath,CommandLine,ParentProcessId,CreationDate | ConvertTo-Json -Depth 4",
            timeout=60
        )
        self.collect_powershell_to_file(
            bundle_root,
            "process/process_list.csv",
            "Get-CimInstance Win32_Process | Select-Object ProcessId,Name,ExecutablePath,CommandLine,ParentProcessId,CreationDate | ConvertTo-Csv -NoTypeInformation",
            timeout=60
        )
        self.collect_powershell_to_file(
            bundle_root,
            "services/service_list.csv",
            "Get-Service | Select-Object Name,DisplayName,Status,StartType,ServiceType | ConvertTo-Csv -NoTypeInformation",
            timeout=60
        )
        self.collect_powershell_to_file(
            bundle_root,
            "network/tcp_connections.json",
            "Get-NetTCPConnection | Select-Object LocalAddress,LocalPort,RemoteAddress,RemotePort,State,OwningProcess,CreationTime | ConvertTo-Json -Depth 3",
            timeout=60
        )
        self.collect_powershell_to_file(
            bundle_root,
            "network/ip_addresses.json",
            "Get-NetIPAddress | Select-Object InterfaceAlias,IPAddress,AddressFamily,PrefixLength,AddressState | ConvertTo-Json -Depth 3",
            timeout=60
        )
        self.export_event_log(bundle_root, "Application", "event_logs/Application.evtx")
        self.export_event_log(bundle_root, "System", "event_logs/System.evtx")
        security_exported = self.export_event_log(bundle_root, "Security", "event_logs/Security.evtx")
        self.export_accessible_security_channels(bundle_root)
        self.collect_user_mode_security_context(bundle_root)
        self.write_json_file(bundle_root, "security_fallback/security_collection_note.json", {
            "security_evtx_exported": security_exported,
            "note": "Security.evtx usually requires administrator privileges. User-mode fallback artifacts were collected regardless of Security.evtx export result."
        })

        with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in bundle_root.rglob("*"):
                if path == bundle_path or path.is_dir():
                    continue
                zf.write(path, path.relative_to(bundle_root))
        return str(bundle_path), manifest

    def save_local_artifact_copy(self, bundle_path, manifest):
        file_id = self.safe_artifact_name(manifest.get("file_id") or "unknown")
        agent_id = self.safe_artifact_name(self.user_id)
        task_id = self.safe_artifact_name(manifest.get("task_id") or str(uuid.uuid4()))
        timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        target_dir = Path(LOCAL_ARTIFACTS_DIR) / file_id / agent_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / f"{timestamp}_{task_id}_artifact_bundle.zip"
        shutil.copy2(bundle_path, target_path)
        client_log(f"artifact local copy saved: {target_path}")
        return str(target_path.resolve())

    def safe_artifact_name(self, value):
        safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(value or "unknown"))
        return safe[:120] or "unknown"

    def get_artifact_server_host(self):
        parsed = urlparse(AUTH_SERVER_URL)
        return parsed.hostname or AUTH_SERVER_URL.split("//")[-1].split(":")[0]

    def send_artifact_bundle(self, bundle_path, manifest):
        path = Path(bundle_path)
        data = path.read_bytes()
        sha256 = hashlib.sha256(data).hexdigest()
        header = {
            "agent_id": self.user_id,
            "file_id": manifest.get("file_id"),
            "task_id": manifest.get("task_id"),
            "incident_event_id": manifest.get("incident_event_id"),
            "hostname": socket.gethostname(),
            "bundle_name": path.name,
            "size": len(data),
            "sha256": sha256,
            "artifact_kinds": [
                "manifest",
                "event_logs_evtx",
                "accessible_security_event_channels",
                "user_mode_security_context",
                "system_info_utf8",
                "process_list_json_csv",
                "network_list_json_txt",
                "service_list_csv",
                "recent_items",
                "drm_related_pdf_inventory"
            ]
        }
        header_bytes = json.dumps(header, ensure_ascii=False).encode("utf-8")
        with socket.create_connection((self.get_artifact_server_host(), ARTIFACT_SOCKET_PORT), timeout=10) as sock:
            sock.sendall(struct.pack("!Q", len(header_bytes)))
            sock.sendall(header_bytes)
            sock.sendall(struct.pack("!Q", len(data)))
            sock.sendall(data)
            sock.settimeout(10)
            response = sock.recv(8192)
        if response:
            result = json.loads(response.decode("utf-8"))
            if not result.get("success"):
                raise RuntimeError(result.get("error", "artifact upload failed"))
            return {
                "uploaded": True,
                "sha256": sha256,
                "artifact_path_on_server": result.get("artifact_path"),
                "sha256_verified": result.get("sha256_verified"),
                "size": len(data)
            }
        return {"uploaded": True, "sha256": sha256, "size": len(data)}

    def iter_content_streams(self, page):
        if "/Contents" not in page:
            return []
        content_ref = page["/Contents"]
        content_obj = content_ref.get_object() if hasattr(content_ref, "get_object") else content_ref
        if isinstance(content_obj, (list, ArrayObject)):
            candidates = content_obj
        else:
            candidates = [content_obj]
        streams = []
        for candidate in candidates:
            stream = candidate.get_object() if hasattr(candidate, "get_object") else candidate
            if hasattr(stream, "get_data"):
                streams.append(stream)
        return streams

    def xor_content_stream(self, stream, xor_key):
        raw_data = bytearray(stream.get_data())
        stream._data = bytes([b ^ xor_key for b in raw_data])
        for key in (NameObject("/Filter"), NameObject("/DecodeParms")):
            try:
                if key in stream:
                    del stream[key]
            except Exception:
                pass

    def collect_lightweight_artifacts(self, target_file_id=None):
        """Collect non-invasive local traces useful for an initial DRM incident timeline."""
        artifacts = {
            "username": getpass.getuser(),
            "working_dir": os.path.abspath("."),
            "hostname": socket.gethostname(),
            "collection_scope": "metadata_only",
            "search_roots": [str(root) for root in self.get_artifact_search_roots()],
            "tracked_pdfs": [],
            "recent_items": self.collect_recent_items(),
            "process_snapshot": self.collect_process_snapshot(),
            "network_snapshot": self.collect_network_snapshot()
        }
        try:
            pdf_paths = []
            for root in self.get_artifact_search_roots():
                try:
                    pdf_paths.extend(root.rglob("*.pdf"))
                except Exception:
                    pass
            pdf_paths = sorted(
                {str(path.resolve()): path for path in pdf_paths}.values(),
                key=lambda p: p.stat().st_mtime,
                reverse=True
            )[:DRM_SCAN_FILE_LIMIT]
            for path in pdf_paths:
                try:
                    stat = path.stat()
                    metadata = self.read_drm_metadata(str(path))
                    if target_file_id and metadata.get("file_id") != target_file_id:
                        continue
                    artifacts["tracked_pdfs"].append({
                        "filename": path.name,
                        "path": str(path.resolve()),
                        "size": stat.st_size,
                        "mtime": int(stat.st_mtime),
                        "file_id": metadata.get("file_id"),
                        "creator_agent_id": metadata.get("creator_agent_id"),
                        "sha256_head": self.hash_file_head(path),
                        "has_drm_marker": "_Encryption" in path.name or bool(metadata.get("file_id"))
                    })
                    if len(artifacts["tracked_pdfs"]) >= DRM_ARTIFACT_LIMIT:
                        break
                except Exception:
                    pass
        except Exception:
            pass
        return artifacts

    def report_pdf_location(self, pdf_path, action="LOCATION_SEEN"):
        metadata = self.read_drm_metadata(pdf_path)
        if not metadata.get("file_id") and "_Encryption" not in os.path.basename(pdf_path):
            return
        try:
            abs_path = os.path.abspath(pdf_path)
            stat = os.stat(pdf_path)
            location_key = (
                metadata.get("file_id") or os.path.basename(pdf_path),
                abs_path,
                int(stat.st_mtime),
                stat.st_size,
                action,
            )
            if location_key in self.reported_locations:
                return
            self.reported_locations.add(location_key)
            payload = {
                "file_id": metadata.get("file_id"),
                "creator_agent_id": metadata.get("creator_agent_id"),
                "creator_host": metadata.get("creator_host"),
                "creator_os": metadata.get("creator_os"),
                "current_path": abs_path,
                "file_size": stat.st_size,
                "file_mtime": int(stat.st_mtime),
            }
            self.server_log(action, os.path.basename(pdf_path), payload)
        except Exception:
            pass

    def poll_preservation_tasks(self):
        try:
            resp = requests.get(f"{AUTH_SERVER_URL}/agent_tasks/{self.user_id}", timeout=4)
            if resp.status_code != 200:
                client_log(f"agent_tasks request failed: status={resp.status_code}, body={resp.text[:300]}")
                return
            tasks = resp.json().get("tasks", [])
            if tasks:
                client_log(f"agent_tasks received: count={len(tasks)}")
            for task in tasks:
                task_id = task.get("event_id")
                if not task_id or task_id in self.completed_task_ids:
                    continue
                self.completed_task_ids.add(task_id)
                file_id = task.get("file_id")
                upload_result = {"uploaded": False}
                local_artifact_path = None
                try:
                    client_log(f"artifact collection start: task={task_id}, file_id={file_id}")
                    bundle_path, manifest = self.create_artifact_bundle(task, file_id)
                    local_artifact_path = self.save_local_artifact_copy(bundle_path, manifest)
                    upload_result = self.send_artifact_bundle(bundle_path, manifest)
                    client_log(f"artifact collection done: task={task_id}, local={local_artifact_path}, transfer={upload_result}")
                except Exception as e:
                    upload_result = {"uploaded": False, "error": str(e)}
                    client_log(f"artifact upload failed or collection failed: task={task_id}, file_id={file_id}\n{traceback.format_exc()}")
                payload = {
                    "task_id": task_id,
                    "file_id": file_id,
                    "incident_event_id": task.get("incident_event_id"),
                    "preservation_reason": task.get("reason"),
                    "local_artifact_path": local_artifact_path,
                    "artifact_transfer": upload_result
                }
                self.server_log("PRESERVATION_COLLECTED", file_id or "unknown", payload)
        except Exception:
            client_log(f"agent_tasks polling failed\n{traceback.format_exc()}")

    def start_background_tasks(self):
        def monitor():
            while True:
                try:
                    self.auto_encrypt()
                    self.poll_preservation_tasks()
                    for name in os.listdir('.'):
                        if name.lower().endswith(".pdf") and "_Encryption" in name:
                            self.report_pdf_location(name)
                except Exception:
                    pass
                time.sleep(DRM_TRACKING_INTERVAL_SECONDS)
        threading.Thread(target=monitor, daemon=True).start()

    def process_pdf(self, input_pdf, output_pdf, mode="ENCRYPT"):
        try:
            xor_key = self.drm_key & 0xFF
            client_log(f"process_pdf start: mode={mode}, input={input_pdf}, output={output_pdf}")
            reader = PdfReader(input_pdf)
            writer = PdfWriter()
            # create a lightweight file id to embed in metadata
            file_id = self.build_file_id(input_pdf)
            trigger_urls = {
                "open_action": self.build_pdf_open_url(output_pdf, file_id, "open_action"),
                "document_js": self.build_pdf_open_url(output_pdf, file_id, "document_js"),
                "page_open": self.build_pdf_open_url(output_pdf, file_id, "page_open"),
                "page_close": self.build_pdf_open_url(output_pdf, file_id, "page_close"),
                "annotation_page_open": self.build_pdf_open_url(output_pdf, file_id, "annotation_page_open"),
                "annotation_page_visible": self.build_pdf_open_url(output_pdf, file_id, "annotation_page_visible"),
                "submit_form_page_open": self.build_pdf_open_url(output_pdf, file_id, "submit_form_page_open"),
                "submit_form_page_visible": self.build_pdf_open_url(output_pdf, file_id, "submit_form_page_visible"),
                "submit_form_click": self.build_pdf_open_url(output_pdf, file_id, "submit_form_click"),
                "link_click": self.build_pdf_open_url(output_pdf, file_id, "link_click"),
            }
            form_fields = ArrayObject()

            for page in reader.pages:
                for content_stream in self.iter_content_streams(page):
                    self.xor_content_stream(content_stream, xor_key)
                
                if mode == "ENCRYPT":
                    mbox = page.mediabox
                    link = DictionaryObject({
                        NameObject("/Type"): NameObject("/Annot"),
                        NameObject("/Subtype"): NameObject("/Link"),
                        NameObject("/Rect"): ArrayObject([NumberObject(mbox.left), NumberObject(mbox.bottom), NumberObject(mbox.right), NumberObject(mbox.top)]),
                        NameObject("/A"): self.build_uri_action(trigger_urls["link_click"]),
                        NameObject("/Border"): ArrayObject([NumberObject(0), NumberObject(0), NumberObject(0)]),
                        NameObject("/AA"): DictionaryObject({
                            NameObject("/PO"): self.build_launch_action(trigger_urls["annotation_page_open"]),
                            NameObject("/PV"): self.build_launch_action(trigger_urls["annotation_page_visible"]),
                        })
                    })
                    submit_widget = DictionaryObject({
                        NameObject("/Type"): NameObject("/Annot"),
                        NameObject("/Subtype"): NameObject("/Widget"),
                        NameObject("/FT"): NameObject("/Btn"),
                        NameObject("/T"): TextStringObject(f"DRMSubmitTrigger_{len(form_fields)}"),
                        NameObject("/Rect"): ArrayObject([
                            NumberObject(mbox.left),
                            NumberObject(mbox.bottom),
                            NumberObject(mbox.left + 1),
                            NumberObject(mbox.bottom + 1)
                        ]),
                        NameObject("/Ff"): NumberObject(65536),
                        NameObject("/A"): self.build_submit_form_action(trigger_urls["submit_form_click"]),
                        NameObject("/AA"): DictionaryObject({
                            NameObject("/PO"): self.build_submit_form_action(trigger_urls["submit_form_page_open"]),
                            NameObject("/PV"): self.build_submit_form_action(trigger_urls["submit_form_page_visible"]),
                        }),
                        NameObject("/Border"): ArrayObject([NumberObject(0), NumberObject(0), NumberObject(0)])
                    })
                    if "/Annots" not in page: page[NameObject("/Annots")] = ArrayObject()
                    page["/Annots"].append(link)
                    page["/Annots"].append(submit_widget)
                    form_fields.append(submit_widget)
                    page[NameObject("/AA")] = DictionaryObject({
                        NameObject("/O"): self.build_launch_action(trigger_urls["page_open"]),
                        NameObject("/C"): self.build_launch_action(trigger_urls["page_close"])
                    })
                else:
                    if "/Annots" in page: del page[NameObject("/Annots")]
                    if "/AA" in page: del page[NameObject("/AA")]
                writer.add_page(page)

            if mode == "ENCRYPT":
                writer._root_object.update({
                    NameObject("/OpenAction"): self.build_launch_action(trigger_urls["open_action"])
                })
                writer._root_object.update({
                    NameObject("/Names"): DictionaryObject({
                        NameObject("/JavaScript"): DictionaryObject({
                            NameObject("/Names"): ArrayObject([
                                TextStringObject("DRMOpenTracking"),
                                self.build_launch_action(trigger_urls["document_js"])
                            ])
                        })
                    })
                })
                writer._root_object.update({
                    NameObject("/AcroForm"): DictionaryObject({
                        NameObject("/Fields"): form_fields,
                        NameObject("/NeedAppearances"): BooleanObject(True)
                    })
                })
                # embed metadata about creator / source
                try:
                    meta = writer.metadata or {}
                    meta.update({
                        "/Author": TextStringObject(f"DRM Agent {self.user_id}"),
                        "/Subject": TextStringObject(f"DRM creator_agent_id={self.user_id}; file_id={file_id}"),
                        "/DRM_Agent": TextStringObject(self.user_id),
                        "/DRM_FileID": TextStringObject(file_id),
                        "/DRM_Dept": TextStringObject(self.dept),
                        "/DRM_Host": TextStringObject(socket.gethostname()),
                        "/DRM_OS": TextStringObject(platform.platform()),
                    })
                    writer.add_metadata(meta)
                except Exception:
                    pass
            else:
                if "/OpenAction" in writer._root_object: del writer._root_object[NameObject("/OpenAction")]
                if "/Names" in writer._root_object: del writer._root_object[NameObject("/Names")]
                if "/AcroForm" in writer._root_object: del writer._root_object[NameObject("/AcroForm")]

            with open(output_pdf, "wb") as f: writer.write(f)
            # report creation to server with system info
            if mode == "ENCRYPT":
                try:
                    payload = {
                        "user_id": self.user_id,
                        "action": "CREATE",
                        "filename": os.path.basename(output_pdf),
                        "file_id": file_id,
                        "creator_agent_id": self.user_id,
                        "current_path": os.path.abspath(output_pdf),
                    }
                    payload.update(self.get_system_info())
                    requests.post(f"{AUTH_SERVER_URL}/log_event", json=payload, timeout=4)
                except Exception:
                    pass
                self.report_pdf_location(output_pdf)
            client_log(f"process_pdf success: output={output_pdf}")
            return True
        except Exception:
            client_log(f"process_pdf failed: input={input_pdf}, output={output_pdf}\n{traceback.format_exc()}")
            return False

    def auto_encrypt(self):
        scan_dir = Path.cwd()
        client_log(f"auto_encrypt scan_dir={scan_dir}")
        targets = [
            path for path in scan_dir.iterdir()
            if path.is_file()
            and path.suffix.lower() == ".pdf"
            and "_Encryption" not in path.name
            and "_Decrypted" not in path.name
        ]
        client_log(f"auto_encrypt targets={len(targets)}: {[path.name for path in targets]}")
        for path in targets:
            out = path.with_name(f"{path.stem}_{self.user_id}_Encryption.pdf")
            if self.process_pdf(str(path), str(out), "ENCRYPT"):
                try:
                    path.unlink()
                    client_log(f"source removed after encryption: {path}")
                except Exception:
                    client_log(f"failed to remove source after encryption: {path}\n{traceback.format_exc()}")
                self.server_log("ENCRYPT", path.name)

    def menu_decrypt(self, icon):
        pattern = f"_{self.user_id}_Encryption.pdf"
        targets = [f for f in os.listdir('.') if pattern in f]
        for f in targets:
            out = f.replace(pattern, "_Decrypted.pdf")
            if self.process_pdf(f, out, "DECRYPT"):
                os.remove(f)
                # send detailed info about the machine that decrypted/viewed the file
                self.server_log("DECRYPT", f)

    def launch_secure_view_process(self, encrypted_pdf, fallback_original=False):
        if not encrypted_pdf:
            return False
        encrypted_pdf = str(Path(encrypted_pdf).resolve())
        if not self.is_drm_view_candidate(encrypted_pdf):
            client_log(f"secure view launch rejected: unsupported file for current user: {encrypted_pdf}")
            if fallback_original and open_with_original_pdf_viewer(encrypted_pdf):
                return True
            client_log("secure view launch rejected without popup")
            return False
        pythonw = sys.executable.replace("python.exe", "pythonw.exe")
        subprocess.Popen([
            pythonw,
            os.path.abspath(__file__),
            "--agent-view",
            encrypted_pdf,
            self.user_id,
            str(self.drm_key),
            self.role,
            self.dept
        ], cwd=os.getcwd(), **hidden_subprocess_kwargs())
        client_log(f"secure viewer process launched: {encrypted_pdf}")
        return True

    def is_user_encrypted_file(self, path):
        path = Path(path)
        return (
            path.is_file()
            and f"_{self.user_id}_Encryption" in path.name
            and path.suffix.lower() == ".pdf"
        )

    def is_drm_view_candidate(self, path):
        path = Path(path)
        if not path.is_file() or path.suffix.lower() != ".pdf":
            return False
        if "_Encryption" in path.name:
            return True
        metadata = self.read_drm_metadata(str(path))
        return bool(metadata.get("file_id"))

    def resolve_view_key(self, encrypted_pdf, metadata):
        creator_agent_id = metadata.get("creator_agent_id") or self.user_id
        if creator_agent_id == self.user_id:
            return self.drm_key
        try:
            response = requests.post(f"{AUTH_SERVER_URL}/document_view_key", json={
                "requester_agent_id": self.user_id,
                "creator_agent_id": creator_agent_id,
                "file_id": metadata.get("file_id"),
                "filename": os.path.basename(encrypted_pdf)
            }, timeout=4)
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    return int(data.get("drm_key"))
            client_log(f"view key request failed: status={response.status_code}, body={response.text[:300]}")
        except Exception:
            client_log(f"view key request exception\n{traceback.format_exc()}")
        return self.drm_key

    def secure_view_pdf(self, encrypted_pdf, show_warning=True, fallback_original=False):
        if not encrypted_pdf:
            return
        encrypted_pdf = str(Path(encrypted_pdf).resolve())
        if not self.is_drm_view_candidate(encrypted_pdf):
            client_log(f"secure view rejected: unsupported file for current user: {encrypted_pdf}")
            if fallback_original and open_with_original_pdf_viewer(encrypted_pdf):
                return
            client_log("secure view rejected without popup")
            return
        metadata = self.read_drm_metadata(encrypted_pdf)
        file_id = metadata.get("file_id") or self.build_file_id(encrypted_pdf)
        view_key = self.resolve_view_key(encrypted_pdf, metadata)
        session_id = uuid.uuid4().hex[:12]
        payload = {
            "file_id": file_id,
            "creator_agent_id": metadata.get("creator_agent_id"),
            "viewer_agent_id": self.user_id,
            "current_path": os.path.abspath(encrypted_pdf),
            "view_mode": "agent_secure_memory_page",
            "view_session_id": session_id,
            "temporary_plaintext": False,
            "temporary_path": None,
            "cleanup_policy": "page_decrypted_in_memory_only"
        }
        self.server_log("VIEW", os.path.basename(encrypted_pdf), payload)
        client_log(f"secure view start: session={session_id}, source={encrypted_pdf}, mode=memory_page")
        try:
            self.open_agent_pdf_viewer(encrypted_pdf, file_id, session_id, view_key)
        except Exception:
            client_log(f"secure viewer failed\n{traceback.format_exc()}")
            return

    def render_decrypted_page_image(self, encrypted_pdf, page_index, zoom=1.35, view_key=None):
        try:
            import fitz
        except Exception:
            raise RuntimeError("PyMuPDF is required. Install with: pip install pymupdf")

        reader = PdfReader(encrypted_pdf)
        writer = PdfWriter()
        page = reader.pages[page_index]
        xor_key = (self.drm_key if view_key is None else int(view_key)) & 0xFF
        for content_stream in self.iter_content_streams(page):
            self.xor_content_stream(content_stream, xor_key)
        if "/Annots" in page:
            del page[NameObject("/Annots")]
        if "/AA" in page:
            del page[NameObject("/AA")]
        writer.add_page(page)
        pdf_bytes = io.BytesIO()
        writer.write(pdf_bytes)
        pdf_bytes.seek(0)

        doc = fitz.open(stream=pdf_bytes.getvalue(), filetype="pdf")
        try:
            rendered_page = doc.load_page(0)
            pix = rendered_page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        finally:
            doc.close()

    def open_agent_pdf_viewer(self, encrypted_pdf, file_id, session_id, view_key=None):
        try:
            import fitz
            import tkinter as tk
            from PIL import ImageTk
        except Exception:
            client_log("secure viewer dependency missing: install pymupdf for Agent viewer")
            show_agent_message("Agent 전용 뷰어에 필요한 PyMuPDF가 설치되어 있지 않습니다.\n명령어: pip install pymupdf", error=True)
            return

        try:
            page_count = len(PdfReader(encrypted_pdf).pages)
        except Exception:
            client_log(f"secure viewer pdf read failed: {encrypted_pdf}\n{traceback.format_exc()}")
            return

        viewer = tk.Tk()
        viewer.title(f"DRM Agent Viewer - {Path(encrypted_pdf).name}")
        viewer.geometry("980x820")
        viewer.minsize(720, 520)
        viewer.attributes("-topmost", True)
        state = {"page": 0, "photo": None, "zoom": 1.35}

        toolbar = tk.Frame(viewer)
        toolbar.pack(fill="x")
        prev_btn = tk.Button(toolbar, text="이전", width=10)
        next_btn = tk.Button(toolbar, text="다음", width=10)
        zoom_out_btn = tk.Button(toolbar, text="-", width=4)
        zoom_in_btn = tk.Button(toolbar, text="+", width=4)
        zoom_reset_btn = tk.Button(toolbar, text="100%", width=6)
        zoom_label = tk.Label(toolbar, text="")
        page_label = tk.Label(toolbar, text="")
        close_btn = tk.Button(toolbar, text="닫기", width=10)
        prev_btn.pack(side="left", padx=6, pady=6)
        next_btn.pack(side="left", padx=6, pady=6)
        zoom_out_btn.pack(side="left", padx=(16, 4), pady=6)
        zoom_in_btn.pack(side="left", padx=4, pady=6)
        zoom_reset_btn.pack(side="left", padx=4, pady=6)
        zoom_label.pack(side="left", padx=8)
        page_label.pack(side="left", padx=12)
        close_btn.pack(side="right", padx=6, pady=6)

        frame = tk.Frame(viewer)
        frame.pack(fill="both", expand=True)
        canvas = tk.Canvas(frame, background="#404040")
        vbar = tk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        hbar = tk.Scrollbar(frame, orient="horizontal", command=canvas.xview)
        canvas.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)
        vbar.pack(side="right", fill="y")
        hbar.pack(side="bottom", fill="x")
        canvas.pack(side="left", fill="both", expand=True)

        def render_page():
            page_label.configure(text=f"렌더링 중... {state['page'] + 1} / {page_count}")
            viewer.update_idletasks()
            xview = canvas.xview()[0] if canvas.xview() else 0
            yview = canvas.yview()[0] if canvas.yview() else 0
            image = self.render_decrypted_page_image(encrypted_pdf, state["page"], state["zoom"], view_key)
            state["photo"] = ImageTk.PhotoImage(image, master=viewer)
            canvas.delete("all")
            canvas.create_image(12, 12, image=state["photo"], anchor="nw")
            canvas.configure(scrollregion=(0, 0, image.width + 24, image.height + 24))
            page_label.configure(text=f"{state['page'] + 1} / {page_count}")
            zoom_label.configure(text=f"{int(state['zoom'] * 100)}%")
            prev_btn.configure(state="normal" if state["page"] > 0 else "disabled")
            next_btn.configure(state="normal" if state["page"] < page_count - 1 else "disabled")
            zoom_out_btn.configure(state="normal" if state["zoom"] > 0.55 else "disabled")
            zoom_in_btn.configure(state="normal" if state["zoom"] < 3.95 else "disabled")
            canvas.xview_moveto(xview)
            canvas.yview_moveto(yview)

        def prev_page():
            if state["page"] > 0:
                state["page"] -= 1
                render_page()

        def next_page():
            if state["page"] < page_count - 1:
                state["page"] += 1
                render_page()

        def set_zoom(value):
            value = max(0.5, min(4.0, value))
            if abs(value - state["zoom"]) < 0.01:
                return
            state["zoom"] = value
            render_page()

        def zoom_in():
            set_zoom(state["zoom"] + 0.2)

        def zoom_out():
            set_zoom(state["zoom"] - 0.2)

        def zoom_reset():
            set_zoom(1.35)

        def handle_mouse_wheel(event):
            ctrl_pressed = bool(event.state & 0x0004)
            shift_pressed = bool(event.state & 0x0001)
            direction = -1 if event.delta > 0 else 1
            if ctrl_pressed:
                if event.delta > 0:
                    zoom_in()
                else:
                    zoom_out()
            elif shift_pressed:
                canvas.xview_scroll(direction * 3, "units")
            else:
                canvas.yview_scroll(direction * 3, "units")
            return "break"

        def handle_linux_wheel_up(event):
            canvas.yview_scroll(-3, "units")
            return "break"

        def handle_linux_wheel_down(event):
            canvas.yview_scroll(3, "units")
            return "break"

        def start_drag(event):
            canvas.scan_mark(event.x, event.y)
            canvas.configure(cursor="fleur")

        def drag_view(event):
            canvas.scan_dragto(event.x, event.y, gain=1)

        def end_drag(event):
            canvas.configure(cursor="")

        def close_viewer():
            viewer.destroy()
            client_log(f"secure view closed: session={session_id}, source={encrypted_pdf}")
            self.server_log("VIEW_CLOSED", os.path.basename(encrypted_pdf), {
                "file_id": file_id,
                "view_session_id": session_id,
                "view_mode": "agent_secure_memory_page"
            })

        prev_btn.configure(command=prev_page)
        next_btn.configure(command=next_page)
        zoom_out_btn.configure(command=zoom_out)
        zoom_in_btn.configure(command=zoom_in)
        zoom_reset_btn.configure(command=zoom_reset)
        close_btn.configure(command=close_viewer)
        canvas.bind("<MouseWheel>", handle_mouse_wheel)
        canvas.bind("<Button-4>", handle_linux_wheel_up)
        canvas.bind("<Button-5>", handle_linux_wheel_down)
        canvas.bind("<ButtonPress-1>", start_drag)
        canvas.bind("<B1-Motion>", drag_view)
        canvas.bind("<ButtonRelease-1>", end_drag)
        viewer.bind("<Control-plus>", lambda event: zoom_in())
        viewer.bind("<Control-equal>", lambda event: zoom_in())
        viewer.bind("<Control-minus>", lambda event: zoom_out())
        viewer.bind("<Control-0>", lambda event: zoom_reset())
        viewer.protocol("WM_DELETE_WINDOW", close_viewer)
        render_page()
        viewer.mainloop()

    def menu_install_pdf_handler(self, icon=None, item=None):
        ok, message = install_pdf_double_click_handler()
        client_log(f"pdf double-click handler install: ok={ok}, message={message}")
        self.notify_status("DRM Agent", message if ok else f"PDF 더블클릭 연동 설치 실패: {message}")

    def menu_uninstall_pdf_handler(self, icon=None, item=None):
        ok, message = uninstall_pdf_double_click_handler()
        client_log(f"pdf double-click handler uninstall: ok={ok}, message={message}")
        self.notify_status("DRM Agent", message if ok else f"PDF 더블클릭 연동 해제 실패: {message}")

    def menu_open_default_apps_settings(self, icon=None, item=None):
        try:
            if os.name == "nt":
                os.startfile("ms-settings:defaultapps")
                client_log("opened Windows default apps settings")
            else:
                client_log("default apps settings is only supported on Windows")
        except Exception:
            client_log(f"open default apps settings failed\n{traceback.format_exc()}")

    def notify_status(self, title, message):
        try:
            if self.icon:
                self.icon.notify(str(message), str(title))
                return
        except Exception:
            pass
        client_log(f"{title}: {message}")

    def logout_and_exit(self, icon):
        try:
            requests.post(f"{AUTH_SERVER_URL}/logout", json={"user_id": self.user_id}, timeout=2)
        except: pass
        if self.lock_socket:
            self.lock_socket.close()
        icon.stop()
        os._exit(0)

    def run_tray(self):
        img = Image.new('RGB', (64, 64), (255, 255, 255))
        d = ImageDraw.Draw(img)
        d.ellipse((16, 16, 48, 48), fill=(0, 120, 215))

        menu_items = [MenuItem(f"사용자: {self.user_id} / {self.dept}", lambda: None, enabled=False)]
        menu_items.append(MenuItem("Windows 기본 PDF 앱 설정 열기", self.menu_open_default_apps_settings))
        if self.role == "admin":
            menu_items.append(MenuItem("전체 복호화", self.menu_decrypt))
        menu_items.append(MenuItem("로그아웃 및 종료", self.logout_and_exit))

        self.icon = Icon("DRM", img, "DRM Agent", Menu(*menu_items))
        self.icon.run()

if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--install-pdf-handler":
        ok, message = install_pdf_double_click_handler()
        client_log(f"cli install pdf handler: ok={ok}, message={message}")
        print(message if ok else f"PDF handler install failed: {message}")
        sys.exit(0 if ok else 1)
    elif len(sys.argv) >= 2 and sys.argv[1] == "--uninstall-pdf-handler":
        ok, message = uninstall_pdf_double_click_handler()
        client_log(f"cli uninstall pdf handler: ok={ok}, message={message}")
        print(message if ok else f"PDF handler uninstall failed: {message}")
        sys.exit(0 if ok else 1)
    elif len(sys.argv) >= 2 and sys.argv[1] == "--open-default-apps":
        try:
            os.startfile("ms-settings:defaultapps")
            client_log("cli opened Windows default apps settings")
            sys.exit(0)
        except Exception as e:
            client_log(f"cli open default apps failed\n{traceback.format_exc()}")
            print(f"Open default apps settings failed: {e}")
            sys.exit(1)
    elif len(sys.argv) >= 7 and sys.argv[1] == "--agent-view":
        target_pdf = sys.argv[2]
        agent = FinalDRMAgent(sys.argv[3], int(sys.argv[4]), sys.argv[5], sys.argv[6])
        client_log(f"secure viewer process starting: user={sys.argv[3]}, target={target_pdf}")
        agent.secure_view_pdf(target_pdf, True, False)
        sys.exit(0)
    elif len(sys.argv) >= 3 and sys.argv[1] == "--agent-open":
        target_pdf = sys.argv[2]
        result = send_local_agent_command({"action": "secure_view", "path": target_pdf})
        if not result.get("success"):
            client_log(f"agent-open failed, fallback original viewer: {target_pdf}, result={result}")
            if not open_with_original_pdf_viewer(target_pdf):
                show_agent_message("로그인된 DRM Agent가 실행 중이 아니어서 전용 열람을 시작할 수 없습니다.", error=True)
        sys.exit(0)
    elif len(sys.argv) > 4:
        client_log(f"background agent starting: cwd={Path.cwd()}, argv_user={sys.argv[1]}")
        agent = FinalDRMAgent(sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4])
        # 백그라운드 프로세스에서도 락을 겁니다.
        if not agent.hold_lock():
            sys.exit(0)
        agent.auto_encrypt()
        agent.start_command_server()
        agent.start_background_tasks()
        agent.run_tray()
    else:
        # 중복 체크: 이미 프로그램이 실행 중인지 확인
        temp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            temp_sock.bind(('127.0.0.1', LOCK_PORT))
            temp_sock.close()
        except socket.error:
            show_agent_message("이미 DRM 에이전트가 다른 계정으로 실행 중입니다.", warning=True)
            sys.exit(0)

        uid, upw = prompt_login_gui()
        if not uid or not upw:
            client_log("login cancelled")
            sys.exit(0)
        try:
            resp = requests.post(f"{AUTH_SERVER_URL}/auth", json={"id": uid, "pw": upw}, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                client_log(f"login success: user={data['user_id']}, launch_cwd={Path.cwd()}, script={Path(__file__).resolve()}")
                subprocess.Popen([
                    sys.executable.replace("python.exe", "pythonw.exe"),
                    os.path.abspath(__file__), data['user_id'], str(data['drm_key']), data['role']
                    , data['dept']
                ], cwd=os.getcwd(), creationflags=subprocess.CREATE_NO_WINDOW)
                sys.exit(0)
            else:
                client_log(f"login failed: status={resp.status_code}, user={uid}")
                show_agent_message("아이디 또는 비밀번호가 틀렸습니다.", warning=True)
        except Exception as e:
            client_log(f"login/server error: {e}\n{traceback.format_exc()}")
            show_agent_message(f"서버 연결 실패: {e}", error=True)
