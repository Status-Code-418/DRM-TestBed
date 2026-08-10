from flask import Flask, request, jsonify
import logging
import requests
import json
import hashlib
import os
import re
import socket
import struct
import threading
import uuid
from datetime import datetime, timedelta

app = Flask(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SERVER_CONFIG_FILE = os.path.join(BASE_DIR, "server_config.json")

def load_server_config():
    if not os.path.exists(SERVER_CONFIG_FILE):
        raise FileNotFoundError(f"{SERVER_CONFIG_FILE} 파일을 찾을 수 없습니다.")
    try:
        with open(SERVER_CONFIG_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            return loaded
    except Exception:
        pass
    raise ValueError(f"{SERVER_CONFIG_FILE} 파일을 읽을 수 없습니다.")

SERVER_CONFIG = load_server_config()
EVENTS_FILE = str(SERVER_CONFIG["events_file"])
ARTIFACTS_DIR = str(SERVER_CONFIG["artifacts_dir"])
ARTIFACT_SOCKET_HOST = str(SERVER_CONFIG["artifact_socket_host"])
ARTIFACT_SOCKET_PORT = int(SERVER_CONFIG["artifact_socket_port"])
MAX_ARTIFACT_BYTES = int(SERVER_CONFIG["max_artifact_bytes"])
FLASK_HOST = str(SERVER_CONFIG["flask_host"])
FLASK_PORT = int(SERVER_CONFIG["flask_port"])
os.makedirs(ARTIFACTS_DIR, exist_ok=True)

# 로그 설정: 한글 깨짐 방지 및 콘솔/파일 동시 기록
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(message)s',
    handlers=[
        logging.FileHandler("drm_server.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)

def get_geo_info(ip):
    """IP 주소를 기반으로 ASN 및 위치 정보를 조회합니다."""
    if not ip or ip == "127.0.0.1":
        return {}
    try:
        # ASN(as) 및 ISP 정보를 포함하여 조회
        fields = "status,message,country,regionName,city,lat,lon,zip,as,isp,query"
        r = requests.get(f"http://ip-api.com/json/{ip}?fields={fields}", timeout=3)
        if r.status_code == 200:
            data = r.json()
            return {k: data.get(k) for k in ("country","regionName","city","lat","lon","zip", "as", "isp")}
    except Exception:
        pass
    return {}

# 부서(dept) 정보 추가
USER_DATABASE = SERVER_CONFIG["users"]
PRESERVATION_TARGET_ACTIONS = {
    "CREATE",
    "LOCATION_SEEN",
    "MOVE",
    "DECRYPT",
    "ENCRYPT",
    "VIEW",
    "OPEN",
    "PDF_OPEN"
}
ACTION_TO_RELATED_ROLE = {
    "CREATE": "creator",
    "ENCRYPT": "creator",
    "LOCATION_SEEN": "holder_current_or_past",
    "MOVE": "distributor",
    "DECRYPT": "decryptor",
    "VIEW": "internal_viewer",
    "OPEN": "internal_viewer",
    "PDF_OPEN": "internal_viewer",
    "pdf_open": "internal_viewer",
    "PRESERVATION_COLLECTED": "preservation_collector"
}

@app.route('/auth', methods=['POST'])
def authenticate():
    data = request.json
    uid = data.get("id", "Unknown")
    upw = data.get("pw", "")
    
    if uid in USER_DATABASE and USER_DATABASE[uid]["pw"] == upw:
        logging.info(f"[LOGIN_SUCCESS] User: {uid} - Role: {USER_DATABASE[uid]['role']}")
        append_event({
            "type": "login",
            "user_id": uid,
            "result": "success",
            "role": USER_DATABASE[uid]["role"],
            "dept": USER_DATABASE[uid]["dept"],
            "remote_addr": request.remote_addr,
            "geo": get_geo_info(request.remote_addr),
            "user_agent": request.headers.get("User-Agent")
        })
        return jsonify({
            "success": True, "user_id": uid, 
            "drm_key": USER_DATABASE[uid]["key"], "role": USER_DATABASE[uid]["role"],
            "dept": USER_DATABASE[uid]["dept"]
        }), 200
    else:
        reason = "Invalid Password" if uid in USER_DATABASE else "User Not Found"
        logging.warning(f"[LOGIN_FAILED] Attempted ID: {uid} - Reason: {reason}")
        append_event({
            "type": "login",
            "user_id": uid,
            "result": "failed",
            "reason": reason,
            "remote_addr": request.remote_addr,
            "geo": get_geo_info(request.remote_addr),
            "user_agent": request.headers.get("User-Agent")
        })
        return jsonify({"success": False}), 401

@app.route('/logout', methods=['POST'])
def logout():
    data = request.json
    uid = data.get("user_id", "Unknown")
    logging.info(f"[LOGOUT] User: {uid} - Session Ended")
    append_event({
        "type": "logout",
        "user_id": uid,
        "remote_addr": request.remote_addr,
        "geo": get_geo_info(request.remote_addr),
        "user_agent": request.headers.get("User-Agent")
    })
    return jsonify({"success": True}), 200

@app.route('/document_view_key', methods=['POST'])
def document_view_key():
    data = request.json or {}
    requester_id = data.get("requester_agent_id", "")
    creator_id = data.get("creator_agent_id", "")
    file_id = data.get("file_id", "")
    filename = data.get("filename", "")

    if requester_id not in USER_DATABASE:
        return jsonify({"success": False, "error": "unknown requester"}), 403
    if creator_id not in USER_DATABASE:
        return jsonify({"success": False, "error": "unknown creator"}), 404

    append_event({
        "type": "document_view_key",
        "action": "VIEW_KEY_ISSUED",
        "requester_agent_id": requester_id,
        "creator_agent_id": creator_id,
        "file_id": file_id,
        "filename": filename,
        "remote_addr": request.remote_addr,
        "geo": get_geo_info(request.remote_addr),
        "user_agent": request.headers.get("User-Agent")
    })
    logging.info(f"[VIEW_KEY_ISSUED] requester={requester_id}, creator={creator_id}, file={filename}")
    return jsonify({
        "success": True,
        "creator_agent_id": creator_id,
        "drm_key": USER_DATABASE[creator_id]["key"]
    }), 200

def load_events():
    try:
        if os.path.exists(EVENTS_FILE):
            with open(EVENTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return []

def append_event(event: dict):
    event.setdefault("event_id", str(uuid.uuid4()))
    event.setdefault("timestamp", datetime.utcnow().isoformat() + "Z")
    data = load_events()
    data.append(event)
    try:
        with open(EVENTS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Failed to write event file: {e}")


def safe_name(value, default="unknown"):
    value = str(value or default)
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value)
    return value[:120] or default


def recv_exact(conn, size):
    chunks = []
    remaining = size
    while remaining > 0:
        chunk = conn.recv(min(65536, remaining))
        if not chunk:
            raise ConnectionError("socket closed while receiving artifact data")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def receive_artifact_bundle(conn, addr):
    header_len = struct.unpack("!Q", recv_exact(conn, 8))[0]
    if header_len > 65536:
        raise ValueError("artifact header is too large")
    header = json.loads(recv_exact(conn, header_len).decode("utf-8"))
    bundle_size = struct.unpack("!Q", recv_exact(conn, 8))[0]
    if bundle_size > MAX_ARTIFACT_BYTES:
        raise ValueError(f"artifact bundle is too large: {bundle_size}")

    file_id = safe_name(header.get("file_id"))
    agent_id = safe_name(header.get("agent_id"))
    task_id = safe_name(header.get("task_id"))
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    target_dir = os.path.join(ARTIFACTS_DIR, file_id, agent_id)
    os.makedirs(target_dir, exist_ok=True)
    filename = f"{timestamp}_{task_id}_artifact_bundle.zip"
    artifact_path = os.path.join(target_dir, filename)

    remaining = bundle_size
    digest = hashlib.sha256()
    with open(artifact_path, "wb") as f:
        while remaining > 0:
            chunk = conn.recv(min(65536, remaining))
            if not chunk:
                raise ConnectionError("socket closed while writing artifact bundle")
            digest.update(chunk)
            f.write(chunk)
            remaining -= len(chunk)

    received_sha256 = digest.hexdigest()
    expected_sha256 = header.get("sha256")
    verified = not expected_sha256 or expected_sha256 == received_sha256
    record = {
        "type": "artifact_received",
        "file_id": header.get("file_id"),
        "agent_id": header.get("agent_id"),
        "task_id": header.get("task_id"),
        "incident_event_id": header.get("incident_event_id"),
        "hostname": header.get("hostname"),
        "remote_addr": addr[0],
        "artifact_path": artifact_path,
        "artifact_filename": filename,
        "artifact_size": bundle_size,
        "sha256": received_sha256,
        "sha256_verified": verified,
        "artifact_kinds": header.get("artifact_kinds", [])
    }
    append_event(record)
    conn.sendall(json.dumps({"success": True, "artifact_path": artifact_path, "sha256_verified": verified}).encode("utf-8"))
    logging.info(f"[ARTIFACT_RECEIVED] Agent: {header.get('agent_id')} | FileID: {header.get('file_id')} | Path: {artifact_path}")


def artifact_socket_worker(conn, addr):
    with conn:
        try:
            receive_artifact_bundle(conn, addr)
        except Exception as e:
            logging.error(f"[ARTIFACT_RECEIVE_FAILED] From: {addr[0]} | Error: {e}")
            try:
                conn.sendall(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))
            except Exception:
                pass


def start_artifact_socket_server():
    def run_server():
        try:
            server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_sock.bind((ARTIFACT_SOCKET_HOST, ARTIFACT_SOCKET_PORT))
            server_sock.listen(20)
            logging.info(f"[ARTIFACT_SOCKET] Listening on {ARTIFACT_SOCKET_HOST}:{ARTIFACT_SOCKET_PORT}")
            while True:
                conn, addr = server_sock.accept()
                threading.Thread(target=artifact_socket_worker, args=(conn, addr), daemon=True).start()
        except Exception as e:
            logging.error(f"[ARTIFACT_SOCKET_FAILED] {e}")

    threading.Thread(target=run_server, daemon=True).start()

@app.route('/log_event', methods=['POST'])
def log_event():
    data = request.json
    user_id = data.get('user_id')
    action = data.get('action')
    filename = data.get('filename')
    
    # 클라이언트에서 보낸 메타 데이터를 추출합니다.
    meta_from_client = {k:v for k,v in data.items() if k not in ['user_id','action','filename']}
    if "artifacts" in meta_from_client:
        meta_from_client["artifacts_removed"] = True
        meta_from_client.pop("artifacts", None)
    
    # 클라이언트에서 보낸 geo 정보가 유효하지 않으면 (예: 사설 IP 기반으로 null), 서버의 remote_addr로 다시 조회합니다.
    client_geo = meta_from_client.get('geo')
    if not client_geo or all(v is None for v in client_geo.values()):
        server_geo_info = get_geo_info(request.remote_addr)
        if server_geo_info:
            meta_from_client['geo'] = server_geo_info
            
    logging.info(f"[EVENT] User: {user_id} | Action: {action} | File: {filename} | Extra: {meta_from_client}")
    append_event({
        "type": "event",
        "user_id": user_id,
        "action": action,
        "filename": filename,
        "meta": meta_from_client, # 업데이트된 meta_from_client 사용
        "remote_addr": request.remote_addr
    })
    return jsonify({"success": True}), 200


def is_external_pdf_open(opener_agent_id):
    return not opener_agent_id or opener_agent_id not in USER_DATABASE


def get_event_agent_id(event):
    meta = event.get("meta") or {}
    return (
        event.get("opener_agent_id")
        or event.get("creator_agent_id")
        or meta.get("agent_id")
        or meta.get("creator_agent_id")
        or event.get("user_id")
    )


def get_related_agents(file_id, events):
    related = {}
    for event in events:
        if get_event_file_id(event) != file_id:
            continue
        agent_id = get_event_agent_id(event)
        if not agent_id or agent_id == "external/unknown":
            continue
        meta = event.get("meta") or {}
        current = related.setdefault(agent_id, {
            "agent_id": agent_id,
            "dept": meta.get("dept") or USER_DATABASE.get(agent_id, {}).get("dept"),
            "hostname": meta.get("hostname") or meta.get("creator_host"),
            "ip": meta.get("ip"),
            "mac": meta.get("mac"),
            "actions": [],
            "first_seen": event.get("timestamp"),
            "last_seen": event.get("timestamp")
        })
        action = event.get("action") or event.get("type")
        if action and action not in current["actions"]:
            current["actions"].append(action)
        if event.get("timestamp"):
            current["first_seen"] = min(current["first_seen"], event.get("timestamp"))
            current["last_seen"] = max(current["last_seen"], event.get("timestamp"))
        for key in ("dept", "hostname", "ip", "mac"):
            if not current.get(key) and meta.get(key):
                current[key] = meta.get(key)
    return sorted(related.values(), key=lambda item: item.get("first_seen") or "")


def get_preservation_targets(file_id, events):
    targets = {}
    for event in events:
        if get_event_file_id(event) != file_id:
            continue
        meta = event.get("meta") or {}
        event_type = event.get("type")
        action = event.get("action") or event_type
        if event_type == "pdf_open" and is_external_pdf_open(event.get("opener_agent_id")):
            continue
        if event_type == "event" and action not in PRESERVATION_TARGET_ACTIONS:
            continue
        if event_type not in ("event", "pdf_open"):
            continue
        agent_id = get_event_agent_id(event)
        if not agent_id and action in ("CREATE", "ENCRYPT"):
            agent_id = meta.get("creator_agent_id") or event.get("creator_agent_id")
        if not agent_id or agent_id not in USER_DATABASE:
            continue
        related_role = ACTION_TO_RELATED_ROLE.get(action, "related_agent")
        current = targets.setdefault(agent_id, {
            "agent_id": agent_id,
            "dept": meta.get("dept") or USER_DATABASE[agent_id].get("dept"),
            "hostname": meta.get("hostname") or meta.get("creator_host"),
            "ip": meta.get("ip"),
            "mac": meta.get("mac"),
            "related_roles": [],
            "evidence_actions": [],
            "last_known_paths": [],
            "first_seen": event.get("timestamp"),
            "last_seen": event.get("timestamp")
        })
        if related_role not in current["related_roles"]:
            current["related_roles"].append(related_role)
        if action not in current["evidence_actions"]:
            current["evidence_actions"].append(action)
        current_path = meta.get("current_path")
        if current_path and current_path not in current["last_known_paths"]:
            current["last_known_paths"].append(current_path)
        if event.get("timestamp"):
            current["first_seen"] = min(current["first_seen"], event.get("timestamp"))
            current["last_seen"] = max(current["last_seen"], event.get("timestamp"))
        for key in ("dept", "hostname", "ip", "mac"):
            if not current.get(key) and meta.get(key):
                current[key] = meta.get(key)
    return sorted(targets.values(), key=lambda item: item.get("last_seen") or "", reverse=True)


def queue_preservation_requests(file_id, incident_event, events):
    existing_tasks = {
        (e.get("file_id"), e.get("target_agent_id"), e.get("incident_event_id"))
        for e in events
        if e.get("type") == "preservation_request"
    }
    for agent in get_preservation_targets(file_id, events):
        key = (file_id, agent["agent_id"], incident_event.get("event_id"))
        if key in existing_tasks:
            continue
        append_event({
            "type": "preservation_request",
            "file_id": file_id,
            "target_agent_id": agent["agent_id"],
            "reason": "external_pdf_open_detected_on_previously_observed_internal_pc",
            "incident_event_id": incident_event.get("event_id"),
            "status": "pending",
            "target_evidence": {
                "related_roles": agent.get("related_roles", []),
                "evidence_actions": agent.get("evidence_actions", []),
                "last_known_paths": agent.get("last_known_paths", []),
                "hostname": agent.get("hostname"),
                "ip": agent.get("ip"),
                "mac": agent.get("mac"),
                "dept": agent.get("dept")
            }
        })


def build_forensic_timeline(file_id, events):
    timeline = []
    for event in events:
        if get_event_file_id(event) != file_id:
            continue
        meta = event.get("meta") or {}
        event_type = event.get("type")
        action = event.get("action")
        category = event_type
        title = event_type
        actor = get_event_agent_id(event) or "unknown"
        if event_type == "event":
            category = "internal_agent_event"
            title = action
        elif event_type == "pdf_open":
            category = "external_view" if is_external_pdf_open(event.get("opener_agent_id")) else "internal_view"
            title = "PDF_OPEN"
            actor = event.get("opener_agent_id") or "external/unknown"
        elif event_type == "incident":
            category = "incident_response"
            title = event.get("incident_type")
        elif event_type == "preservation_request":
            category = "preservation_request"
            title = "PRESERVATION_REQUESTED"
            actor = event.get("target_agent_id")
        elif event_type == "artifact_received":
            category = "artifact_collection"
            title = "ARTIFACT_RECEIVED"
            actor = event.get("agent_id")
        timeline.append({
            "timestamp": event.get("timestamp"),
            "category": category,
            "title": title,
            "actor": actor,
            "filename": event.get("filename"),
            "trigger": event.get("trigger"),
            "hostname": meta.get("hostname"),
            "ip": meta.get("ip") or event.get("remote_addr"),
            "geo": meta.get("geo") or event.get("geo"),
            "artifact_path": event.get("artifact_path"),
            "event_id": event.get("event_id")
        })
    return sorted(timeline, key=lambda item: item.get("timestamp") or "")


def parse_event_time(value):
    try:
        return datetime.fromisoformat(value.replace("Z", ""))
    except Exception:
        return None


def has_recent_external_incident(file_id, remote_addr, user_agent, events, window_seconds=60):
    now = datetime.utcnow()
    for event in reversed(events):
        if event.get("type") != "incident":
            continue
        if event.get("incident_type") != "EXTERNAL_PDF_OPEN_DETECTED":
            continue
        if event.get("file_id") != file_id:
            continue
        if event.get("remote_addr") != remote_addr or event.get("user_agent") != user_agent:
            continue
        event_time = parse_event_time(event.get("timestamp", ""))
        if event_time and now - event_time <= timedelta(seconds=window_seconds):
            return True
    return False


@app.route('/pdf_open', methods=['GET'])
def pdf_open():
    # Called when PDF JavaScript opens this URL on document open/view
    fname = request.args.get('file') or request.args.get('filename') or 'unknown'
    file_id = request.args.get('file_id')
    creator_agent_id = request.args.get('creator_agent_id')
    opener_agent_id = request.args.get('opener_agent_id')
    trigger = request.args.get('trigger') or 'unknown'
    
    geo_info = get_geo_info(request.remote_addr)
    logging.info(
        f"[PDF_OPEN] File: {fname} | Creator: {creator_agent_id} | "
        f"Opener: {opener_agent_id or 'external/unknown'} | Trigger: {trigger} | "
        f"IP: {request.remote_addr} | ASN: {geo_info.get('as')}"
    )
    
    pdf_open_event = {
        "type": "pdf_open",
        "filename": fname,
        "file_id": file_id,
        "creator_agent_id": creator_agent_id,
        "opener_agent_id": opener_agent_id,
        "trigger": trigger,
        "remote_addr": request.remote_addr,
        "geo": geo_info,
        "user_agent": request.headers.get("User-Agent")
    }
    append_event(pdf_open_event)
    events_data = load_events()
    incident_created = False
    if (
        file_id
        and is_external_pdf_open(opener_agent_id)
        and not has_recent_external_incident(file_id, request.remote_addr, request.headers.get("User-Agent"), events_data)
    ):
        incident_event = {
            "type": "incident",
            "incident_type": "EXTERNAL_PDF_OPEN_DETECTED",
            "file_id": file_id,
            "filename": fname,
            "creator_agent_id": creator_agent_id,
            "trigger": trigger,
            "remote_addr": request.remote_addr,
            "geo": geo_info,
            "user_agent": request.headers.get("User-Agent"),
            "source_event_id": pdf_open_event.get("event_id"),
            "severity": "medium"
        }
        append_event(incident_event)
        queue_preservation_requests(file_id, incident_event, load_events())
        incident_created = True
    # Redirect to a small 1x1 gif or return a tiny image; for simplicity return JSON OK
    return jsonify({"success": True, "trigger": trigger, "incident_created": incident_created}), 200


def get_event_file_id(event):
    if event.get("file_id"):
        return event.get("file_id")
    meta = event.get("meta") or {}
    return meta.get("file_id") or meta.get("DRM_FileID")

def summarize_document(file_id, events):
    doc_events = [e for e in events if get_event_file_id(e) == file_id]
    creator = None
    distribution_path = []
    view_history = []
    login_history = []
    artifact_history = []
    incident_history = []
    preservation_requests = []
    for event in doc_events:
        meta = event.get("meta") or {}
        if not creator or event.get("action") == "CREATE":
            creator = {
                "agent_id": event.get("creator_agent_id") or meta.get("creator_agent_id") or meta.get("agent_id"),
                "dept": meta.get("dept") or "Unknown",
                "hostname": meta.get("hostname"),
                "os": meta.get("os_name") or meta.get("creator_os")
            }
        action = event.get("action")
        if action in ("CREATE", "LOCATION_SEEN", "MOVE"):
            distribution_path.append({
                "timestamp": event.get("timestamp"),
                "agent_id": meta.get("agent_id") or event.get("user_id"),
                "hostname": meta.get("hostname"),
                "ip": meta.get("ip"),
                "geo": meta.get("geo"),
                "mac": meta.get("mac"),
                "path": meta.get("current_path"),
                "filename": event.get("filename"),
                "remote_addr": event.get("remote_addr"),
                "dept": meta.get("dept")
            })
        if meta.get("artifacts"):
            artifact_history.append({
                "timestamp": event.get("timestamp"),
                "action": action,
                "agent_id": meta.get("agent_id") or event.get("user_id"),
                "hostname": meta.get("hostname"),
                "artifacts": meta.get("artifacts")
            })
        if event.get("type") == "artifact_received":
            artifact_history.append({
                "timestamp": event.get("timestamp"),
                "action": "ARTIFACT_RECEIVED",
                "agent_id": event.get("agent_id"),
                "hostname": event.get("hostname"),
                "artifact_path": event.get("artifact_path"),
                "artifact_size": event.get("artifact_size"),
                "sha256": event.get("sha256"),
                "sha256_verified": event.get("sha256_verified"),
                "artifact_kinds": event.get("artifact_kinds", [])
            })
        if event.get("type") == "pdf_open":
            view_history.append({
                "timestamp": event.get("timestamp"),
                "filename": event.get("filename"),
                "file_id": event.get("file_id"),
                "creator_agent_id": event.get("creator_agent_id"),
                "opener_agent_id": event.get("opener_agent_id") or "external/unknown",
                "trigger": event.get("trigger") or "unknown",
                "remote_addr": event.get("remote_addr"),
                "geo": event.get("geo"),
                "user_agent": event.get("user_agent")
            })
        if event.get("type") in ("login", "logout"):
            login_history.append(event)
        if event.get("type") == "incident":
            incident_history.append(event)
        if event.get("type") == "preservation_request":
            preservation_requests.append(event)
    return {
        "file_id": file_id,
        "creator_agent_id": creator,
        "distribution_path": distribution_path,
        "view_history": view_history,
        "artifact_history": artifact_history,
        "incident_history": incident_history,
        "preservation_requests": preservation_requests,
        "related_agents": get_related_agents(file_id, events),
        "preservation_targets": get_preservation_targets(file_id, events),
        "forensic_timeline": build_forensic_timeline(file_id, events),
        "login_history": login_history,
        "events": doc_events
    }

@app.route('/events', methods=['GET'])
def events():
    try:
        data = load_events()
        return jsonify({"success": True, "events": data}), 200
    except Exception as e:
        logging.error(f"Failed to read events: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/documents', methods=['GET'])
def documents():
    events_data = load_events()
    file_ids = sorted({fid for fid in (get_event_file_id(e) for e in events_data) if fid})
    return jsonify({
        "success": True,
        "documents": [summarize_document(file_id, events_data) for file_id in file_ids]
    }), 200

@app.route('/sessions', methods=['GET'])
def sessions():
    events_data = load_events()
    session_events = [e for e in events_data if e.get("type") in ("login", "logout")]
    return jsonify({"success": True, "sessions": session_events}), 200

@app.route('/incidents', methods=['GET'])
def incidents():
    events_data = load_events()
    incident_events = [e for e in events_data if e.get("type") == "incident"]
    return jsonify({"success": True, "incidents": incident_events}), 200

@app.route('/artifacts', methods=['GET'])
def artifacts():
    events_data = load_events()
    artifact_events = [e for e in events_data if e.get("type") == "artifact_received"]
    return jsonify({"success": True, "artifacts": artifact_events}), 200

@app.route('/timeline/<file_id>', methods=['GET'])
def timeline(file_id):
    events_data = load_events()
    document = summarize_document(file_id, events_data)
    external_views = [
        view for view in document["view_history"]
        if view.get("opener_agent_id") == "external/unknown"
    ]
    return jsonify({
        "success": True,
        "file_id": file_id,
        "incident_status": "external_open_detected" if external_views else "no_external_open_detected",
        "creator": document["creator_agent_id"],
        "internal_distribution": document["distribution_path"],
        "external_views": external_views,
        "related_agents": document["related_agents"],
        "preservation_targets": document["preservation_targets"],
        "preservation_requests": document["preservation_requests"],
        "preserved_artifacts": document["artifact_history"],
        "forensic_timeline": document["forensic_timeline"]
    }), 200

@app.route('/agent_tasks/<agent_id>', methods=['GET'])
def agent_tasks(agent_id):
    events_data = load_events()
    completed_task_ids = {
        (e.get("meta") or {}).get("task_id")
        for e in events_data
        if e.get("type") == "event" and e.get("action") == "PRESERVATION_COLLECTED"
    }
    tasks = [
        e for e in events_data
        if e.get("type") == "preservation_request"
        and e.get("target_agent_id") == agent_id
        and e.get("event_id") not in completed_task_ids
    ]
    return jsonify({"success": True, "tasks": tasks}), 200

@app.route('/document_history/<file_id>', methods=['GET'])
def document_history(file_id):
    events_data = load_events()
    return jsonify({"success": True, "document": summarize_document(file_id, events_data)}), 200

start_artifact_socket_server()

if __name__ == "__main__":
    app.run(host=FLASK_HOST, port=FLASK_PORT)
