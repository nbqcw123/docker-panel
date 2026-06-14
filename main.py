#!/usr/bin/env python3
"""Docker Management Panel - Backend (FastAPI)"""
import json
import subprocess
import asyncio
import re
import os
import socket
import ssl
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Docker Panel")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DOCKER_SOCKET = "/var/run/docker.sock"

def _detect_docker_bin() -> str:
    """Auto-detect docker binary path across different NAS systems."""
    candidates = [
        "docker",  # PATH-based (fnOS, standard Linux)
        "/usr/bin/docker",  # Debian/Ubuntu (fnOS)
        "/usr/local/bin/docker",  # Manual install
        "/volume1/@appstore/ContainerManager/usr/bin/docker",  # Synology
    ]
    for c in candidates:
        try:
            result = subprocess.run(
                [c, "version", "--format", "{{.Server.Version}}"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return c
        except Exception:
            continue
    return "docker"  # fallback

DOCKER_BIN = _detect_docker_bin()


def _detect_disk_targets() -> list:
    """Auto-detect disk mount points for different NAS systems."""
    targets = []
    try:
        result = subprocess.run(
            ["df", "-h", "--output=target,pcent"],
            capture_output=True, text=True, timeout=5
        )
        lines = result.stdout.strip().split("\n")[1:]  # skip header
        for line in lines:
            parts = line.split()
            if len(parts) >= 2:
                mount = parts[0]
                pct_str = parts[-1].replace("%", "")
                try:
                    pct = int(pct_str)
                except ValueError:
                    continue
                # Skip tmpfs, overlay, etc.
                if mount in ("/", "/boot", "/boot/efi") or \
                   mount.startswith("/volume") or \
                   mount.startswith("/mnt/") or \
                   mount.startswith("/srv/") or \
                   mount.startswith("/data") or \
                   mount.startswith("/home"):
                    targets.append(mount)
    except Exception:
        pass
    # Deduplicate and ensure / is first
    seen = set()
    unique = []
    for t in targets:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    # Sort: / first, then alphabetical
    unique.sort(key=lambda x: (0 if x == "/" else 1, x))
    return unique if unique else ["/"]


def docker_api(method: str, path: str, data: Optional[bytes] = None) -> dict:
    """Call Docker API via Unix socket, fallback to docker CLI."""
    # Try Unix socket first
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(15)
        sock.connect(DOCKER_SOCKET)

        body = data if data is not None else b""
        headers = (
            f"{method} {path} HTTP/1.1\r\n"
            f"Host: localhost\r\n"
            f"Accept: application/json\r\n"
        )
        if data:
            headers += f"Content-Type: application/json\r\n"
        headers += f"Content-Length: {len(body)}\r\n"
        headers += "Connection: close\r\n\r\n"

        sock.sendall(headers.encode() + body)

        response = b""
        while True:
            try:
                chunk = sock.recv(8192)
                if not chunk:
                    break
                response += chunk
            except socket.timeout:
                break
        sock.close()

        header_end = response.find(b"\r\n\r\n")
        if header_end == -1:
            raise Exception("Invalid HTTP response")

        header_part = response[:header_end].decode("utf-8", errors="replace")
        body_part = response[header_end + 4:]

        status_line = header_part.split("\r\n")[0]
        status_code = int(status_line.split(" ")[1])

        if "Transfer-Encoding: chunked" in header_part:
            body_part = _decode_chunked(body_part)

        if status_code >= 400:
            try:
                err_data = json.loads(body_part)
                return {"error": err_data.get("message", f"HTTP {status_code}"), "status_code": status_code}
            except:
                return {"error": f"HTTP {status_code}"}

        if not body_part:
            return {}

        try:
            return json.loads(body_part)
        except json.JSONDecodeError:
            return {"raw": body_part.decode("utf-8", errors="replace")}

    except (PermissionError, FileNotFoundError, OSError):
        # Socket access denied - fallback to docker CLI
        return _docker_cli_fallback(method, path, data)
    except Exception as e:
        return {"error": str(e)}


def _docker_cli_fallback(method: str, path: str, data: Optional[bytes] = None):
    """Fallback to docker CLI when socket is not accessible."""
    import re
    
    # Parse the API path to construct docker CLI commands
    # /containers/json?all=true -> docker ps -a --format json
    # /containers/{id}/start -> docker start {id}
    # /containers/{id}/stop -> docker stop {id}
    # /containers/{id}/restart -> docker restart {id}
    # /containers/{id}/stats?stream=false -> docker stats {id} --no-stream --format json
    
    try:
        if path.startswith("/containers/json"):
            all_flag = "all=true" in path
            # Use table format for speed, then parse
            cmd = [DOCKER_BIN, "ps", "-a" if all_flag else ""]
            cmd = [c for c in cmd if c]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if result.returncode != 0:
                return {"error": result.stderr.strip()}
            
            containers = []
            lines = result.stdout.strip().split("\n")
            if len(lines) > 1:
                for line in lines[1:]:  # Skip header
                    parts = line.split(None, 7)  # Split into columns
                    if len(parts) >= 7:
                        cid = parts[0]
                        image = parts[1]
                        # Parse status - look for "Up", "Exited", "Created" etc.
                        status_str = " ".join(parts[4:7])
                        state = "running" if status_str.startswith("Up") else "exited" if "Exited" in status_str else "created" if "Created" in status_str else "paused"
                        
                        # Parse ports from the line
                        ports_str = parts[6] if len(parts) > 6 else ""
                        ports = _parse_ports(ports_str)
                        
                        # Container name is typically in the last column
                        name_parts = line.split()
                        name = name_parts[-1] if name_parts else cid
                        
                        containers.append({
                            "Id": cid,
                            "Names": [name],
                            "Image": image,
                            "Status": status_str,
                            "State": state,
                            "Ports": ports,
                            "Labels": {},
                        })
            return containers
        
        elif path.startswith("/containers/") and path.endswith("/start"):
            container_id = path.split("/")[2]
            cmd = [DOCKER_BIN, "start", container_id]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                return {"error": result.stderr.strip()}
            return {"started": True}
        
        elif path.startswith("/containers/") and path.endswith("/stop"):
            container_id = path.split("/")[2]
            cmd = [DOCKER_BIN, "stop", container_id]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                return {"error": result.stderr.strip()}
            return {"stopped": True}
        
        elif path.startswith("/containers/") and path.endswith("/restart"):
            container_id = path.split("/")[2]
            cmd = [DOCKER_BIN, "restart", container_id]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                return {"error": result.stderr.strip()}
            return {"restarted": True}
        
        elif "/stats" in path:
            container_id = path.split("/")[2]
            cmd = [DOCKER_BIN, "stats", container_id, "--no-stream", "--format", "{{json .}}"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if result.returncode != 0:
                return {"error": result.stderr.strip()}
            try:
                stats = json.loads(result.stdout.strip())
                return {
                    "cpu_stats": {"cpu_usage": {"total_usage": 0}, "system_cpu_usage": 0, "online_cpus": 1},
                    "precpu_stats": {},
                    "memory_stats": {
                        "usage": int(stats.get("MemUsage", "0").split("/")[0].strip().replace("MiB", "000").replace("GiB", "000000")),
                        "limit": int(stats.get("MemUsage", "0").split("/")[1].strip().replace("MiB", "000").replace("GiB", "000000")),
                    },
                    "networks": {},
                }
            except:
                return {"memory_stats": {"usage": 0, "limit": 1}}
        
        else:
            return {"error": f"Unsupported API path: {path}"}
    
    except Exception as e:
        return {"error": str(e)}


def _parse_ports(ports_str: str) -> list:
    """Parse docker ps ports string into list of port dicts."""
    if not ports_str:
        return []
    ports = []
    for part in ports_str.split(","):
        part = part.strip()
        if "->" in part:
            host_part, container_part = part.split("->", 1)
            host_port = host_part.split(":")[-1].strip() if ":" in host_part else ""
            container_port = container_part.split("/")[0].strip() if "/" in container_part else container_part.strip()
            proto = container_part.split("/")[1].strip() if "/" in container_part else "tcp"
            if host_port:
                ports.append({
                    "PublicPort": int(host_port) if host_port.isdigit() else 0,
                    "PrivatePort": int(container_port) if container_port.isdigit() else 0,
                    "Type": proto,
                    "IP": "0.0.0.0",
                })
    return ports


def _decode_chunked(data: bytes) -> bytes:
    """Decode HTTP chunked transfer encoding."""
    result = b""
    pos = 0
    while pos < len(data):
        # Find chunk size
        end = data.find(b"\r\n", pos)
        if end == -1:
            break
        size_str = data[pos:end].split(b";")[0].strip()
        try:
            size = int(size_str, 16)
        except ValueError:
            break
        if size == 0:
            break
        result += data[end + 2:end + 2 + size]
        pos = end + 2 + size + 2  # skip \r\n after chunk
    return result


def docker_cmd(*args) -> dict:
    """Execute docker CLI command as fallback."""
    cmd = [DOCKER_BIN] + list(args)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return {"error": result.stderr.strip()}
        return {"output": result.stdout.strip()}
    except subprocess.TimeoutExpired:
        return {"error": "Command timed out"}
    except Exception as e:
        return {"error": str(e)}


def get_disk_usage() -> dict:
    """Get disk usage for auto-detected mount points."""
    try:
        targets = _detect_disk_targets()
        result = subprocess.run(
            ["df", "-h"] + targets,
            capture_output=True, text=True, timeout=5
        )
        lines = result.stdout.strip().split("\n")
        disks = {}
        for line in lines[1:]:
            parts = line.split()
            if len(parts) >= 6:
                mount = parts[5]
                disks[mount] = {
                    "size": parts[1],
                    "used": parts[2],
                    "available": parts[3],
                    "use_percent": parts[4].replace("%", ""),
                }
        return disks
    except Exception:
        return {}


def get_system_memory() -> dict:
    """Get system memory info from /proc/meminfo."""
    try:
        result = subprocess.run(
            ["cat", "/proc/meminfo"],
            capture_output=True, text=True, timeout=5
        )
        meminfo = {}
        for line in result.stdout.strip().split("\n"):
            match = re.match(r"(\w+):\s+(\d+)\s+kB", line)
            if match:
                meminfo[match.group(1)] = int(match.group(2))

        total = meminfo.get("MemTotal", 0)
        available = meminfo.get("MemAvailable", 0)

        return {
            "total_mb": round(total / 1024),
            "used_mb": round((total - available) / 1024),
            "available_mb": round(available / 1024),
            "use_percent": round((total - available) / total * 100, 1) if total > 0 else 0,
        }
    except Exception:
        return {}


@app.get("/api/containers")
async def list_containers():
    """List all containers."""
    loop = asyncio.get_event_loop()
    raw = await loop.run_in_executor(None, lambda: docker_api("GET", "/containers/json?all=true"))
    if isinstance(raw, dict) and "error" in raw:
        raise HTTPException(status_code=500, detail=raw["error"])

    containers = []
    for c in raw:
        container_id = c.get("Id", "")

        # Parse port mappings
        ports = []
        for p in c.get("Ports", []):
            port_info = {
                "container_port": p.get("PrivatePort"),
                "host_port": p.get("PublicPort"),
                "host_ip": p.get("IP", "0.0.0.0"),
                "type": p.get("Type", "tcp"),
            }
            ports.append(port_info)

        container_info = {
            "id": container_id[:12],
            "name": c.get("Names", [""])[0].lstrip("/"),
            "image": c.get("Image", ""),
            "status": c.get("Status", ""),
            "state": c.get("State", ""),
            "created": c.get("Created", 0),
            "ports": ports,
        }
        containers.append(container_info)

    return {"containers": containers, "count": len(containers)}


@app.get("/api/container/{container_id}/stats")
async def container_stats(container_id: str):
    """Get real-time stats for a container."""
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: docker_api("GET", f"/containers/{container_id}/stats?stream=false"))
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    # Parse CPU
    cpu_percent = 0
    try:
        cpu_stats = result.get("cpu_stats", {})
        precpu_stats = result.get("precpu_stats", {})

        cpu_usage = cpu_stats.get("cpu_usage", {})
        precpu_usage = precpu_stats.get("cpu_usage", {})

        cpu_delta = cpu_usage.get("total_usage", 0) - precpu_usage.get("total_usage", 0)
        system_delta = cpu_stats.get("system_cpu_usage", 0) - precpu_stats.get("system_cpu_usage", 0)
        num_cpus = cpu_stats.get("online_cpus", 1)

        if system_delta > 0 and cpu_delta > 0:
            cpu_percent = round((cpu_delta / system_delta) * num_cpus * 100, 2)
    except Exception:
        cpu_percent = 0

    # Parse memory
    mem_stats = result.get("memory_stats", {})
    mem_usage = mem_stats.get("usage", 0)
    mem_limit = mem_stats.get("limit", 1)
    mem_percent = round(mem_usage / mem_limit * 100, 2) if mem_limit > 0 else 0

    # Parse networks
    networks = result.get("networks", {})
    net_rx = sum(n.get("rx_bytes", 0) for n in networks.values())
    net_tx = sum(n.get("tx_bytes", 0) for n in networks.values())

    return {
        "id": container_id[:12],
        "cpu_percent": cpu_percent,
        "memory_usage": mem_usage,
        "memory_limit": mem_limit,
        "memory_usage_mb": round(mem_usage / 1024 / 1024, 1),
        "memory_limit_mb": round(mem_limit / 1024 / 1024, 1),
        "memory_percent": mem_percent,
        "network_rx": net_rx,
        "network_tx": net_tx,
    }


@app.get("/api/system")
async def system_info():
    """Get system memory, disk, and port info."""
    loop = asyncio.get_event_loop()
    # Get all containers for port summary
    raw = await loop.run_in_executor(None, lambda: docker_api("GET", "/containers/json?all=true"))
    ports = []
    if isinstance(raw, list):
        for c in raw:
            if c.get("State") != "running":
                continue
            name = c.get("Names", [""])[0].lstrip("/")
            for p in c.get("Ports", []):
                host_port = p.get("PublicPort")
                if host_port:
                    ports.append({
                        "host_port": host_port,
                        "container_port": p.get("PrivatePort"),
                        "protocol": p.get("Type", "tcp"),
                        "host_ip": p.get("IP", "0.0.0.0"),
                        "container_name": name,
                    })
    # Sort by host port
    ports.sort(key=lambda x: x["host_port"])
    # Deduplicate by host port (keep first)
    seen = set()
    unique_ports = []
    for p in ports:
        key = (p["host_port"], p["protocol"])
        if key not in seen:
            seen.add(key)
            unique_ports.append(p)
    return {
        "memory": get_system_memory(),
        "disk": get_disk_usage(),
        "ports": unique_ports,
        "ports_count": len(unique_ports),
    }


class ActionRequest(BaseModel):
    action: str  # start, stop, restart


@app.post("/api/container/{container_id}/action")
async def container_action(container_id: str, req: ActionRequest):
    """Start, stop, or restart a container."""
    action = req.action.lower()
    if action not in ("start", "stop", "restart"):
        raise HTTPException(status_code=400, detail=f"Invalid action: {action}")

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: docker_api("POST", f"/containers/{container_id}/{action}"))

    if isinstance(result, dict) and "error" in result:
        # 304 means already in desired state — not an error
        if result.get("status_code") == 304:
            return {"success": True, "action": action, "container_id": container_id[:12], "note": "already in desired state"}
        raise HTTPException(status_code=500, detail=result["error"])

    return {"success": True, "action": action, "container_id": container_id[:12]}


@app.get("/api/containers/all-stats")
async def all_containers_stats():
    """Get all containers with their stats in one call."""
    containers_resp = await list_containers()
    containers = containers_resp["containers"]

    # Fetch stats for running containers concurrently
    running = [c for c in containers if c["state"] == "running"]

    async def fetch_stats(c):
        try:
            stats = await container_stats(c["id"])
            c["stats"] = stats
        except Exception:
            c["stats"] = None
        return c

    tasks = [fetch_stats(c) for c in running]
    await asyncio.gather(*tasks)

    for c in containers:
        if c["state"] != "running":
            c["stats"] = None

    loop2 = asyncio.get_event_loop()
    system = await loop2.run_in_executor(None, get_system_memory)
    disks = await loop2.run_in_executor(None, get_disk_usage)

    return {
        "containers": containers,
        "system": {"memory": system, "disk": disks},
    }


# Serve embedded frontend
@app.get("/", response_class=HTMLResponse)
async def frontend():
    return HTMLResponse(content=FRONTEND_HTML)


FRONTEND_HTML = r"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Docker 管理面板</title>
<style>
/* ========== THEME SYSTEM ========== */
:root {
  --bg: #0f1117;
  --bg2: #161b22;
  --card: #1c2333;
  --card-hover: #242d3d;
  --border: #30363d;
  --border-light: #21262d;
  --text: #e6edf3;
  --text-dim: #8b949e;
  --text-bright: #f0f6fc;
  --accent: #58a6ff;
  --accent-dim: #1f6feb;
  --green: #3fb950;
  --green-dim: #238636;
  --red: #f85149;
  --red-dim: #da3633;
  --yellow: #d29922;
  --yellow-dim: #9e6a03;
  --orange: #db6d28;
  --purple: #bc8cff;
  --cyan: #39d2c0;
  --shadow: rgba(0,0,0,0.4);
  --radius: 12px;
  --radius-sm: 8px;
  --radius-xs: 6px;
  --transition: 0.25s cubic-bezier(0.4, 0, 0.2, 1);
}

/* Theme: Light */
[data-theme="light"] {
  --bg: #f6f8fa;
  --bg2: #ffffff;
  --card: #ffffff;
  --card-hover: #f3f4f6;
  --border: #d0d7de;
  --border-light: #e6e8eb;
  --text: #1f2328;
  --text-dim: #656d76;
  --text-bright: #1f2328;
  --accent: #0969da;
  --accent-dim: #0550ae;
  --green: #1a7f37;
  --green-dim: #116329;
  --red: #cf222e;
  --red-dim: #a40e26;
  --yellow: #9a6700;
  --yellow-dim: #7d4e00;
  --orange: #bc4c00;
  --purple: #8250df;
  --cyan: #1b7c83;
  --shadow: rgba(0,0,0,0.08);
}

/* Theme: Blue Ocean */
[data-theme="ocean"] {
  --bg: #0a1628;
  --bg2: #0d1f3c;
  --card: #112645;
  --card-hover: #163056;
  --border: #1c3a5f;
  --border-light: #152e4d;
  --text: #c3d4e6;
  --text-dim: #6b8cae;
  --text-bright: #e8f0fe;
  --accent: #38bdf8;
  --accent-dim: #0284c7;
  --green: #34d399;
  --green-dim: #059669;
  --red: #fb7185;
  --red-dim: #e11d48;
  --yellow: #fbbf24;
  --yellow-dim: #d97706;
  --orange: #fb923c;
  --purple: #a78bfa;
  --cyan: #22d3ee;
  --shadow: rgba(0,0,0,0.5);
}

/* Theme: Purple Night */
[data-theme="purple"] {
  --bg: #13081f;
  --bg2: #1a0e2e;
  --card: #221440;
  --card-hover: #2a1a4d;
  --border: #3d2666;
  --border-light: #2e1c52;
  --text: #d8c8f0;
  --text-dim: #8b7aab;
  --text-bright: #f0e8ff;
  --accent: #c084fc;
  --accent-dim: #9333ea;
  --green: #4ade80;
  --green-dim: #16a34a;
  --red: #f87171;
  --red-dim: #dc2626;
  --yellow: #facc15;
  --yellow-dim: #ca8a04;
  --orange: #fb923c;
  --purple: #e879f9;
  --cyan: #22d3ee;
  --shadow: rgba(0,0,0,0.5);
}

/* ========== BASE ========== */
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans SC', sans-serif;
  min-height: 100vh;
  transition: background var(--transition), color var(--transition);
}

/* ========== HEADER ========== */
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
  padding: 16px 24px;
  background: var(--card);
  border-radius: var(--radius);
  border: 1px solid var(--border);
  box-shadow: 0 2px 8px var(--shadow);
  position: sticky;
  top: 0;
  z-index: 100;
  backdrop-filter: blur(12px);
}
.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}
.header h1 {
  font-size: 18px;
  font-weight: 700;
  display: flex;
  align-items: center;
  gap: 8px;
  letter-spacing: -0.3px;
}
.header h1 .icon { font-size: 24px; }
.header-right {
  display: flex;
  align-items: center;
  gap: 10px;
}
.refresh-btn {
  background: var(--accent-dim);
  color: #fff;
  border: none;
  padding: 8px 18px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: 13px;
  font-weight: 600;
  transition: all var(--transition);
  display: flex;
  align-items: center;
  gap: 6px;
}
.refresh-btn:hover { background: var(--accent); transform: translateY(-1px); }
.refresh-btn:active { transform: translateY(0); }
.refresh-btn.loading { opacity: 0.6; pointer-events: none; }
.refresh-btn .spin { display: inline-block; animation: spin 1s linear infinite; }

/* Theme Switcher */
.theme-switcher {
  display: flex;
  gap: 4px;
  background: var(--bg2);
  border-radius: var(--radius-sm);
  padding: 3px;
  border: 1px solid var(--border);
}
.theme-btn {
  width: 28px;
  height: 28px;
  border-radius: 6px;
  border: 2px solid transparent;
  cursor: pointer;
  font-size: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all var(--transition);
  background: transparent;
}
.theme-btn:hover { background: var(--card-hover); }
.theme-btn.active { border-color: var(--accent); background: var(--card-hover); }

/* ========== PORTS BAR ========== */
.ports-bar {
  margin-bottom: 20px;
  padding: 16px 20px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: 0 2px 8px var(--shadow);
}
.ports-bar-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}
.ports-bar-title {
  font-size: 14px;
  font-weight: 700;
  display: flex;
  align-items: center;
  gap: 8px;
}
.ports-bar-count {
  font-size: 12px;
  color: var(--text-dim);
  background: var(--bg2);
  padding: 2px 10px;
  border-radius: 12px;
  border: 1px solid var(--border);
}
.ports-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.port-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: var(--bg2);
  border: 1px solid var(--border-light);
  border-radius: var(--radius-sm);
  font-size: 13px;
  font-weight: 600;
  font-family: 'SF Mono', 'Cascadia Code', 'Consolas', monospace;
  color: var(--text-bright);
  transition: all var(--transition);
}
.port-item:hover {
  border-color: var(--accent);
  background: var(--card-hover);
  transform: translateX(2px);
  box-shadow: 0 2px 8px var(--shadow);
}
.port-item .port-num {
  color: var(--accent);
  font-size: 16px;
  font-weight: 700;
  min-width: 50px;
  text-align: right;
}
.port-item .port-arrow {
  color: var(--text-dim);
  font-size: 12px;
}
.port-item .port-container {
  color: var(--text-dim);
  font-size: 11px;
  font-weight: 400;
  margin-left: auto;
  max-width: 150px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.port-item .port-proto {
  color: var(--yellow);
  font-size: 10px;
  font-weight: 500;
}
.ports-empty {
  color: var(--text-dim);
  font-size: 13px;
  padding: 8px 0;
}

/* ========== MAIN LAYOUT ========== */
.main-layout {
  display: grid;
  grid-template-columns: 280px 1fr;
  gap: 20px;
  align-items: start;
}
.ports-sidebar {
  position: sticky;
  top: 70px;
  max-height: calc(100vh - 90px);
  overflow-y: auto;
}
.ports-sidebar .ports-bar {
  margin-bottom: 12px;
}
.ports-sidebar .system-bar {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.ports-sidebar .sys-card {
  padding: 12px 14px;
}
.ports-sidebar .sys-card .value {
  font-size: 18px;
}
.content-area {
  min-width: 0;
}

/* ========== CONTAINER ROW ========== */
.container-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 16px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  margin-bottom: 6px;
  transition: all var(--transition);
}
.container-row:hover {
  border-color: var(--accent);
  background: var(--card-hover);
  transform: translateX(2px);
  box-shadow: 0 2px 8px var(--shadow);
}
.container-row .row-status {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.container-row .row-status.running { background: var(--green); box-shadow: 0 0 6px var(--green); animation: pulse 2s infinite; }
.container-row .row-status.exited { background: var(--red); }
.container-row .row-status.paused { background: var(--yellow); }
.container-row .row-status.created { background: var(--accent); }
.container-row .row-name {
  font-size: 14px;
  font-weight: 600;
  min-width: 140px;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-bright);
}
.container-row .row-image {
  font-size: 11px;
  color: var(--text-dim);
  font-family: 'SF Mono', 'Cascadia Code', monospace;
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.container-row .row-ports {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
  flex: 1;
  min-width: 0;
}
.container-row .row-port {
  font-size: 11px;
  font-family: 'SF Mono', 'Cascadia Code', monospace;
  padding: 2px 6px;
  background: rgba(88,166,255,0.08);
  color: var(--accent);
  border-radius: 4px;
  border: 1px solid rgba(88,166,255,0.15);
}
.container-row .row-stats {
  display: flex;
  gap: 12px;
  font-size: 11px;
  color: var(--text-dim);
  min-width: 120px;
  justify-content: flex-end;
}
.container-row .row-actions {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
}
.container-row .row-actions button {
  padding: 4px 10px;
  font-size: 11px;
  font-weight: 600;
  border-radius: 4px;
  cursor: pointer;
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text);
  transition: all var(--transition);
}
.container-row .row-actions button:disabled { opacity: 0.3; pointer-events: none; }
.container-row .row-actions .start:not(:disabled) { border-color: var(--green-dim); color: var(--green); }
.container-row .row-actions .start:hover:not(:disabled) { background: rgba(63,185,80,0.1); }
.container-row .row-actions .stop:not(:disabled) { border-color: var(--red-dim); color: var(--red); }
.container-row .row-actions .stop:hover:not(:disabled) { background: rgba(248,81,73,0.1); }
.container-row .row-actions .restart:not(:disabled) { border-color: var(--yellow-dim); color: var(--yellow); }
.container-row .row-actions .restart:hover:not(:disabled) { background: rgba(210,153,34,0.1); }

@media (max-width: 1200px) {
  .main-layout { grid-template-columns: 220px 1fr; }
}
@media (max-width: 900px) {
  .main-layout { grid-template-columns: 1fr; }
  .ports-sidebar { position: static; max-height: none; }
  .container-row { flex-wrap: wrap; }
  .container-row .row-stats { width: 100%; justify-content: flex-start; }
}

/* ========== SYSTEM BAR ========== */
.system-bar {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 12px;
  margin-bottom: 20px;
}
.sys-card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px 18px;
  transition: all var(--transition);
}
.sys-card:hover { border-color: var(--accent); transform: translateY(-1px); box-shadow: 0 4px 12px var(--shadow); }
.sys-card .label { font-size: 12px; color: var(--text-dim); margin-bottom: 8px; font-weight: 500; }
.sys-card .value { font-size: 22px; font-weight: 700; letter-spacing: -0.5px; }
.sys-card .sub { font-size: 11px; color: var(--text-dim); margin-top: 2px; }
.sys-card .bar { height: 5px; background: var(--border); border-radius: 3px; margin-top: 10px; overflow: hidden; }
.sys-card .bar-fill { height: 100%; border-radius: 3px; transition: width 0.8s cubic-bezier(0.4, 0, 0.2, 1); }

/* ========== CATEGORY TABS ========== */
.category-bar {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}
.cat-tab {
  background: var(--card);
  border: 1px solid var(--border);
  color: var(--text-dim);
  padding: 8px 18px;
  border-radius: 24px;
  cursor: pointer;
  font-size: 13px;
  font-weight: 500;
  transition: all var(--transition);
  display: flex;
  align-items: center;
  gap: 6px;
}
.cat-tab:hover { border-color: var(--accent); color: var(--text); }
.cat-tab.active { background: var(--accent-dim); color: #fff; border-color: var(--accent-dim); }
.cat-tab .count {
  background: rgba(255,255,255,0.15);
  padding: 1px 7px;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 600;
}
.cat-tab.active .count { background: rgba(255,255,255,0.25); }

/* ========== SECTION HEADERS ========== */
.section-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 24px 0 14px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--border);
}
.section-header:first-child { margin-top: 0; }
.section-title {
  font-size: 15px;
  font-weight: 700;
  display: flex;
  align-items: center;
  gap: 8px;
}
.section-title .dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}
.section-title .dot.green { background: var(--green); box-shadow: 0 0 8px var(--green); }
.section-title .dot.gray { background: var(--text-dim); }
.section-count {
  font-size: 12px;
  color: var(--text-dim);
  background: var(--bg2);
  padding: 2px 8px;
  border-radius: 10px;
  border: 1px solid var(--border);
}

/* ========== CONTAINER ROWS ========== */
.container-rows {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

/* Animations */
.toast.show { transform: translateY(0); }
.toast.success { border-left: 3px solid var(--green); }
.toast.error { border-left: 3px solid var(--red); }

/* Error banner */
.error-banner {
  background: rgba(248,81,73,0.08);
  border: 1px solid rgba(248,81,73,0.3);
  border-radius: var(--radius-sm);
  padding: 12px 16px;
  margin-bottom: 16px;
  font-size: 13px;
  color: var(--red);
}

/* Footer */
.footer {
  text-align: center;
  padding: 20px;
  color: var(--text-dim);
  font-size: 12px;
  margin-top: 30px;
}

/* Animations */
@keyframes spin { to { transform: rotate(360deg); } }
@keyframes fadeIn {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}
/* Responsive */
@media (max-width: 768px) {
  .header { padding: 12px 16px; }
  .header h1 { font-size: 16px; }
  .system-bar { grid-template-columns: 1fr 1fr; }
  .theme-switcher { display: none; }
}
@media (max-width: 480px) {
  .system-bar { grid-template-columns: 1fr; }
  .category-bar { gap: 6px; }
  .cat-tab { padding: 6px 12px; font-size: 12px; }
}
</style>
</head>
<body>

<div class="header">
  <div class="header-left">
    <h1><span class="icon">🐳</span> Docker 管理面板</h1>
  </div>
  <div class="header-right">
    <div class="theme-switcher">
      <button class="theme-btn active" data-theme="dark" title="暗色主题">🌙</button>
      <button class="theme-btn" data-theme="light" title="亮色主题">☀️</button>
      <button class="theme-btn" data-theme="ocean" title="海洋蓝">🌊</button>
      <button class="theme-btn" data-theme="purple" title="紫色之夜">🔮</button>
    </div>
    <button class="refresh-btn" id="refreshBtn" onclick="loadData()">
      <span id="refreshIcon">⟳</span> 刷新
    </button>
  </div>
</div>

<div id="errorBanner"></div>

<div class="main-layout">
  <!-- 左侧：端口占用 -->
  <div class="ports-sidebar">
    <div class="ports-bar">
      <div class="ports-bar-header">
        <div class="ports-bar-title">🔌 已占用端口</div>
        <span class="ports-bar-count" id="portsCount">0 个端口</span>
      </div>
      <div class="ports-list" id="portsList"></div>
    </div>
    <div class="system-bar" id="systemBar"></div>
  </div>

  <!-- 右侧：容器列表 -->
  <div class="content-area">
    <div class="category-bar" id="categoryBar">
      <div class="cat-tab active" data-cat="all" onclick="setCategory('all')">
        📦 全部 <span class="count" id="count-all">0</span>
      </div>
      <div class="cat-tab" data-cat="running" onclick="setCategory('running')">
        🟢 使用中 <span class="count" id="count-running">0</span>
      </div>
      <div class="cat-tab" data-cat="stopped" onclick="setCategory('stopped')">
        🔴 未使用 <span class="count" id="count-stopped">0</span>
      </div>
    </div>
    <div id="sectionsContainer"></div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
const API = '';
let allContainers = [];
let currentCategory = 'all';

// Theme switching
document.querySelectorAll('.theme-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const theme = btn.dataset.theme;
    document.documentElement.setAttribute('data-theme', theme);
    document.querySelectorAll('.theme-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    localStorage.setItem('docker-panel-theme', theme);
  });
});

// Load saved theme
const savedTheme = localStorage.getItem('docker-panel-theme');
if (savedTheme) {
  document.documentElement.setAttribute('data-theme', savedTheme);
  document.querySelectorAll('.theme-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.theme === savedTheme);
  });
}

async function loadData() {
  const btn = document.getElementById('refreshBtn');
  const icon = document.getElementById('refreshIcon');
  btn.classList.add('loading');
  icon.classList.add('spin');
  document.getElementById('errorBanner').innerHTML = '';

  try {
    // Fetch containers+stats and system info (ports) in parallel
    const [respAll, respSys] = await Promise.all([
      fetch(API + '/api/containers/all-stats'),
      fetch(API + '/api/system'),
    ]);
    if (!respAll.ok) {
      const err = await respAll.json();
      throw new Error(err.detail || 'API error');
    }
    const data = await respAll.json();
    const sysData = await respSys.json();

    if (data.containers) {
      allContainers = data.containers;
    }
    // Merge system data with ports
    const system = { ...(data.system || {}), ...(sysData || {}) };
    renderPorts(system);
    renderSystemBar(system);
    renderCategories();
    renderContainers();
  } catch (e) {
    document.getElementById('errorBanner').innerHTML =
      '<div class="error-banner">⚠️ 加载失败: ' + e.message + '</div>';
  } finally {
    btn.classList.remove('loading');
    icon.classList.remove('spin');
  }
}

function renderPorts(sys) {
  const ports = sys.ports || [];
  const count = sys.ports_count || 0;
  document.getElementById('portsCount').textContent = count + ' 个端口';
  const list = document.getElementById('portsList');
  if (ports.length === 0) {
    list.innerHTML = '<div class="ports-empty">暂无运行中的容器暴露端口</div>';
    return;
  }
  list.innerHTML = ports.map(p => {
    const name = p.container_name || '';
    return `<div class="port-item" title="${name}">
      <span class="port-num">${p.host_port}</span>
      <span class="port-arrow">→</span>
      <span>${p.container_port}</span>
      <span class="port-proto">${p.protocol}</span>
      ${name ? `<span class="port-container">(${name})</span>` : ''}
    </div>`;
  }).join('');
}

function renderSystemBar(sys) {
  const mem = sys.memory || {};
  const disk = sys.disk || {};
  let html = '';

  if (mem.total_mb) {
    const pct = mem.use_percent || 0;
    const color = pct > 85 ? 'var(--red)' : pct > 65 ? 'var(--yellow)' : 'var(--green)';
    html += `<div class="sys-card">
      <div class="label">🧠 系统内存</div>
      <div class="value">${mem.used_mb} <span style="font-size:14px;color:var(--text-dim)">/ ${mem.total_mb} MB</span></div>
      <div class="sub">可用 ${mem.available_mb} MB</div>
      <div class="bar"><div class="bar-fill" style="width:${pct}%;background:${color}"></div></div>
    </div>`;
  }

  for (const [mount, info] of Object.entries(disk)) {
    const pct = parseInt(info.use_percent) || 0;
    const color = pct > 85 ? 'var(--red)' : pct > 65 ? 'var(--yellow)' : 'var(--accent)';
    const label = mount === '/' ? '系统盘' : mount.replace('/volume', '存储卷 ');
    html += `<div class="sys-card">
      <div class="label">💾 ${label}</div>
      <div class="value">${info.used} <span style="font-size:14px;color:var(--text-dim)">/ ${info.size}</span></div>
      <div class="sub">可用 ${info.available}</div>
      <div class="bar"><div class="bar-fill" style="width:${pct}%;background:${color}"></div></div>
    </div>`;
  }

  // Container summary
  const running = allContainers.filter(c => c.state === 'running').length;
  const total = allContainers.length;
  html += `<div class="sys-card">
    <div class="label">🐳 容器状态</div>
    <div class="value">${running} <span style="font-size:14px;color:var(--text-dim)">/ ${total} 运行中</span></div>
    <div class="sub">${total - running} 个未启动</div>
    <div class="bar"><div class="bar-fill" style="width:${total > 0 ? running/total*100 : 0}%;background:var(--green)"></div></div>
  </div>`;

  document.getElementById('systemBar').innerHTML = html ||
    '<div class="sys-card"><div class="label">系统信息</div><div class="value">加载中...</div></div>';
}

function renderCategories() {
  const running = allContainers.filter(c => c.state === 'running').length;
  const stopped = allContainers.filter(c => c.state !== 'running').length;
  document.getElementById('count-all').textContent = allContainers.length;
  document.getElementById('count-running').textContent = running;
  document.getElementById('count-stopped').textContent = stopped;
}

function setCategory(cat) {
  currentCategory = cat;
  document.querySelectorAll('.cat-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.cat === cat);
  });
  renderContainers();
}

function renderContainers() {
  const container = document.getElementById('sectionsContainer');

  let running = allContainers.filter(c => c.state === 'running');
  let stopped = allContainers.filter(c => c.state !== 'running');

  if (currentCategory === 'running') {
    stopped = [];
  } else if (currentCategory === 'stopped') {
    running = [];
  }

  let html = '';

  if (running.length > 0) {
    html += `<div class="section-header">
      <div class="section-title">
        <span class="dot green"></span>
        使用中
      </div>
      <span class="section-count">${running.length} 个容器</span>
    </div>`;
    html += `<div class="container-rows">`;
    html += running.map((c, i) => renderRow(c, i)).join('');
    html += `</div>`;
  }

  if (stopped.length > 0) {
    html += `<div class="section-header">
      <div class="section-title">
        <span class="dot gray"></span>
        未使用
      </div>
      <span class="section-count">${stopped.length} 个容器</span>
    </div>`;
    html += `<div class="container-rows">`;
    html += stopped.map((c, i) => renderRow(c, i)).join('');
    html += `</div>`;
  }

  if (running.length === 0 && stopped.length === 0) {
    html = `<div class="empty-state">
      <div class="icon">📦</div>
      <div>${currentCategory === 'all' ? '没有容器' : (currentCategory === 'running' ? '没有运行中的容器' : '没有未使用的容器')}</div>
    </div>`;
  }

  container.innerHTML = html;
}

function renderRow(c, index) {
  const state = c.state || 'unknown';
  const isRunning = state === 'running';
  const isStopped = state === 'exited' || state === 'dead';
  const isCreated = state === 'created';

  // Ports for this container
  const portsHtml = (c.ports || [])
    .filter(p => p.host_port)
    .map(p => `<span class="row-port">${p.host_port}→${p.container_port}/${p.type}</span>`)
    .join('');

  // Stats
  let statsHtml = '';
  if (c.stats && isRunning) {
    const cpu = c.stats.cpu_percent || 0;
    const memUse = c.stats.memory_usage_mb || 0;
    const memLimit = c.stats.memory_limit_mb || 0;
    statsHtml = `<span>CPU ${cpu}%</span><span>MEM ${memUse}/${memLimit}MB</span>`;
  } else {
    statsHtml = '<span>-</span>';
  }

  // Action buttons
  const startDisabled = isRunning ? 'disabled' : '';
  const stopDisabled = (isStopped || isCreated) ? 'disabled' : '';
  const restartDisabled = (isStopped || isCreated) ? 'disabled' : '';

  return `<div class="container-row" id="card-${c.id}">
    <span class="row-status ${state}"></span>
    <span class="row-name" title="${escHtml(c.name)}">${escHtml(c.name)}</span>
    <span class="row-image" title="${escHtml(c.image || '')}">${escHtml(c.image || '-')}</span>
    <div class="row-ports">${portsHtml || '<span style="color:var(--text-dim);font-size:11px">无端口</span>'}</div>
    <div class="row-stats">${statsHtml}</div>
    <div class="row-actions">
      <button class="start" ${startDisabled} onclick="doAction('${c.id}','start',this)">▶</button>
      <button class="stop" ${stopDisabled} onclick="doAction('${c.id}','stop',this)">⏹</button>
      <button class="restart" ${restartDisabled} onclick="doAction('${c.id}','restart',this)">⟳</button>
    </div>
  </div>`;
}

function escHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

async function doAction(id, action, btn) {
  const labels = { start: '启动', stop: '停止', restart: '重启' };

  btn.classList.add('loading');
  btn.disabled = true;

  try {
    const resp = await fetch(API + `/api/container/${id}/action`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action }),
    });
    if (!resp.ok) {
      const err = await resp.json();
      throw new Error(err.detail || '操作失败');
    }
    showToast(`${labels[action]} 成功`, 'success');
    setTimeout(loadData, 1500);
  } catch (e) {
    showToast(`${labels[action]} 失败: ${e.message}`, 'error');
    btn.classList.remove('loading');
    btn.disabled = false;
  }
}

function showToast(msg, type = '') {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show ' + type;
  setTimeout(() => t.className = 'toast', 3000);
}

// Init
loadData();
setInterval(loadData, 30000);
</script>
</body>
</html>
"""
