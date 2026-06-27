#!/usr/bin/env python3
"""Docker Management Panel - Backend (FastAPI)"""
import json, subprocess, asyncio, re, os, socket, ssl, urllib.request, urllib.error, shutil, tempfile
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

VERSION = "1.5.0"
GITHUB_REPO = "nbqcw123/docker-panel"
GITHUB_RAW_BASE = f"https://raw.githubusercontent.com/{GITHUB_REPO}/master"

# Mirror sources for update
UPDATE_SOURCES = [
    {"name": "GitHub", "url": f"https://raw.githubusercontent.com/{GITHUB_REPO}/master", "test_file": "version.json"},
    {"name": "GitMirror", "url": f"https://raw.gitmirror.com/{GITHUB_REPO}/master", "test_file": "version.json"},
    {"name": "GHFast", "url": f"https://ghfast.top/https://raw.githubusercontent.com/{GITHUB_REPO}/master", "test_file": "version.json"},
]

app = FastAPI(title="Docker Panel")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DOCKER_SOCKET = "/var/run/docker.sock"
HOST_ROOT = os.environ.get("HOST_ROOT", "")

# Custom names and descriptions storage
CUSTOM_DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "container_meta.json")

def _load_custom_meta():
    """Load custom names and descriptions from local JSON file"""
    try:
        if os.path.exists(CUSTOM_DATA_FILE):
            with open(CUSTOM_DATA_FILE, "r") as f:
                return json.load(f)
    except:
        pass
    return {"names": {}, "descriptions": {}}

def _save_custom_meta(data):
    """Save custom names and descriptions to local JSON file"""
    try:
        with open(CUSTOM_DATA_FILE, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except:
        pass

def _get_remote_version():
    """Fetch latest version info from GitHub"""
    try:
        url = f"{GITHUB_RAW_BASE}/version.json"
        req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return data
    except Exception as e:
        return None

def _version_tuple(v):
    """Parse version string to tuple for comparison"""
    try:
        return tuple(int(x) for x in v.strip().split("."))
    except:
        return (0,)

@app.get("/api/version")
async def get_version():
    """Get local and remote version info"""
    import asyncio
    loop = asyncio.get_event_loop()
    remote = await asyncio.wait_for(loop.run_in_executor(None, _get_remote_version), timeout=5)
    local_version = VERSION
    remote_version = remote.get("version", "") if remote else ""
    has_update = False
    if remote_version:
        has_update = _version_tuple(remote_version) > _version_tuple(local_version)
    return {
        "local": local_version,
        "remote": remote_version,
        "has_update": has_update,
        "changelog": remote.get("changelog", []) if remote else [],
        "date": remote.get("date", "") if remote else "",
        "repo": GITHUB_REPO
    }

class UpdateRequest(BaseModel):
    target_version: str = ""
    source_url: str = ""  # optional: force specific source

@app.get("/api/check-update")
async def check_update():
    """Check if a newer version is available on GitHub"""
    import asyncio
    loop = asyncio.get_event_loop()
    # Run blocking call in thread to avoid freezing uvicorn
    remote = await asyncio.wait_for(loop.run_in_executor(None, _get_remote_version), timeout=5)
    local_version = VERSION
    remote_version = remote.get("version", "") if remote else ""
    has_update = False
    if remote_version:
        has_update = _version_tuple(remote_version) > _version_tuple(local_version)
    return {
        "currentVersion": local_version,
        "latestVersion": remote_version,
        "updateAvailable": has_update,
        "changelog": remote.get("changelog", []) if remote else [],
        "downloadUrl": f"https://github.com/{GITHUB_REPO}/releases/latest"
    }

@app.post("/api/upgrade")
async def do_upgrade():
    """Pull latest image, rebuild container, and restart service"""
    import subprocess as sp
    container_name = "docker-panel"
    image_name = "docker-panel:latest"
    logs = []
    try:
        # Step 1: Pull latest image from Docker Hub
        logs.append("正在拉取最新镜像...")
        pull_result = sp.run(["docker", "pull", image_name], capture_output=True, text=True, timeout=180)
        if pull_result.returncode != 0:
            logs.append(f"拉取失败: {pull_result.stderr[:200]}")
            return {"success": False, "message": "\n".join(logs)}
        logs.append("镜像拉取完成")

        # Step 2: Stop current container
        logs.append("正在停止当前容器...")
        try:
            sp.run(["docker", "stop", container_name], timeout=30)
        except:
            pass

        # Step 3: Remove old container
        try:
            sp.run(["docker", "rm", container_name], timeout=15)
        except:
            pass
        logs.append("旧容器已清理")

        # Step 4: Run new container
        host_port = "50087"
        internal_port = "50087"
        run_cmd = [
            "docker", "run", "-d",
            "--name", container_name,
            "--restart", "unless-stopped",
            "-p", f"{host_port}:{internal_port}",
            "-v", "/var/run/docker.sock:/var/run/docker.sock",
            "-v", "/:/host:ro",
            "-e", "HOST_ROOT=/host",
            "-w", "/app",
            image_name
        ]
        logs.append("正在启动新容器...")
        run_result = sp.run(run_cmd, capture_output=True, text=True, timeout=60)
        if run_result.returncode != 0:
            logs.append(f"启动失败: {run_result.stderr[:200]}")
            return {"success": False, "message": "\n".join(logs)}
        logs.append("升级成功，容器已重启")
        return {"success": True, "message": "\n".join(logs)}
    except Exception as e:
        return {"success": False, "message": f"升级失败: {str(e)}\n" + "\n".join(logs)}

@app.get("/api/sources")
async def check_sources():
    """Check all update sources and return their status and latency"""
    import time
    results = []
    for src in UPDATE_SOURCES:
        url = f"{src['url']}/{src['test_file']}"
        start = time.time()
        try:
            req = urllib.request.Request(url, headers={"Cache-Control": "no-cache", "User-Agent": "DockerPanel/1.4"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                resp.read()
            latency = round((time.time() - start) * 1000)
            results.append({"name": src["name"], "url": src["url"], "status": "ok", "latency_ms": latency})
        except Exception as e:
            results.append({"name": src["name"], "url": src["url"], "status": "error", "error": str(e)[:100]})
    # Sort by latency (ok first, then error)
    results.sort(key=lambda x: x.get("latency_ms", 9999))
    return {"sources": results}

@app.post("/api/update")
async def perform_update(req: UpdateRequest):
    """Download latest main.py from best available source and replace local file"""
    try:
        # Determine download URL: use provided source_url or default
        base_url = req.source_url if req.source_url else GITHUB_RAW_BASE
        
        # Download latest main.py
        url = f"{base_url}/main.py"
        req_dl = urllib.request.Request(url, headers={"Cache-Control": "no-cache", "User-Agent": "DockerPanel/1.4"})
        with urllib.request.urlopen(req_dl, timeout=30) as resp:
            new_content = resp.read()
        
        # Verify it's valid Python
        try:
            compile(new_content, "<string>", "exec")
        except SyntaxError as e:
            raise HTTPException(500, f"Downloaded file has syntax error: {e}")
        
        # Backup current file
        main_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
        backup_path = main_path + ".bak"
        shutil.copy2(main_path, backup_path)
        
        # Write new file
        with open(main_path, "wb") as f:
            f.write(new_content)
        
        # Also download version.json
        try:
            url_v = f"{base_url}/version.json"
            req_v = urllib.request.Request(url_v, headers={"Cache-Control": "no-cache"})
            with urllib.request.urlopen(req_v, timeout=10) as resp:
                v_content = resp.read()
            v_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "version.json")
            with open(v_path, "wb") as f:
                f.write(v_content)
        except:
            pass  # version.json is optional
        
        # Clear pycache
        pycache = os.path.join(os.path.dirname(os.path.abspath(__file__)), "__pycache__")
        if os.path.exists(pycache):
            shutil.rmtree(pycache)
        
        return {"success": True, "message": "更新成功，请重启面板服务以生效", "restarted": False}
    except HTTPException:
        raise
    except Exception as e:
        # Restore backup if exists
        try:
            if os.path.exists(backup_path):
                shutil.copy2(backup_path, main_path)
        except:
            pass
        raise HTTPException(500, f"更新失败: {str(e)}")

@app.post("/api/restart")
async def restart_service():
    """Restart the panel service"""
    try:
        # Write a restart flag file
        flag_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".restart_flag")
        with open(flag_path, "w") as f:
            f.write(str(os.getpid()))
        return {"success": True, "message": "重启信号已发送"}
    except Exception as e:
        raise HTTPException(500, f"重启失败: {str(e)}")

def _detect_docker_bin() -> str:
    for c in ["docker", "/usr/bin/docker", "/usr/local/bin/docker", "/volume1/@appstore/ContainerManager/usr/bin/docker"]:
        try:
            r = subprocess.run([c, "version", "--format", "{{.Server.Version}}"], capture_output=True, text=True, timeout=5)
            if r.returncode == 0: return c
        except: continue
    return "docker"
DOCKER_BIN = _detect_docker_bin()

def _detect_disk_targets() -> list:
    targets = []
    try:
        # Use df without -l to see all filesystems (including /volume1 on Synology)
        result = subprocess.run(["df", "--output=target,pcent,size,fstype"], capture_output=True, text=True, timeout=5)
        for line in result.stdout.strip().split("\n")[1:]:
            parts = line.split()
            if len(parts) >= 4:
                mount = parts[0]
                size_str = parts[2]
                fs_type = parts[3]
                try: int(parts[1].replace("%", ""))
                except ValueError: continue
                # Skip absurdly large filesystems (> 100TB, likely storage pool pseudo-fs like btrfs root)
                try:
                    size_kb = int(size_str)
                    if size_kb > 100 * 1024 * 1024 * 1024:  # 100TB in KB
                        continue
                except:
                    pass
                # Skip pseudo filesystems (show whole pool/storage, not real partition)
                if fs_type in ("btrfs", "zfs", "overlay", "overlayfs") and mount == "/":
                    continue
                if mount in ("/", "/boot", "/boot/efi"): targets.append(mount)
                elif re.match(r"^/host/vol\d+$", mount): targets.append(mount)
                elif re.match(r"^/host/volume\d+$", mount): targets.append(mount)
                elif re.match(r"^/volume\d+$", mount): targets.append(mount)
                elif re.match(r"^/vol\d+$", mount): targets.append(mount)
                elif re.match(r"^/volume\d+/", mount): targets.append(mount)
                elif mount in ("/mnt", "/srv", "/data", "/home"): targets.append(mount)
                elif re.match(r"^(/mnt|/srv|/data|/home)/[^/]+$", mount): targets.append(mount)
    except: pass
    seen, unique = set(), []
    for t in targets:
        if t not in seen: seen.add(t); unique.append(t)
    unique.sort(key=lambda x: (0 if x == "/" else 1, x))
    return unique if unique else ["/"]

def docker_api(method, path, data=None):
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(15); sock.connect(DOCKER_SOCKET)
        body = data or b""
        hdrs = f"{method} {path} HTTP/1.1\r\nHost: localhost\r\nAccept: application/json\r\n"
        if data: hdrs += "Content-Type: application/json\r\n"
        hdrs += f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
        sock.sendall(hdrs.encode() + body)
        resp = b""
        while True:
            try:
                chunk = sock.recv(8192)
                if not chunk: break
                resp += chunk
            except socket.timeout: break
        sock.close()
        he = resp.find(b"\r\n\r\n")
        if he == -1: raise Exception("Invalid HTTP response")
        hp = resp[:he].decode("utf-8", errors="replace")
        bp = resp[he+4:]
        sc = int(hp.split("\r\n")[0].split(" ")[1])
        if "Transfer-Encoding: chunked" in hp: bp = _decode_chunked(bp)
        if sc >= 400:
            try: return {"error": json.loads(bp).get("message", f"HTTP {sc}"), "status_code": sc}
            except: return {"error": f"HTTP {sc}"}
        if not bp: return {}
        try: return json.loads(bp)
        except: return {"raw": bp.decode("utf-8", errors="replace")}
    except (PermissionError, FileNotFoundError, OSError): return _docker_cli_fallback(method, path, data)
    except Exception as e: return {"error": str(e)}

def _decode_chunked(data):
    r, pos = b"", 0
    while pos < len(data):
        e = data.find(b"\r\n", pos)
        if e == -1: break
        try: sz = int(data[pos:e].split(b";")[0].strip(), 16)
        except: break
        if sz == 0: break
        r += data[e+2:e+2+sz]; pos = e+2+sz+2
    return r

def _docker_cli_fallback(method, path, data=None):
    try:
        if path.startswith("/containers/json"):
            cmd = [c for c in [DOCKER_BIN, "ps", "-a" if "all=true" in path else ""] if c]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if r.returncode != 0: return {"error": r.stderr.strip()}
            cs = []
            for ln in r.stdout.strip().split("\n")[1:]:
                p = ln.split(None, 7)
                if len(p) >= 7:
                    ss = " ".join(p[4:7])
                    st = "running" if ss.startswith("Up") else "exited" if "Exited" in ss else "created" if "Created" in ss else "paused"
                    cs.append({"Id": p[0], "Names": [ln.split()[-1]], "Image": p[1], "Status": ss, "State": st, "Ports": _parse_ports(p[6] if len(p)>6 else ""), "Labels": {}})
            return cs
        cid = path.split("/")[2]
        if path.endswith("/start"): r = subprocess.run([DOCKER_BIN, "start", cid], capture_output=True, text=True, timeout=30); return {"started": True} if r.returncode == 0 else {"error": r.stderr.strip()}
        if path.endswith("/stop"): r = subprocess.run([DOCKER_BIN, "stop", cid], capture_output=True, text=True, timeout=30); return {"stopped": True} if r.returncode == 0 else {"error": r.stderr.strip()}
        if path.endswith("/restart"): r = subprocess.run([DOCKER_BIN, "restart", cid], capture_output=True, text=True, timeout=30); return {"restarted": True} if r.returncode == 0 else {"error": r.stderr.strip()}
        if "/stats" in path:
            r = subprocess.run([DOCKER_BIN, "stats", cid, "--no-stream", "--format", "{{json .}}"], capture_output=True, text=True, timeout=15)
            if r.returncode != 0: return {"error": r.stderr.strip()}
            try:
                s = json.loads(r.stdout.strip())
                return {"cpu_stats":{"cpu_usage":{"total_usage":0},"system_cpu_usage":0,"online_cpus":1},"precpu_stats":{},"memory_stats":{"usage":int(s.get("MemUsage","0").split("/")[0].strip().replace("MiB","000").replace("GiB","000000")),"limit":int(s.get("MemUsage","0").split("/")[1].strip().replace("MiB","000").replace("GiB","000000"))},"networks":{}}
            except: return {"memory_stats":{"usage":0,"limit":1}}
        return {"error": f"Unsupported: {path}"}
    except Exception as e: return {"error": str(e)}

def _parse_ports(ps):
    if not ps: return []
    ports = []
    for part in ps.split(","):
        part = part.strip()
        if "->" in part:
            h,c = part.split("->",1)
            hp = h.split(":")[-1].strip() if ":" in h else ""
            cp = c.split("/")[0].strip() if "/" in c else c.strip()
            proto = c.split("/")[1].strip() if "/" in c else "tcp"
            if hp: ports.append({"PublicPort":int(hp) if hp.isdigit() else 0,"PrivatePort":int(cp) if cp.isdigit() else 0,"Type":proto,"IP":"0.0.0.0"})
    return ports

# Shared folder size cache
_shared_folder_cache = {"time": 0, "data": {}}
_SHARED_FOLDER_CACHE_TTL = 300  # 5 minutes

def _get_shared_folder_sizes():
    """Get shared folder sizes, with caching. Auto-detects NAS type and access method."""
    import time
    now = time.time()
    if now - _shared_folder_cache["time"] < _SHARED_FOLDER_CACHE_TTL:
        return _shared_folder_cache["data"]
    
    shared_folders = {}
    try:
        import os
        # Method 1: Direct filesystem access (container mounted /volume1 or /vol)
        search_roots = []
        for prefix in [HOST_ROOT, ""] if HOST_ROOT else [""]:
            for vol in ["/volume1", "/vol", "/vol1", "/mnt/user"]:
                path = prefix + vol
                if os.path.isdir(path):
                    search_roots.append(path)
                    break
            if search_roots:
                break
        
        if search_roots:
            for root in search_roots:
                try:
                    for name in os.listdir(root):
                        path = os.path.join(root, name)
                        if os.path.isdir(path) and not name.startswith("@") and not name.startswith("."):
                            try:
                                r_du = subprocess.run(["du","-sm",path], capture_output=True, text=True, timeout=30)
                                size_mb = int(r_du.stdout.split()[0])
                                if size_mb > 100:
                                    shared_folders[name] = size_mb
                            except:
                                pass
                except:
                    pass
        
        # Method 2: df subvolume detection (when /volume1 not mounted in container)
        if not shared_folders:
            try:
                r = subprocess.run(["df","--output=target,size"], capture_output=True, text=True, timeout=5)
                for line in r.stdout.strip().split("\n")[1:]:
                    parts = line.split()
                    if len(parts) >= 2:
                        m, size_str = parts[0], parts[1]
                        # Synology: /volume1/xxx subvolumes
                        vol_match = re.match(r"^/volume\d+/(.+)$", m)
                        if vol_match:
                            name = vol_match.group(1)
                            if not name.startswith("@") and not name.startswith("."):
                                try:
                                    size_mb = int(size_str) // 1024
                                    if size_mb > 100:
                                        shared_folders[name] = size_mb
                                except:
                                    pass
                        # fnOS: /vol1/xxx
                        vol_match2 = re.match(r"^/vol\d+/(.+)$", m)
                        if vol_match2:
                            name = vol_match2.group(1)
                            if not name.startswith("@") and not name.startswith("."):
                                try:
                                    size_mb = int(size_str) // 1024
                                    if size_mb > 100:
                                        shared_folders[name] = size_mb
                                except:
                                    pass
            except:
                pass
    except:
        pass
    
    _shared_folder_cache["time"] = now
    _shared_folder_cache["data"] = shared_folders
    return shared_folders

def get_disk_usage():
    try:
        tgts = _detect_disk_targets()
        # Get filesystem type with df -lT
        fs_map = {}
        try:
            r_fs = subprocess.run(["df","-lT"]+tgts, capture_output=True, text=True, timeout=5)
            for ln in r_fs.stdout.strip().split("\n")[1:]:
                p = ln.split()
                if len(p) >= 7:
                    fs_map[p[6]] = p[1]
        except:
            pass
        # Get detailed info
        r = subprocess.run(["df"]+tgts, capture_output=True, text=True, timeout=5)
        ds = {}
        for ln in r.stdout.strip().split("\n")[1:]:
            p = ln.split()
            if len(p) >= 6:
                mount = p[5]
                try:
                    total_bytes = int(p[1]) * 1024
                    used_bytes = int(p[2]) * 1024
                    avail_bytes = int(p[3]) * 1024
                except:
                    total_bytes = used_bytes = avail_bytes = 0
                ds[mount] = {
                    "size": p[1]+"G", "used": p[2]+"G", "available": p[3]+"G",
                    "use_percent": p[4].replace("%",""),
                    "total_bytes": total_bytes, "used_bytes": used_bytes, "available_bytes": avail_bytes,
                    "fs_type": fs_map.get(mount, "unknown")
                }
        
        # Get shared folder sizes (Synology NAS)
        shared = _get_shared_folder_sizes()
        if shared:
            ds["__shared_folders__"] = shared
        
        return ds
    except: return {}

def get_system_memory():
    try:
        r = subprocess.run(["cat","/proc/meminfo"], capture_output=True, text=True, timeout=5)
        mi = {}
        for ln in r.stdout.strip().split("\n"):
            m = re.match(r"(\w+):\s+(\d+)\s+kB", ln)
            if m: mi[m.group(1)] = int(m.group(2))
        t,a = mi.get("MemTotal",0), mi.get("MemAvailable",0)
        used = t - a
        buffers = mi.get("Buffers",0)
        cached = mi.get("Cached",0) + mi.get("SReclaimable",0)
        active = mi.get("Active",0)
        inactive = mi.get("Inactive",0)
        slab = mi.get("Slab",0)
        # Swap
        swap_total = mi.get("SwapTotal",0)
        swap_free = mi.get("SwapFree",0)
        swap_used = swap_total - swap_free
        swap_pct = round(swap_used / swap_total * 100, 1) if swap_total > 0 else 0
        return {
            "total_mb":round(t/1024),"used_mb":round(used/1024),"available_mb":round(a/1024),
            "total_gb":round(t/1048576,2),"used_gb":round(used/1048576,2),"available_gb":round(a/1048576,2),
            "buffers_mb":round(buffers/1024),"cached_mb":round(cached/1024),
            "active_mb":round(active/1024),"inactive_mb":round(inactive/1024),"slab_mb":round(slab/1024),
            "use_percent":round(used/t*100,1) if t>0 else 0,
            "swap_total_mb":round(swap_total/1024),"swap_used_mb":round(swap_used/1024),"swap_free_mb":round(swap_free/1024),
            "swap_total_gb":round(swap_total/1048576,2),"swap_used_gb":round(swap_used/1048576,2),
            "swap_percent":swap_pct
        }
    except: return {}

def get_system_cpu():
    """Get detailed CPU info"""
    try:
        import time, os
        cores = os.cpu_count() or 1
        
        # Read /proc/stat twice
        with open("/proc/stat", "r") as f:
            line1 = f.readline()
        parts1 = line1.split()
        idle1 = int(parts1[4])
        total1 = sum(int(x) for x in parts1[1:])
        
        time.sleep(0.1)
        
        with open("/proc/stat", "r") as f:
            line2 = f.readline()
        parts2 = line2.split()
        idle2 = int(parts2[4])
        total2 = sum(int(x) for x in parts2[1:])
        
        idle_delta = idle2 - idle1
        total_delta = total2 - total1
        if total_delta == 0:
            return {"use_percent": 0, "load_1m": 0, "load_5m": 0, "load_15m": 0, "cores": cores, "model": "", "freq": ""}
        use_percent = round((1 - idle_delta / total_delta) * 100, 1)
        
        # Per-core usage
        per_core = []
        try:
            with open("/proc/stat", "r") as f:
                lines = f.readlines()
            # First snapshot already taken, just read current
            core_lines = [l for l in lines if l.startswith("cpu") and l[3].isdigit()]
            # We need a second snapshot for per-core
            snap1 = []
            for cl in core_lines:
                p = cl.split()
                snap1.append((int(p[4]), sum(int(x) for x in p[1:])))
            time.sleep(0.1)
            with open("/proc/stat", "r") as f:
                lines2 = f.readlines()
            core_lines2 = [l for l in lines2 if l.startswith("cpu") and l[3].isdigit()]
            for i, cl in enumerate(core_lines2):
                p = cl.split()
                idle_c = int(p[4])
                total_c = sum(int(x) for x in p[1:])
                if i < len(snap1) and total_c - snap1[i][1] > 0:
                    core_pct = round((1 - (idle_c - snap1[i][0]) / (total_c - snap1[i][1])) * 100, 1)
                else:
                    core_pct = 0
                per_core.append(core_pct)
        except:
            per_core = [use_percent] * cores
        
        # CPU model
        model = ""
        try:
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if "model name" in line:
                        model = line.split(":")[1].strip()
                        break
        except:
            pass
        
        # CPU frequency
        freq = ""
        try:
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if "cpu MHz" in line or "MHz" in line:
                        mhz = float(line.split(":")[1].strip())
                        freq = f"{mhz:.0f} MHz" if mhz < 1000 else f"{mhz/1000:.2f} GHz"
                        break
        except:
            pass
        
        # Load averages
        try:
            with open("/proc/loadavg", "r") as f:
                la = f.read().split()
            load_1m, load_5m, load_15m = la[0], la[1], la[2]
        except:
            load_1m = load_5m = load_15m = "0"
        
        return {
            "use_percent": use_percent, "load_1m": load_1m, "load_5m": load_5m, "load_15m": load_15m,
            "cores": cores, "model": model, "freq": freq, "per_core": per_core
        }
    except: return {"use_percent": 0, "load_1m": 0, "load_5m": 0, "load_15m": 0, "cores": 1, "model": "", "freq": "", "per_core": []}

# Network stats: read /proc/net/dev for total bytes and calculate speed
_net_prev = {"time": 0, "rx_bytes": 0, "tx_bytes": 0}

def get_network_stats():
    """Get network total bytes and real-time speed (bytes/s)"""
    global _net_prev
    try:
        with open("/proc/net/dev", "r") as f:
            lines = f.readlines()
        total_rx, total_tx = 0, 0
        interfaces = {}
        for ln in lines[2:]:  # skip header lines
            parts = ln.strip().split()
            if len(parts) < 10: continue
            iface = parts[0].rstrip(":")
            if iface in ("lo",): continue
            rx_bytes = int(parts[1])
            tx_bytes = int(parts[9])
            total_rx += rx_bytes
            total_tx += tx_bytes
            interfaces[iface] = {"rx_bytes": rx_bytes, "tx_bytes": tx_bytes}
        now = __import__("time").time()
        speed_rx, speed_tx = 0, 0
        if _net_prev["time"] > 0:
            dt = now - _net_prev["time"]
            if dt > 0:
                speed_rx = max(0, (total_rx - _net_prev["rx_bytes"]) / dt)
                speed_tx = max(0, (total_tx - _net_prev["tx_bytes"]) / dt)
        _net_prev = {"time": now, "rx_bytes": total_rx, "tx_bytes": total_tx}
        return {
            "interfaces": interfaces,
            "total_rx_bytes": total_rx,
            "total_tx_bytes": total_tx,
            "speed_rx": round(speed_rx),
            "speed_tx": round(speed_tx),
        }
    except: return {}

@app.get("/api/containers")
async def list_containers():
    loop = asyncio.get_event_loop()
    raw = await loop.run_in_executor(None, lambda: docker_api("GET", "/containers/json?all=true"))
    if isinstance(raw, dict) and "error" in raw: raise HTTPException(500, raw["error"])
    meta = _load_custom_meta()
    cs = []
    for c in raw:
        ps = [{"container_port":p.get("PrivatePort"),"host_port":p.get("PublicPort"),"host_ip":p.get("IP","0.0.0.0"),"type":p.get("Type","tcp")} for p in c.get("Ports",[])]
        cid = c.get("Id", "")
        short_id = cid[:12]
        # Get version from labels
        labels = c.get("Labels", {}) or {}
        version = labels.get("org.opencontainers.image.version", "")
        if not version:
            # Try to extract from image tag
            img = c.get("Image", "")
            if ":" in img and "@" not in img:
                tag = img.split(":")[-1]
                if tag and tag != "latest":
                    version = tag
        # Custom name
        custom_name = meta.get("names", {}).get(cid, "")
        description = meta.get("descriptions", {}).get(cid, "")
        cs.append({
            "id": short_id,
            "full_id": cid,
            "name": c.get("Names",[""])[0].lstrip("/"),
            "custom_name": custom_name,
            "description": description,
            "image": c.get("Image",""),
            "version": version,
            "status": c.get("Status",""),
            "state": c.get("State",""),
            "created": c.get("Created",0),
            "ports": ps
        })
    return {"containers": cs, "count": len(cs)}

@app.get("/api/container/{cid}/stats")
async def container_stats(cid: str):
    loop = asyncio.get_event_loop()
    r = await loop.run_in_executor(None, lambda: docker_api("GET", f"/containers/{cid}/stats?stream=false"))
    if isinstance(r, dict) and "error" in r: raise HTTPException(500, r["error"])
    cpu = 0
    try:
        cu,pu = r.get("cpu_stats",{}).get("cpu_usage",{}), r.get("precpu_stats",{}).get("cpu_usage",{})
        cd,sd = cu.get("total_usage",0)-pu.get("total_usage",0), r.get("cpu_stats",{}).get("system_cpu_usage",0)-r.get("precpu_stats",{}).get("system_cpu_usage",0)
        if sd>0 and cd>0: cpu = round(cd/sd*r.get("cpu_stats",{}).get("online_cpus",1)*100,2)
    except: pass
    ms = r.get("memory_stats",{})
    mu,ml = ms.get("usage",0), ms.get("limit",1)
    mp = round(mu/ml*100,2) if ml>0 else 0
    ns = r.get("networks",{})
    net_details = {}
    for iface, ninfo in ns.items():
        net_details[iface] = {"rx_bytes": ninfo.get("rx_bytes",0), "tx_bytes": ninfo.get("tx_bytes",0)}
    return {"id":cid[:12],"cpu_percent":cpu,"memory_usage":mu,"memory_limit":ml,"memory_usage_mb":round(mu/1024/1024,1),"memory_limit_mb":round(ml/1024/1024,1),"memory_percent":mp,"network_rx":sum(n.get("rx_bytes",0) for n in ns.values()),"network_tx":sum(n.get("tx_bytes",0) for n in ns.values()),"network_details":net_details}

@app.get("/api/system")
async def system_info():
    loop = asyncio.get_event_loop()
    raw = await loop.run_in_executor(None, lambda: docker_api("GET", "/containers/json?all=true"))
    ports = []
    if isinstance(raw, list):
        for c in raw:
            if c.get("State") != "running": continue
            n = c.get("Names",[""])[0].lstrip("/")
            for p in c.get("Ports",[]):
                hp = p.get("PublicPort")
                if hp: ports.append({"host_port":hp,"container_port":p.get("PrivatePort"),"protocol":p.get("Type","tcp"),"host_ip":p.get("IP","0.0.0.0"),"container_name":n})
    ports.sort(key=lambda x: x["host_port"])
    seen, up = set(), []
    for p in ports:
        k = (p["host_port"],p["protocol"])
        if k not in seen: seen.add(k); up.append(p)
    return {"memory":get_system_memory(),"cpu":get_system_cpu(),"disk":get_disk_usage(),"network":get_network_stats(),"ports":up,"ports_count":len(up)}

class ActionRequest(BaseModel):
    action: str

class CustomNameRequest(BaseModel):
    name: str

class DescriptionRequest(BaseModel):
    description: str

@app.post("/api/container/{cid}/custom-name")
async def set_custom_name(cid: str, req: CustomNameRequest):
    meta = _load_custom_meta()
    # Find full ID from short ID
    loop = asyncio.get_event_loop()
    raw = await loop.run_in_executor(None, lambda: docker_api("GET", "/containers/json?all=true"))
    if isinstance(raw, list):
        for c in raw:
            if c.get("Id", "").startswith(cid):
                meta["names"][c["Id"]] = req.name
                _save_custom_meta(meta)
                return {"success": True, "cid": cid, "name": req.name}
    raise HTTPException(404, "Container not found")

@app.post("/api/container/{cid}/description")
async def set_description(cid: str, req: DescriptionRequest):
    meta = _load_custom_meta()
    loop = asyncio.get_event_loop()
    raw = await loop.run_in_executor(None, lambda: docker_api("GET", "/containers/json?all=true"))
    if isinstance(raw, list):
        for c in raw:
            if c.get("Id", "").startswith(cid):
                meta["descriptions"][c["Id"]] = req.description
                _save_custom_meta(meta)
                return {"success": True, "cid": cid, "description": req.description}
    raise HTTPException(404, "Container not found")

@app.post("/api/container/{cid}/action")
async def container_action(cid: str, req: ActionRequest):
    act = req.action.lower()
    if act not in ("start","stop","restart"): raise HTTPException(400, f"Invalid: {act}")
    loop = asyncio.get_event_loop()
    r = await loop.run_in_executor(None, lambda: docker_api("POST", f"/containers/{cid}/{act}"))
    if isinstance(r, dict) and "error" in r:
        if r.get("status_code") == 304: return {"success":True,"action":act,"cid":cid[:12],"note":"already in state"}
        raise HTTPException(500, r["error"])
    return {"success":True,"action":act,"cid":cid[:12]}

@app.get("/api/containers/all-stats")
async def all_containers_stats():
    cr = await list_containers()
    cs = cr["containers"]
    running = [c for c in cs if c["state"]=="running"]
    async def fs(c):
        try: c["stats"] = await container_stats(c["id"])
        except: c["stats"] = None
        return c
    await asyncio.gather(*[fs(c) for c in running])
    for c in cs:
        if c["state"]!="running": c["stats"] = None
    l2 = asyncio.get_event_loop()
    return {"containers":cs,"system":{"memory":await l2.run_in_executor(None,get_system_memory),"disk":await l2.run_in_executor(None,get_disk_usage)}}

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
:root {
  --bg:#0f1117;--bg2:#161b22;--card:#1c2333;--card-hover:#242d3d;
  --border:#30363d;--text:#e6edf3;--text-dim:#8b949e;--text-bright:#f0f6fc;
  --accent:#58a6ff;--accent-dim:#1f6feb;
  --green:#3fb950;--green-dim:#238636;--red:#f85149;--red-dim:#da3633;
  --yellow:#d29922;--yellow-dim:#9e6a03;
  --shadow:rgba(0,0,0,0.4);--radius:10px;--radius-sm:8px;--radius-xs:6px;
  --transition:0.2s ease;
}
[data-theme="light"] {--bg:#f6f8fa;--bg2:#fff;--card:#fff;--card-hover:#f3f4f6;--border:#d0d7de;--text:#1f2328;--text-dim:#656d76;--text-bright:#1f2328;--accent:#0969da;--accent-dim:#0550ae;--green:#1a7f37;--green-dim:#116329;--red:#cf222e;--red-dim:#a40e26;--yellow:#9a6700;--yellow-dim:#7d4e00;--shadow:rgba(0,0,0,0.08);}
[data-theme="ocean"] {--bg:#0a1628;--bg2:#0d1f3c;--card:#112645;--card-hover:#163056;--border:#1c3a5f;--text:#c3d4e6;--text-dim:#6b8cae;--text-bright:#e8f0fe;--accent:#38bdf8;--accent-dim:#0284c7;--green:#34d399;--green-dim:#059669;--red:#fb7185;--red-dim:#e11d48;--yellow:#fbbf24;--yellow-dim:#d97706;--shadow:rgba(0,0,0,0.5);}
[data-theme="purple"] {--bg:#13081f;--bg2:#1a0e2e;--card:#221440;--card-hover:#2a1a4d;--border:#3d2666;--text:#d8c8f0;--text-dim:#8b7aab;--text-bright:#f0e8ff;--accent:#c084fc;--accent-dim:#9333ea;--green:#4ade80;--green-dim:#16a34a;--red:#f87171;--red-dim:#dc2626;--yellow:#facc15;--yellow-dim:#ca8a04;--shadow:rgba(0,0,0,0.5);}

*{margin:0;padding:0;box-sizing:border-box;}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans SC',sans-serif;min-height:100vh;transition:background var(--transition),color var(--transition);}

/* ===== HEADER ===== */
.header {display:flex;align-items:center;gap:12px;padding:12px 20px;margin-bottom:14px;background:var(--card);border-radius:var(--radius);border:1px solid var(--border);box-shadow:0 2px 8px var(--shadow);position:sticky;top:0;z-index:100;backdrop-filter:blur(12px);}
.header-left {display:flex;align-items:center;gap:8px;flex-shrink:0;}
.header h1 {font-size:17px;font-weight:700;display:flex;align-items:center;gap:6px;white-space:nowrap;margin:0;}
.header h1 .icon {font-size:20px;}
.hdr-ver {
  font-size:11px;font-weight:600;color:var(--text-dim);
  background:var(--bg2);padding:2px 8px;border-radius:10px;
  border:1px solid var(--border-light);letter-spacing:0.3px;
}

/* Status pills in header - centered */
.hdr-stats {
  display:flex;
  gap:10px;
  flex:1;
  min-width:0;
  justify-content:center;
  flex-wrap:wrap;
}
.hdr-pill {
  display:flex;align-items:center;gap:6px;
  padding:6px 14px;border-radius:20px;border:1px solid var(--border);
  background:var(--bg2);font-size:12px;font-weight:600;white-space:nowrap;
  transition:all var(--transition);
}
.hdr-pill:hover {border-color:var(--accent);}
.hdr-pill .icon {font-size:14px;}
.hdr-pill .val {color:var(--text-bright);}
.hdr-pill .sub {color:var(--text-dim);font-size:10px;font-weight:400;}
.hdr-pill .bar {width:40px;height:4px;background:var(--border);border-radius:2px;overflow:hidden;margin-left:4px;}
.hdr-pill .bar-fill {height:100%;border-radius:2px;transition:width 0.6s ease;}
.hdr-pill.green .val {color:var(--green);}
.hdr-pill.yellow .val {color:var(--yellow);}
.hdr-pill.red .val {color:var(--red);}

.header-right {display:flex;align-items:center;gap:10px;flex-shrink:0;}
.btn-about {background:var(--bg2);color:var(--text-dim);border:1px solid var(--border);padding:7px 14px;border-radius:var(--radius-sm);cursor:pointer;font-size:13px;font-weight:500;transition:all var(--transition);white-space:nowrap;}
.btn-about:hover {border-color:var(--accent);color:var(--text);background:var(--card-hover);}
.theme-switcher {display:flex;gap:3px;background:var(--bg2);border-radius:var(--radius-sm);padding:3px;border:1px solid var(--border);}
.theme-btn {width:26px;height:26px;border-radius:5px;border:2px solid transparent;cursor:pointer;font-size:13px;display:flex;align-items:center;justify-content:center;transition:all var(--transition);background:transparent;color:var(--text);}
.theme-btn:hover {background:var(--card-hover);}
.theme-btn.active {border-color:var(--accent);background:var(--card-hover);}
.refresh-btn {background:var(--accent-dim);color:#fff;border:none;padding:7px 16px;border-radius:var(--radius-sm);cursor:pointer;font-size:13px;font-weight:600;transition:all var(--transition);display:flex;align-items:center;gap:5px;white-space:nowrap;}
.refresh-btn:hover {background:var(--accent);}
.refresh-btn.loading {opacity:0.6;pointer-events:none;}
.refresh-btn .spin {display:inline-block;animation:spin 1s linear infinite;}

/* ===== SEARCH & FILTER ===== */
.toolbar {display:flex;gap:10px;margin-bottom:16px;align-items:center;flex-wrap:wrap;}
.search-box {
  flex:1;min-width:200px;position:relative;
}
.search-box input {
  width:100%;padding:9px 14px 9px 36px;
  background:var(--card);border:1px solid var(--border);border-radius:var(--radius-sm);
  color:var(--text);font-size:13px;transition:all var(--transition);
  outline:none;
}
.search-box input:focus {border-color:var(--accent);box-shadow:0 0 0 3px rgba(88,166,255,0.15);}
.search-box input::placeholder {color:var(--text-dim);}
.search-box .search-icon {position:absolute;left:11px;top:50%;transform:translateY(-50%);font-size:14px;color:var(--text-dim);}
.search-box .clear-btn {position:absolute;right:8px;top:50%;transform:translateY(-50%);cursor:pointer;font-size:16px;color:var(--text-dim);display:none;}
.search-box .clear-btn.show {display:block;}
.search-box .clear-btn:hover {color:var(--text);}

.category-bar {display:flex;gap:6px;flex-wrap:wrap;}
.cat-tab {
  background:var(--card);border:1px solid var(--border);color:var(--text-dim);
  padding:7px 16px;border-radius:20px;cursor:pointer;font-size:12px;font-weight:500;
  transition:all var(--transition);display:flex;align-items:center;gap:5px;
}
.cat-tab:hover {border-color:var(--accent);color:var(--text);}
.cat-tab.active {background:var(--accent-dim);color:#fff;border-color:var(--accent-dim);}
.cat-tab .count {background:rgba(255,255,255,0.15);padding:1px 6px;border-radius:8px;font-size:10px;font-weight:600;}
.cat-tab.active .count {background:rgba(255,255,255,0.25);}

/* Sort bar */
.sort-bar {
  display:flex;align-items:center;gap:4px;
  background:var(--card);border:1px solid var(--border);border-radius:20px;
  padding:3px 12px;
}
.sort-label {font-size:11px;color:var(--text-dim);font-weight:500;margin-right:2px;white-space:nowrap;}
.sort-btn {
  background:transparent;border:1px solid transparent;color:var(--text-dim);
  padding:4px 10px;border-radius:12px;cursor:pointer;font-size:11px;font-weight:500;
  transition:all var(--transition);white-space:nowrap;
}
.sort-btn:hover {color:var(--text);background:var(--card-hover);}
.sort-btn.active {color:var(--accent);border-color:var(--accent);background:rgba(88,166,255,0.08);}

/* ===== LAYOUT ===== */
.main-layout {display:grid;grid-template-columns:260px 1fr;gap:16px;align-items:start;}
.ports-sidebar {position:sticky;top:70px;max-height:calc(100vh - 90px);overflow-y:auto;}
.content-area {min-width:0;}

/* ===== PORTS ===== */
.ports-bar {padding:14px 16px;background:var(--card);border:1px solid var(--border);border-radius:var(--radius);box-shadow:0 2px 8px var(--shadow);}
.ports-bar-header {display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;}
.ports-bar-title {font-size:13px;font-weight:700;display:flex;align-items:center;gap:6px;}
.ports-bar-count {font-size:11px;color:var(--text-dim);background:var(--bg2);padding:2px 8px;border-radius:10px;border:1px solid var(--border);}
.ports-list {display:flex;flex-direction:column;gap:5px;}
.port-item {
  display:flex;align-items:center;gap:6px;padding:7px 10px;
  background:var(--bg2);border:1px solid var(--border-light);border-radius:var(--radius-sm);
  font-size:12px;font-weight:600;font-family:'SF Mono','Cascadia Code','Consolas',monospace;
  color:var(--text-bright);transition:all var(--transition);
}
.port-item:hover {border-color:var(--accent);background:var(--card-hover);transform:translateX(2px);}
.port-item .port-num {color:var(--accent);font-size:14px;font-weight:700;min-width:42px;text-align:right;}
.port-item .port-arrow {color:var(--text-dim);font-size:11px;}
.port-item .port-container {color:var(--text-dim);font-size:10px;font-weight:400;margin-left:auto;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.port-item .port-proto {color:var(--yellow);font-size:9px;font-weight:500;}
.ports-empty {color:var(--text-dim);font-size:12px;padding:6px 0;}

/* ===== CONTAINER ROWS ===== */
.container-rows {display:flex;flex-direction:column;gap:5px;}
.container-row {
  display:flex;align-items:center;gap:10px;padding:9px 14px;
  background:var(--card);border:1px solid var(--border);border-radius:var(--radius-sm);
  transition:all var(--transition);
}
.container-row:hover {border-color:var(--accent);background:var(--card-hover);transform:translateX(2px);box-shadow:0 2px 8px var(--shadow);}
.container-row .row-status {width:7px;height:7px;border-radius:50%;flex-shrink:0;}
.container-row .row-status.running {background:var(--green);box-shadow:0 0 6px var(--green);animation:pulse 2s infinite;}
.container-row .row-status.exited {background:var(--red);}
.container-row .row-status.paused {background:var(--yellow);}
.container-row .row-status.created {background:var(--accent);}
.container-row .row-name {font-size:13px;font-weight:600;min-width:120px;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--text-bright);}
.container-row .row-image {font-size:10px;color:var(--text-dim);font-family:'SF Mono','Cascadia Code',monospace;max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.container-row .row-version {font-size:10px;font-family:'SF Mono','Cascadia Code',monospace;padding:2px 6px;background:rgba(192,132,252,0.1);color:var(--accent);border-radius:3px;border:1px solid rgba(192,132,252,0.2);white-space:nowrap;flex-shrink:0;}
.container-row .row-desc {font-size:11px;color:var(--text-dim);max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex-shrink:0;}
.container-row .row-ports {display:flex;gap:3px;flex-wrap:wrap;flex:1;min-width:0;}
.container-row .row-port {font-size:10px;font-family:'SF Mono','Cascadia Code',monospace;padding:2px 5px;background:rgba(88,166,255,0.08);color:var(--accent);border-radius:3px;border:1px solid rgba(88,166,255,0.15);}
.container-row .row-stats {display:flex;gap:10px;font-size:10px;color:var(--text-dim);min-width:110px;justify-content:flex-end;}
.container-row .row-actions {display:flex;gap:5px;flex-shrink:0;}
.container-row .row-actions button {padding:3px 8px;font-size:10px;font-weight:600;border-radius:3px;cursor:pointer;background:transparent;border:1px solid var(--border);color:var(--text);transition:all var(--transition);}
.container-row .row-actions button:disabled {opacity:0.3;pointer-events:none;}
.container-row .row-actions .start:not(:disabled) {border-color:var(--green-dim);color:var(--green);}
.container-row .row-actions .start:hover:not(:disabled) {background:rgba(63,185,80,0.1);}
.container-row .row-actions .stop:not(:disabled) {border-color:var(--red-dim);color:var(--red);}
.container-row .row-actions .stop:hover:not(:disabled) {background:rgba(248,81,73,0.1);}
.container-row .row-actions .restart:not(:disabled) {border-color:var(--yellow-dim);color:var(--yellow);}
.container-row .row-actions .restart:hover:not(:disabled) {background:rgba(210,153,34,0.1);}

/* ===== SECTIONS ===== */
.section-header {display:flex;align-items:center;gap:8px;margin:20px 0 12px;padding-bottom:8px;border-bottom:1px solid var(--border);}
.section-header:first-child {margin-top:0;}
.section-title {font-size:14px;font-weight:700;display:flex;align-items:center;gap:6px;}
.section-title .dot {width:7px;height:7px;border-radius:50%;display:inline-block;}
.section-title .dot.green {background:var(--green);box-shadow:0 0 6px var(--green);}
.section-title .dot.gray {background:var(--text-dim);}
.section-count {font-size:11px;color:var(--text-dim);background:var(--bg2);padding:2px 7px;border-radius:8px;border:1px solid var(--border);}

/* ===== MISC ===== */
.error-banner {background:rgba(248,81,73,0.08);border:1px solid rgba(248,81,73,0.3);border-radius:var(--radius-sm);padding:10px 14px;margin-bottom:14px;font-size:13px;color:var(--red);}
.toast {position:fixed;bottom:20px;right:20px;padding:10px 20px;background:var(--card);border:1px solid var(--border);border-radius:var(--radius-sm);box-shadow:0 4px 16px var(--shadow);font-size:13px;font-weight:500;transform:translateY(80px);opacity:0;transition:all 0.3s ease;z-index:200;}
.toast.show {transform:translateY(0);opacity:1;}
.toast.success {border-left:3px solid var(--green);}
.toast.error {border-left:3px solid var(--red);}
.no-results {text-align:center;padding:30px;color:var(--text-dim);font-size:13px;}

/* ===== RESPONSIVE ===== */
@media(max-width:1100px) {
  .hdr-pill .bar {display:none;}
}
@media(max-width:900px) {
  .header {flex-wrap:wrap;}
  .hdr-stats {order:3;width:100%;flex-wrap:wrap;}
  .main-layout {grid-template-columns:1fr;}
  .ports-sidebar {position:static;max-height:none;}
  .container-row {flex-wrap:wrap;}
  .container-row .row-stats {width:100%;justify-content:flex-start;}
}
@media(max-width:600px) {
  .header {padding:10px 14px;}
  .header h1 {font-size:15px;}
  .hdr-pill {padding:4px 10px;font-size:11px;}
  .theme-switcher {display:none;}
}

@keyframes spin{to{transform:rotate(360deg);}}
@keyframes pulse{0%,100%{opacity:1;}50%{opacity:0.5;}}

/* ===== DETAIL PANEL ===== */
.detail-overlay {
  display:none; position:fixed; top:0; left:0; right:0; bottom:0;
  background:rgba(0,0,0,0.5); z-index:300; backdrop-filter:blur(4px);
  justify-content:center; align-items:center;
}
.detail-overlay.show { display:flex; }
.detail-panel {
  background:var(--card); border:1px solid var(--border); border-radius:var(--radius);
  box-shadow:0 8px 32px var(--shadow); padding:24px 28px; min-width:480px; max-width:640px;
  max-height:80vh; overflow-y:auto; animation:fadeIn 0.2s ease;
}
.detail-panel .close-btn {
  float:right; cursor:pointer; font-size:20px; color:var(--text-dim);
  transition:color var(--transition); line-height:1;
}
.detail-panel .close-btn:hover { color:var(--text); }
.detail-panel .detail-header {
  display:flex; align-items:center; gap:10px; margin-bottom:16px;
}
.detail-panel .detail-header .status-dot {
  width:10px; height:10px; border-radius:50%; flex-shrink:0;
}
.detail-panel .detail-header .status-dot.running { background:var(--green); box-shadow:0 0 8px var(--green); }
.detail-panel .detail-header .status-dot.exited { background:var(--red); }
.detail-panel .detail-header .status-dot.paused { background:var(--yellow); }
.detail-panel .detail-header .status-dot.created { background:var(--accent); }
.detail-panel .detail-header h2 {
  font-size:18px; font-weight:700; color:var(--text-bright); flex:1;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
}
.detail-panel .detail-section {
  margin-bottom:14px; padding-bottom:14px; border-bottom:1px solid var(--border);
}
.detail-panel .detail-section:last-child { border-bottom:none; margin-bottom:0; padding-bottom:0; }
.detail-panel .detail-label {
  font-size:11px; color:var(--text-dim); font-weight:600; text-transform:uppercase;
  letter-spacing:0.5px; margin-bottom:6px;
}
.detail-panel .detail-value {
  font-size:13px; color:var(--text); line-height:1.6;
  word-break:break-all;
}
.detail-panel .detail-value.mono {
  font-family:'SF Mono','Cascadia Code','Consolas',monospace;
  font-size:12px; background:var(--bg2); padding:8px 12px; border-radius:var(--radius-xs);
  border:1px solid var(--border-light);
}
.detail-panel .detail-stats {
  display:grid; grid-template-columns:repeat(3,1fr); gap:10px;
}
.detail-panel .detail-stat {
  background:var(--bg2); border:1px solid var(--border-light); border-radius:var(--radius-sm);
  padding:10px 12px; text-align:center;
}
.detail-panel .detail-stat .stat-val { font-size:18px; font-weight:700; color:var(--text-bright); }
.detail-panel .detail-stat .stat-label { font-size:10px; color:var(--text-dim); margin-top:2px; }
.detail-panel .detail-ports {
  display:flex; flex-wrap:wrap; gap:5px;
}
.detail-panel .detail-port {
  font-size:11px; font-family:'SF Mono','Cascadia Code',monospace;
  padding:3px 8px; background:rgba(88,166,255,0.08); color:var(--accent);
  border-radius:3px; border:1px solid rgba(88,166,255,0.15);
}
.detail-panel .detail-actions {
  display:flex; gap:8px; margin-top:16px; padding-top:16px; border-top:1px solid var(--border);
}
.detail-panel .detail-actions button {
  flex:1; padding:9px 16px; border-radius:var(--radius-sm); cursor:pointer;
  font-size:13px; font-weight:600; border:1px solid var(--border);
  background:transparent; color:var(--text); transition:all var(--transition);
}
.detail-panel .detail-actions button:hover { background:var(--card-hover); }
.detail-panel .detail-actions button:disabled { opacity:0.3; pointer-events:none; }
.detail-panel .detail-actions .btn-start { border-color:var(--green-dim); color:var(--green); }
.detail-panel .detail-actions .btn-start:hover:not(:disabled) { background:rgba(63,185,80,0.1); }
.detail-panel .detail-actions .btn-stop { border-color:var(--red-dim); color:var(--red); }
.detail-panel .detail-actions .btn-stop:hover:not(:disabled) { background:rgba(248,81,73,0.1); }
.detail-panel .detail-actions .btn-restart { border-color:var(--yellow-dim); color:var(--yellow); }
.detail-panel .detail-actions .btn-restart:hover:not(:disabled) { background:rgba(210,153,34,0.1); }

/* ===== UPDATE MODAL ===== */
.hdr-update-btn {
  font-size:11px;font-weight:600;padding:3px 10px;border-radius:10px;
  border:1px solid var(--yellow-dim);background:rgba(210,153,34,0.1);
  color:var(--yellow);cursor:pointer;transition:all var(--transition);
  animation:updatePulse 2s ease-in-out infinite;
}
.hdr-update-btn:hover { background:rgba(210,153,34,0.25); }
@keyframes updatePulse {
  0%,100% { box-shadow:0 0 0 0 rgba(210,153,34,0.3); }
  50% { box-shadow:0 0 8px 2px rgba(210,153,34,0.2); }
}
/* ===== MODAL ===== */
.modal-overlay {display:none;position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:300;align-items:center;justify-content:center;}
.modal-overlay.show {display:flex;}
.modal-box {background:var(--card);border:1px solid var(--border);border-radius:var(--radius);box-shadow:0 8px 32px var(--shadow);max-height:80vh;overflow-y:auto;width:90%;}
.modal-header {display:flex;align-items:center;justify-content:space-between;padding:14px 18px;border-bottom:1px solid var(--border);}
.modal-header h3 {font-size:16px;font-weight:700;color:var(--text-bright);margin:0;}
.modal-close {background:none;border:none;color:var(--text-dim);font-size:18px;cursor:pointer;padding:4px 8px;border-radius:4px;}
.modal-close:hover {color:var(--text);background:var(--card-hover);}
.modal-body {padding:18px;}

/* ===== UPDATE MODAL ===== */
.update-overlay {
  display:none;position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:300;align-items:center;justify-content:center;}
  background:rgba(0,0,0,0.5);z-index:400;backdrop-filter:blur(4px);
  justify-content:center;align-items:center;
}
.update-overlay.show { display:flex; }
.update-panel {
  background:var(--card);border:1px solid var(--border);border-radius:var(--radius);
  box-shadow:0 8px 32px var(--shadow);padding:28px 32px;min-width:420px;max-width:520px;
  max-height:70vh;overflow-y:auto;animation:fadeIn 0.2s ease;
}
.update-panel .update-header {
  display:flex;align-items:center;gap:10px;margin-bottom:16px;
}
.update-panel .update-header h2 { font-size:18px;font-weight:700;color:var(--text-bright);flex:1; }
.update-panel .update-header .close-btn {
  cursor:pointer;font-size:20px;color:var(--text-dim);transition:color var(--transition);line-height:1;
}
.update-panel .update-header .close-btn:hover { color:var(--text); }
.update-panel .update-meta {
  font-size:12px;color:var(--text-dim);margin-bottom:12px;
  padding-bottom:12px;border-bottom:1px solid var(--border);
}
.update-panel .update-meta span { margin-right:12px; }
.update-panel .update-changelog {
  margin-bottom:16px;
}
.update-panel .update-changelog .cl-title {
  font-size:12px;font-weight:600;color:var(--text-dim);text-transform:uppercase;
  letter-spacing:0.5px;margin-bottom:8px;
}
.update-panel .update-changelog ul {
  list-style:none;padding:0;margin:0;
}
.update-panel .update-changelog ul li {
  font-size:13px;color:var(--text);padding:4px 0 4px 16px;position:relative;
}
.update-panel .update-changelog ul li::before {
  content:"•";color:var(--accent);position:absolute;left:0;font-size:16px;line-height:1.2;
}
.update-panel .update-actions {
  display:flex;gap:10px;margin-top:16px;padding-top:16px;border-top:1px solid var(--border);
}
.update-panel .update-actions button {
  flex:1;padding:10px 16px;border-radius:var(--radius-sm);cursor:pointer;
  font-size:13px;font-weight:600;border:1px solid var(--border);
  background:transparent;color:var(--text);transition:all var(--transition);
}
.update-panel .update-actions button:hover { background:var(--card-hover); }
.update-panel .update-actions button:disabled { opacity:0.5;pointer-events:none; }
.update-panel .update-actions .btn-update {
  border-color:var(--green-dim);color:var(--green);background:rgba(63,185,80,0.08);
}
.update-panel .update-actions .btn-update:hover:not(:disabled) { background:rgba(63,185,80,0.15); }
.update-panel .update-progress {
  display:none;margin-top:12px;padding:10px 14px;background:var(--bg2);
  border-radius:var(--radius-sm);font-size:12px;color:var(--text-dim);
  border:1px solid var(--border);
}
.update-panel .update-progress.show { display:block; }
.update-panel .update-progress .progress-bar {
  width:100%;height:4px;background:var(--border);border-radius:2px;
  margin-top:6px;overflow:hidden;
}
.update-panel .update-progress .progress-fill {
  height:100%;background:var(--green);border-radius:2px;transition:width 0.3s ease;
}
</style>
</head>
<body>

<div class="header">
  <div class="header-left">
    <span style="font-size:20px">🐳</span>
    <span style="font-size:17px;font-weight:700;white-space:nowrap;">Docker Panel</span>
  </div>
  <div class="hdr-stats" id="hdrStats"></div>
  <div class="header-right">
    <button class="btn-about" onclick="showAboutModal()">关于</button>
    <div class="theme-switcher">
      <button class="theme-btn active" data-theme="dark" title="暗色">🌙</button>
      <button class="theme-btn" data-theme="light" title="亮色">☀️</button>
      <button class="theme-btn" data-theme="ocean" title="海洋蓝">🌊</button>
      <button class="theme-btn" data-theme="purple" title="紫色">🔮</button>
    </div>
    <button class="refresh-btn" id="refreshBtn" onclick="loadData()"><span id="refreshIcon">⟳</span> 加载</button>
  </div>
</div>

<div id="errorBanner"></div>

<div class="toolbar">
  <div class="category-bar">
    <div class="cat-tab active" data-cat="all" onclick="setCategory('all')">📦 全部 <span class="count" id="count-all">0</span></div>
    <div class="cat-tab" data-cat="running" onclick="setCategory('running')">🟢 使用中 <span class="count" id="count-running">0</span></div>
    <div class="cat-tab" data-cat="stopped" onclick="setCategory('stopped')">🔴 未使用 <span class="count" id="count-stopped">0</span></div>
  </div>
  <div class="sort-bar">
    <span class="sort-label">排序:</span>
    <button class="sort-btn active" data-sort="name" onclick="setSort('name')">名称</button>
    <button class="sort-btn" data-sort="cpu" onclick="setSort('cpu')">CPU</button>
    <button class="sort-btn" data-sort="mem" onclick="setSort('mem')">内存</button>
    <button class="sort-btn" data-sort="net" onclick="setSort('net')">网络</button>
  </div>
</div>

<div class="main-layout">
  <div class="ports-sidebar">
    <div class="ports-bar">
      <div class="ports-bar-header">
        <div class="ports-bar-title">🔌 已使用端口</div>
        <span class="ports-bar-count" id="portsCount">0</span>
      </div>
      <div class="ports-list" id="portsList"></div>
    </div>
  </div>
  <div class="content-area">
    <!-- 搜索框在容器列表上方 -->
    <div class="search-box">
      <span class="search-icon">🔍</span>
      <input type="text" id="searchInput" placeholder="搜索容器名称 / 镜像..." oninput="onSearch()">
      <span class="clear-btn" id="clearBtn" onclick="clearSearch()">✕</span>
    </div>
    <div id="sectionsContainer"></div>
  </div>
</div>

<!-- 容器详情大胶囊 -->
<div class="detail-overlay" id="detailOverlay" onclick="closeDetail(event)">
  <div class="detail-panel" id="detailPanel"></div>
</div>

<!-- CPU Modal -->
<div class="modal-overlay" id="cpuModal" onclick="if(event.target===this)closeModal('cpuModal')">
  <div class="modal-box" style="max-width:360px">
    <div class="modal-header"><h3>⚡ CPU 详情</h3><button class="modal-close" onclick="closeModal('cpuModal')">✕</button></div>
    <div class="modal-body" id="cpuModalBody"></div>
  </div>
</div>

<!-- Memory Modal -->
<div class="modal-overlay" id="memModal" onclick="if(event.target===this)closeModal('memModal')">
  <div class="modal-box" style="max-width:360px">
    <div class="modal-header"><h3>🧠 内存详情</h3><button class="modal-close" onclick="closeModal('memModal')">✕</button></div>
    <div class="modal-body" id="memModalBody"></div>
  </div>
</div>

<!-- Network Modal -->
<div class="modal-overlay" id="netModal" onclick="if(event.target===this)closeModal('netModal')">
  <div class="modal-box" style="max-width:420px">
    <div class="modal-header"><h3>🌐 网络详情</h3><button class="modal-close" onclick="closeModal('netModal')">✕</button></div>
    <div class="modal-body" id="netModalBody"></div>
  </div>
</div>

<!-- Disk Modal -->
<div class="modal-overlay" id="diskModal" onclick="if(event.target===this)closeModal('diskModal')">
  <div class="modal-box" style="max-width:420px">
    <div class="modal-header"><h3>💾 磁盘详情</h3><button class="modal-close" onclick="closeModal('diskModal')">✕</button></div>
    <div class="modal-body" id="diskModalBody" style="text-align:center"></div>
  </div>
</div>

<!-- Containers Modal -->
<div class="modal-overlay" id="containersModal" onclick="if(event.target===this)closeModal('containersModal')">
  <div class="modal-box" style="max-width:500px">
    <div class="modal-header"><h3>🐳 容器列表</h3><button class="modal-close" onclick="closeModal('containersModal')">✕</button></div>
    <div class="modal-body" id="containersModalBody"></div>
  </div>
</div>

<!-- About Modal -->
<div class="modal-overlay" id="aboutModal" onclick="if(event.target===this)closeModal('aboutModal')">
  <div class="modal-box" style="max-width:400px">
    <div class="modal-header"><h3>关于</h3><button class="modal-close" onclick="closeModal('aboutModal')">✕</button></div>
    <div class="modal-body" id="aboutModalBody"></div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
const API = '';
let allContainers = [];
let lastSysData = null;
let currentCategory = 'all';
let searchQuery = '';
let currentSort = 'name';

// Theme
document.querySelectorAll('.theme-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.documentElement.setAttribute('data-theme', btn.dataset.theme);
    document.querySelectorAll('.theme-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    localStorage.setItem('docker-panel-theme', btn.dataset.theme);
  });
});
const st = localStorage.getItem('docker-panel-theme');
if (st) {
  document.documentElement.setAttribute('data-theme', st);
  document.querySelectorAll('.theme-btn').forEach(b => b.classList.toggle('active', b.dataset.theme === st));
}

// Search
function onSearch() {
  searchQuery = document.getElementById('searchInput').value.trim().toLowerCase();
  document.getElementById('clearBtn').classList.toggle('show', searchQuery.length > 0);
  renderContainers();
}
function clearSearch() {
  document.getElementById('searchInput').value = '';
  searchQuery = '';
  document.getElementById('clearBtn').classList.remove('show');
  renderContainers();
}

async function loadData() {
  const btn = document.getElementById('refreshBtn'), icon = document.getElementById('refreshIcon');
  btn.classList.add('loading'); icon.classList.add('spin');
  document.getElementById('errorBanner').innerHTML = '';
  try {
    const [rA, rS] = await Promise.all([fetch(API+'/api/containers/all-stats'), fetch(API+'/api/system')]);
    if (!rA.ok) { const e = await rA.json(); throw new Error(e.detail || 'API error'); }
    const data = await rA.json(), sysData = await rS.json();
    if (data.containers) allContainers = data.containers;
    const sys = {...(data.system||{}), ...(sysData||{})};
    lastSysData = sys;
    renderPorts(sys);
    renderHdrStats(sys);
    renderCategories();
    renderContainers();
  } catch(e) {
    document.getElementById('errorBanner').innerHTML = '<div class="error-banner">⚠️ 加载失败: '+e.message+'</div>';
  } finally {
    btn.classList.remove('loading'); icon.classList.remove('spin');
  }
}

function renderPorts(sys) {
  const ports = sys.ports || [];
  document.getElementById('portsCount').textContent = ports.length + ' 个端口';
  const list = document.getElementById('portsList');
  if (!ports.length) { list.innerHTML = '<div class="ports-empty">暂无运行中的容器暴露端口</div>'; return; }
  list.innerHTML = ports.map(p => {
    const n = p.container_name || '';
    return `<div class="port-item" title="${n}"><span class="port-num">${p.host_port}</span><span class="port-arrow">→</span><span>${p.container_port}</span><span class="port-proto">${p.protocol}</span>${n?`<span class="port-container">(${n})</span>`:''}</div>`;
  }).join('');
}

function renderHdrStats(sys) {
  const mem = sys.memory || {}, cpu = sys.cpu || {}, disk = sys.disk || {}, net = sys.network || {};
  const running = allContainers.filter(c=>c.state==='running').length, total = allContainers.length;
  let html = '';

  // CPU - clickable, opens detail modal
  const cpuPct = cpu.use_percent || 0;
  const cpuColor = cpuPct > 85 ? 'var(--red)' : cpuPct > 65 ? 'var(--yellow)' : 'var(--green)';
  html += `<div class="hdr-pill" style="cursor:pointer" onclick="showCpuModal()" title="CPU 详情"><span class="icon">⚡</span><span class="val" style="color:${cpuColor}">${cpuPct}%</span><span class="sub">CPU</span><div class="bar"><div class="bar-fill" style="width:${cpuPct}%;background:${cpuColor}"></div></div></div>`;

  // Memory - clickable, opens detail modal
  if (mem.total_mb) {
    const pct = mem.use_percent||0, c = pct>85?'var(--red)':pct>65?'var(--yellow)':'var(--green)';
    html += `<div class="hdr-pill" style="cursor:pointer" onclick="showMemModal()" title="内存: ${mem.used_mb}MB / ${mem.total_mb}MB"><span class="icon">🧠</span><span class="val" style="color:${c}">${mem.use_percent}%</span><span class="sub">内存</span><div class="bar"><div class="bar-fill" style="width:${pct}%;background:${c}"></div></div></div>`;
  }

  // Network - clickable, opens detail modal
  if (net.speed_rx !== undefined) {
    const speedRx = formatSpeed(net.speed_rx);
    const speedTx = formatSpeed(net.speed_tx);
    const totalRx = formatBytes(net.total_rx_bytes||0);
    const totalTx = formatBytes(net.total_tx_bytes||0);
    html += `<div class="hdr-pill" style="cursor:pointer" onclick="showNetModal()" title="总下载: ${totalRx} | 总上传: ${totalTx}"><span class="icon">🌐</span><span class="val">↓${speedRx}</span><span class="sub">↑${speedTx}</span></div>`;
  }

  // Disk - clickable, opens donut chart modal
  const diskKeys = Object.keys(disk);
  if (diskKeys.length > 0) {
    const d0 = disk[diskKeys[0]];
    const dPct = parseInt(d0.use_percent) || 0;
    const dColor = dPct > 85 ? 'var(--red)' : dPct > 65 ? 'var(--yellow)' : 'var(--green)';
    const totalDiskG = (Object.values(disk).reduce((s,d) => s + (d.total_bytes||0), 0) / (1024*1024*1024)).toFixed(1);
    const usedDiskG = (Object.values(disk).reduce((s,d) => s + (d.used_bytes||0), 0) / (1024*1024*1024)).toFixed(1);
    html += `<div class="hdr-pill" style="cursor:pointer" onclick="showDiskModal()" title="磁盘: ${usedDiskG}G / ${totalDiskG}G"><span class="icon">💾</span><span class="val" style="color:${dColor}">${d0.use_percent}%</span><span class="sub">磁盘</span><div class="bar"><div class="bar-fill" style="width:${dPct}%;background:${dColor}"></div></div></div>`;
  }

  // Containers count - clickable, opens container list modal
  html += `<div class="hdr-pill green" style="cursor:pointer" onclick="showContainersModal()" title="容器列表"><span class="icon">🐳</span><span class="val">${running}/${total}</span><span class="sub">容器</span></div>`;

  document.getElementById('hdrStats').innerHTML = html;
}

function formatSpeed(bytesPerSec) {
  if (bytesPerSec < 1024) return bytesPerSec+'B/s';
  if (bytesPerSec < 1048576) return (bytesPerSec/1024).toFixed(1)+'KB/s';
  if (bytesPerSec < 1073741824) return (bytesPerSec/1048576).toFixed(1)+'MB/s';
  return (bytesPerSec/1073741824).toFixed(1)+'GB/s';
}

function renderCategories() {
  const filtered = getFiltered();
  document.getElementById('count-all').textContent = filtered.length;
  document.getElementById('count-running').textContent = filtered.filter(c=>c.state==='running').length;
  document.getElementById('count-stopped').textContent = filtered.filter(c=>c.state!=='running').length;
}

function getFiltered() {
  let cs = [...allContainers];
  // category filter
  if (currentCategory === 'running') cs = cs.filter(c => c.state === 'running');
  else if (currentCategory === 'stopped') cs = cs.filter(c => c.state !== 'running');
  // search filter
  if (searchQuery) cs = cs.filter(c => (c.name||'').toLowerCase().includes(searchQuery) || (c.image||'').toLowerCase().includes(searchQuery));
  // sort
  switch(currentSort) {
    case 'cpu':
      cs.sort((a,b) => (b.stats?.cpu_percent||0) - (a.stats?.cpu_percent||0));
      break;
    case 'mem':
      cs.sort((a,b) => (b.stats?.memory_percent||0) - (a.stats?.memory_percent||0));
      break;
    case 'net':
      cs.sort((a,b) => ((b.stats?.network_rx||0)+(b.stats?.network_tx||0)) - ((a.stats?.network_rx||0)+(a.stats?.network_tx||0)));
      break;
    default: // name
      cs.sort((a,b) => (a.name||'').localeCompare(b.name||'', 'zh-CN'));
  }
  return cs;
}

function setSort(sort) {
  currentSort = sort;
  document.querySelectorAll('.sort-btn').forEach(b => b.classList.toggle('active', b.dataset.sort === sort));
  renderContainers();
}

function setCategory(cat) {
  currentCategory = cat;
  document.querySelectorAll('.cat-tab').forEach(t => t.classList.toggle('active', t.dataset.cat === cat));
  renderCategories();
  renderContainers();
}

function renderContainers() {
  const el = document.getElementById('sectionsContainer');
  const filtered = getFiltered();
  let running = filtered.filter(c => c.state === 'running');
  let stopped = filtered.filter(c => c.state !== 'running');

  if (!running.length && !stopped.length) {
    const msg = searchQuery ? `未找到匹配 "${searchQuery}" 的容器` : (currentCategory==='all'?'没有容器':currentCategory==='running'?'没有运行中的容器':'没有未使用的容器');
    el.innerHTML = `<div class="no-results">📦 ${msg}</div>`;
    return;
  }

  let html = '';
  if (running.length) {
    html += `<div class="section-header"><div class="section-title"><span class="dot green"></span>使用中</div><span class="section-count">${running.length} 个容器</span></div>`;
    html += '<div class="container-rows">' + running.map((c,i)=>renderRow(c,i)).join('') + '</div>';
  }
  if (stopped.length) {
    html += `<div class="section-header"><div class="section-title"><span class="dot gray"></span>未使用</div><span class="section-count">${stopped.length} 个容器</span></div>`;
    html += '<div class="container-rows">' + stopped.map((c,i)=>renderRow(c,i)).join('') + '</div>';
  }
  el.innerHTML = html;
}

function renderRow(c, i) {
  const state = c.state||'unknown';
  const isRunning = state==='running', isStopped = state==='exited'||state==='dead', isCreated = state==='created';
  const portsHtml = (c.ports||[]).filter(p=>p.host_port).map(p=>`<span class="row-port">${p.host_port}→${p.container_port}/${p.type}</span>`).join('');
  const statsHtml = (c.stats&&isRunning) ? `<span>CPU ${c.stats.cpu_percent||0}%</span><span>MEM ${c.stats.memory_usage_mb||0}/${c.stats.memory_limit_mb||0}MB</span>` : '<span>-</span>';
  // Display name: custom name + original name in brackets
  const displayName = c.custom_name ? `<span class="row-name" title="${esc(c.name)}">${esc(c.custom_name)} <span style="color:var(--text-dim);font-size:11px;font-weight:400">(${esc(c.name)})</span></span>` : `<span class="row-name" title="${esc(c.name)}">${esc(c.name)}</span>`;
  // Version badge
  const versionHtml = c.version ? `<span class="row-version" title="版本">${esc(c.version)}</span>` : '';
  // Description
  const descHtml = c.description ? `<span class="row-desc" title="${esc(c.description)}">📝 ${esc(c.description)}</span>` : '';
  return `<div class="container-row" id="card-${c.id}" onclick="showDetail(allContainers.find(x=>x.id==='${c.id}'))">
    <span class="row-status ${state}"></span>
    ${displayName}
    ${versionHtml}
    <span class="row-image" title="${esc(c.image||'')}">${esc(c.image||'-')}</span>
    ${descHtml}
    <div class="row-ports">${portsHtml||'<span style="color:var(--text-dim);font-size:10px">无端口</span>'}</div>
    <div class="row-stats">${statsHtml}</div>
    <div class="row-actions">
      <button class="start" ${isRunning?'disabled':''} onclick="event.stopPropagation();doAction('${c.id}','start',this)">▶</button>
      <button class="stop" ${(isStopped||isCreated)?'disabled':''} onclick="event.stopPropagation();doAction('${c.id}','stop',this)">⏹</button>
      <button class="restart" ${(isStopped||isCreated)?'disabled':''} onclick="event.stopPropagation();doAction('${c.id}','restart',this)">⟳</button>
    </div>
  </div>`;
}

function esc(s) { const d=document.createElement('div'); d.textContent=s; return d.innerHTML; }

// ===== DETAIL PANEL =====
function showDetail(c) {
  const state = c.state||'unknown';
  const isRunning = state==='running', isStopped = state==='exited'||state==='dead', isCreated = state==='created';
  const portsHtml = (c.ports||[]).filter(p=>p.host_port).map(p=>`<span class="detail-port">${p.host_port}→${p.container_port}/${p.type}</span>`).join('') || '<span style="color:var(--text-dim)">无端口映射</span>';
  const cpu = c.stats ? (c.stats.cpu_percent||0)+'%' : '-';
  const memUse = c.stats ? (c.stats.memory_usage_mb||0)+'MB' : '-';
  const memLimit = c.stats ? (c.stats.memory_limit_mb||0)+'MB' : '-';
  const memPct = c.stats ? (c.stats.memory_percent||0)+'%' : '-';
  const netRx = c.stats ? formatBytes(c.stats.network_rx||0) : '-';
  const netTx = c.stats ? formatBytes(c.stats.network_tx||0) : '-';
  // Per-interface network stats from container detail
  const netDetails = c.stats?.network_details || null;
  let netDetailHtml = '';
  if (netDetails) {
    for (const [iface, info] of Object.entries(netDetails)) {
      netDetailHtml += `<div style="display:flex;gap:8px;margin-top:4px;font-size:11px;color:var(--text-dim)">
        <span style="color:var(--accent);font-weight:600">${iface}</span>
        <span>↓${formatBytes(info.rx_bytes||0)}</span>
        <span>↑${formatBytes(info.tx_bytes||0)}</span>
      </div>`;
    }
  }
  const versionHtml = c.version ? `<div class="detail-value" style="margin-top:4px"><b>版本:</b> ${esc(c.version)}</div>` : '';
  const customNameVal = c.custom_name || '';
  const descVal = c.description || '';

  document.getElementById('detailPanel').innerHTML = `
    <span class="close-btn" onclick="closeDetail()">✕</span>
    <div class="detail-header">
      <span class="status-dot ${state}"></span>
      <h2>${esc(c.custom_name || c.name)}</h2>
    </div>
    <div class="detail-section">
      <div class="detail-label">基本信息</div>
      <div class="detail-value"><b>ID:</b> ${esc(c.id)} &nbsp; <b>状态:</b> ${esc(c.status||state)}</div>
      <div class="detail-value mono" style="margin-top:6px">${esc(c.image||'-')}</div>
      ${versionHtml}
      <div class="detail-value" style="margin-top:4px;font-size:12px;color:var(--text-dim)"><b>原名:</b> ${esc(c.name)}</div>
    </div>
    <div class="detail-section">
      <div class="detail-label">自定义名称</div>
      <div style="display:flex;gap:6px;align-items:center">
        <input type="text" id="customNameInput" value="${esc(customNameVal)}" placeholder="输入自定义名称..." style="flex:1;padding:7px 10px;background:var(--bg2);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px;outline:none" onfocus="this.style.borderColor='var(--accent)'" onblur="this.style.borderColor='var(--border)'">
        <button onclick="saveCustomName('${c.id}')" style="padding:7px 14px;background:var(--accent-dim);color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:12px;font-weight:600">保存</button>
      </div>
    </div>
    <div class="detail-section">
      <div class="detail-label">容器用途</div>
      <div style="display:flex;gap:6px;align-items:flex-start">
        <textarea id="descInput" placeholder="输入容器用途描述..." rows="3" style="flex:1;padding:7px 10px;background:var(--bg2);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px;outline:none;resize:vertical;font-family:inherit" onfocus="this.style.borderColor='var(--accent)'" onblur="this.style.borderColor='var(--border)'">${esc(descVal)}</textarea>
      </div>
      <button onclick="saveDescription('${c.id}')" style="margin-top:6px;padding:7px 14px;background:var(--accent-dim);color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:12px;font-weight:600">保存用途</button>
    </div>
    <div class="detail-section">
      <div class="detail-label">网络流量</div>
      <div class="detail-stats">
        <div class="detail-stat"><div class="stat-val">${netRx}</div><div class="stat-label">下载总量</div></div>
        <div class="detail-stat"><div class="stat-val">${netTx}</div><div class="stat-label">上传总量</div></div>
        <div class="detail-stat"><div class="stat-val">${c.stats ? formatBytes((c.stats.network_rx||0)+(c.stats.network_tx||0)) : '-'}</div><div class="stat-label">总流量</div></div>
      </div>
      ${netDetailHtml ? `<div style="margin-top:10px;padding-top:10px;border-top:1px solid var(--border)"><div style="font-size:10px;color:var(--text-dim);font-weight:600;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px">按网卡统计</div>${netDetailHtml}</div>` : ''}
    </div>
    <div class="detail-section">
      <div class="detail-label">资源使用</div>
      <div class="detail-stats">
        <div class="detail-stat"><div class="stat-val">${cpu}</div><div class="stat-label">CPU</div></div>
        <div class="detail-stat"><div class="stat-val">${memUse}</div><div class="stat-label">内存 / ${memLimit}</div></div>
        <div class="detail-stat"><div class="stat-val">${memPct}</div><div class="stat-label">内存占用</div></div>
      </div>
    </div>
    <div class="detail-section">
      <div class="detail-label">端口映射</div>
      <div class="detail-ports">${portsHtml}</div>
    </div>
    <div class="detail-actions">
      <button class="btn-start" ${isRunning?'disabled':''} onclick="doAction('${c.id}','start',this);closeDetail()">▶ 启动</button>
      <button class="btn-stop" ${(isStopped||isCreated)?'disabled':''} onclick="doAction('${c.id}','stop',this);closeDetail()">⏹ 停止</button>
      <button class="btn-restart" ${(isStopped||isCreated)?'disabled':''} onclick="doAction('${c.id}','restart',this);closeDetail()">⟳ 重启</button>
    </div>
  `;
  document.getElementById('detailOverlay').classList.add('show');
}
function closeDetail(e) {
  if (!e || e.target===document.getElementById('detailOverlay') || e.target.classList.contains('close-btn'))
    document.getElementById('detailOverlay').classList.remove('show');
}
function formatBytes(b) {
  if (b<1024) return b+'B';
  if (b<1048576) return (b/1024).toFixed(1)+'KB';
  if (b<1073741824) return (b/1048576).toFixed(1)+'MB';
  return (b/1073741824).toFixed(1)+'GB';
}

async function doAction(id, action, btn) {
  const labels={start:'启动',stop:'停止',restart:'重启'};
  btn.classList.add('loading'); btn.disabled=true;
  try {
    const r = await fetch(API+`/api/container/${id}/action`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action})});
    if (!r.ok) { const e=await r.json(); throw new Error(e.detail||'操作失败'); }
    showToast(labels[action]+' 成功','success');
    setTimeout(loadData,1500);
  } catch(e) {
    showToast(labels[action]+' 失败: '+e.message,'error');
    btn.classList.remove('loading'); btn.disabled=false;
  }
}

async function saveCustomName(id) {
  const input = document.getElementById('customNameInput');
  const name = input.value.trim();
  try {
    const r = await fetch(API+`/api/container/${id}/custom-name`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})});
    if (!r.ok) { const e=await r.json(); throw new Error(e.detail||'保存失败'); }
    showToast('自定义名称已保存','success');
    setTimeout(loadData,500);
  } catch(e) {
    showToast('保存失败: '+e.message,'error');
  }
}

async function saveDescription(id) {
  const input = document.getElementById('descInput');
  const description = input.value.trim();
  try {
    const r = await fetch(API+`/api/container/${id}/description`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({description})});
    if (!r.ok) { const e=await r.json(); throw new Error(e.detail||'保存失败'); }
    showToast('容器用途已保存','success');
    setTimeout(loadData,500);
  } catch(e) {
    showToast('保存失败: '+e.message,'error');
  }
}

function showToast(msg,type) {
  const t=document.getElementById('toast');
  t.textContent=msg; t.className='toast show '+type;
  setTimeout(()=>t.className='toast',3000);
}

// ===== UPDATE MODAL =====
let updateInfo = null;

function checkVersion() {
  fetch(API+'/api/version').then(r=>r.json()).then(data=>{
    updateInfo = data;
    const btn = document.getElementById('hdrUpdateBtn');
    if (data.has_update) {
      btn.style.display = 'inline-flex';
      btn.textContent = '⬆ v' + data.remote;
    } else {
      btn.style.display = 'none';
    }
  }).catch(()=>{});
}

function showUpdateModal() {
  if (!updateInfo || !updateInfo.has_update) return;
  const overlay = document.getElementById('updateOverlay');
  document.getElementById('updateLocalVer').textContent = updateInfo.local;
  document.getElementById('updateRemoteVer').textContent = updateInfo.remote;
  document.getElementById('updateDate').textContent = updateInfo.date || '';
  const clUl = document.getElementById('updateChangelog');
  clUl.innerHTML = '';
  if (updateInfo.changelog && updateInfo.changelog.length) {
    updateInfo.changelog.forEach(item=>{
      const li = document.createElement('li');
      li.textContent = item;
      clUl.appendChild(li);
    });
  } else {
    clUl.innerHTML = '<li>无更新说明</li>';
  }
  document.getElementById('updateProgress').classList.remove('show');
  document.getElementById('updateProgress').innerHTML = '';
  document.getElementById('btnUpdate').disabled = false;
  document.getElementById('btnUpdate').textContent = '立即更新';
  overlay.classList.add('show');
}

function closeUpdateModal() {
  document.getElementById('updateOverlay').classList.remove('show');
}

function closeModal(id) { document.getElementById(id).classList.remove('show'); }

function showDiskModal() {
  const disk = lastSysData?.disk || {};
  const sharedFolders = disk.__shared_folders__ || {};
  const systemKeys = Object.keys(disk).filter(k => k !== '__shared_folders__');
  
  const hasShared = Object.keys(sharedFolders).length > 0;
  if (!hasShared && systemKeys.length === 0) return;

  const colors = ['#58a6ff','#3fb950','#d29922','#c084fc','#38bdf8','#f87171','#fbbf24','#a78bfa','#34d399','#fb923c','#f472b6','#60a5fa'];
  const folderIcons = { 'music':'🎵', 'video':'🎬', 'photo':'📷', 'media':'📺', 'docker':'🐳', 'Download':'⬇️', 'homes':'👤', 'web':'🌐', 'ffmpeg':'🎞️', 'xiaoya':'📦', '迅雷':'⚡' };
  
  // Build SVG pie chart (solid sectors)
  const size = 220, cx = size/2, cy = size/2, r = size/2 - 10;
  let svg = `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">`;
  
  let chartItems = [];
  
  if (hasShared) {
    const totalMB = Object.values(sharedFolders).reduce((s, v) => s + v, 0);
    let startAngle = -90; // Start from top
    
    Object.entries(sharedFolders)
      .sort((a, b) => b[1] - a[1])
      .forEach(([name, sizeMB], i) => {
        const pct = sizeMB / totalMB;
        const angle = pct * 360;
        const endAngle = startAngle + angle;
        
        // Calculate path
        const x1 = cx + r * Math.cos(startAngle * Math.PI / 180);
        const y1 = cy + r * Math.sin(startAngle * Math.PI / 180);
        const x2 = cx + r * Math.cos(endAngle * Math.PI / 180);
        const y2 = cy + r * Math.sin(endAngle * Math.PI / 180);
        const largeArc = angle > 180 ? 1 : 0;
        
        const color = colors[i % colors.length];
        svg += `<path d="M${cx},${cy} L${x1},${y1} A${r},${r} 0 ${largeArc},1 ${x2},${y2} Z" fill="${color}" stroke="var(--card)" stroke-width="2" opacity="0.9"/>`;
        
        // Add label if sector is big enough (> 5%)
        if (pct > 0.05) {
          const midAngle = (startAngle + endAngle) / 2;
          const labelR = r * 0.65;
          const lx = cx + labelR * Math.cos(midAngle * Math.PI / 180);
          const ly = cy + labelR * Math.sin(midAngle * Math.PI / 180);
          const sizeStr = sizeMB >= 1024 ? (sizeMB/1024).toFixed(1)+'G' : sizeMB+'M';
          svg += `<text x="${lx}" y="${ly}" text-anchor="middle" dominant-baseline="central" fill="#fff" font-size="11" font-weight="700">${sizeStr}</text>`;
          if (pct > 0.1) {
            svg += `<text x="${lx}" y="${ly+13}" text-anchor="middle" dominant-baseline="central" fill="#fff" font-size="9" opacity="0.9">${(pct*100).toFixed(0)}%</text>`;
          }
        }
        
        startAngle = endAngle;
        chartItems.push({ name, sizeMB, color, pct });
      });
    
    // Center circle (donut hole) - show used/available
    svg += `<circle cx="${cx}" cy="${cy}" r="${r*0.35}" fill="var(--card)"/>`;
    const totalGB = (totalMB / 1024).toFixed(1);
    // Get available space from system disk
    let availGB = '-';
    if (systemKeys.length > 0 && disk[systemKeys[0]]) {
      availGB = (disk[systemKeys[0]].available_bytes / (1024**3)).toFixed(1);
    }
    svg += `<text x="${cx}" y="${cy-10}" text-anchor="middle" dominant-baseline="central" fill="var(--text-bright)" font-size="13" font-weight="700">已用 ${totalGB} GB</text>`;
    svg += `<text x="${cx}" y="${cy+6}" text-anchor="middle" dominant-baseline="central" fill="var(--green)" font-size="11">剩余 ${availGB} GB</text>`;
    svg += `<text x="${cx}" y="${cy+20}" text-anchor="middle" dominant-baseline="central" fill="var(--text-dim)" font-size="10">${chartItems.length} 个文件夹</text>`;
  } else {
    // System partitions pie
    const totalBytes = systemKeys.reduce((s, k) => s + (disk[k].total_bytes || 0), 0);
    let startAngle = -90;
    
    systemKeys.forEach((mount, i) => {
      const d = disk[mount];
      const pct = (d.total_bytes || 0) / totalBytes;
      const angle = pct * 360;
      const endAngle = startAngle + angle;
      
      const x1 = cx + r * Math.cos(startAngle * Math.PI / 180);
      const y1 = cy + r * Math.sin(startAngle * Math.PI / 180);
      const x2 = cx + r * Math.cos(endAngle * Math.PI / 180);
      const y2 = cy + r * Math.sin(endAngle * Math.PI / 180);
      const largeArc = angle > 180 ? 1 : 0;
      
      const color = colors[i % colors.length];
      svg += `<path d="M${cx},${cy} L${x1},${y1} A${r},${r} 0 ${largeArc},1 ${x2},${y2} Z" fill="${color}" stroke="var(--card)" stroke-width="2" opacity="0.9"/>`;
      
      if (pct > 0.05) {
        const midAngle = (startAngle + endAngle) / 2;
        const labelR = r * 0.65;
        const lx = cx + labelR * Math.cos(midAngle * Math.PI / 180);
        const ly = cy + labelR * Math.sin(midAngle * Math.PI / 180);
        svg += `<text x="${lx}" y="${ly}" text-anchor="middle" dominant-baseline="central" fill="#fff" font-size="10" font-weight="700">${d.use_percent}%</text>`;
      }
      
      startAngle = endAngle;
    });
    
    svg += `<circle cx="${cx}" cy="${cy}" r="${r*0.35}" fill="var(--card)"/>`;
    const totalG = (totalBytes / (1024**3)).toFixed(1);
    svg += `<text x="${cx}" y="${cy-6}" text-anchor="middle" dominant-baseline="central" fill="var(--text-bright)" font-size="14" font-weight="700">${systemKeys.length} 分区</text>`;
    svg += `<text x="${cx}" y="${cy+10}" text-anchor="middle" dominant-baseline="central" fill="var(--text-dim)" font-size="11">共 ${totalG} GB</text>`;
  }
  svg += '</svg>';

  // Legend list
  let details = '<div style="margin-top:14px;text-align:left;max-height:200px;overflow-y:auto">';
  chartItems.forEach(item => {
    const sizeGB = (item.sizeMB / 1024).toFixed(1);
    const sizeStr = item.sizeMB >= 1024 ? `${sizeGB} GB` : `${item.sizeMB} MB`;
    const icon = folderIcons[item.name] || '📁';
    const pctStr = (item.pct * 100).toFixed(1);
    details += `<div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border)">
      <span style="width:12px;height:12px;border-radius:3px;background:${item.color};flex-shrink:0"></span>
      <span style="font-size:14px">${icon}</span>
      <span style="font-weight:600;font-size:13px;flex:1">${item.name}</span>
      <span style="font-size:12px;color:var(--text-dim)">${pctStr}%</span>
      <span style="font-weight:700;font-size:13px;color:${item.color};min-width:60px;text-align:right">${sizeStr}</span>
    </div>`;
  });
  details += '</div>';

  document.getElementById('diskModalBody').innerHTML = svg + details;
  document.getElementById('diskModal').classList.add('show');
}

function showAboutModal() {
  const currentVersion = '1.5.0';
  const author = 'nbqcw123';
  const repo = 'nbqcw123/docker-panel';
  const repoUrl = `https://github.com/${repo}`;
  
  const html = `
    <div style="text-align:center;padding:20px 16px">
      <div style="font-size:40px;margin-bottom:8px">🐳</div>
      <div style="font-size:20px;font-weight:700;color:var(--text-bright)">Docker Panel</div>
      <div style="font-size:13px;color:var(--text-dim);margin-top:4px">Docker 容器管理面板</div>
      <div style="margin-top:12px;display:inline-block;padding:4px 12px;background:var(--accent-dim);color:#fff;border-radius:12px;font-size:12px;font-weight:600">v${currentVersion}</div>
    </div>
    
    <div style="border-top:1px solid var(--border);margin-top:14px;padding-top:14px">
      <div style="font-size:14px;font-weight:600;margin-bottom:8px;color:var(--text-bright)">👤 作者</div>
      <div style="font-size:13px;color:var(--text)">${author}</div>
    </div>
    
    <div style="border-top:1px solid var(--border);margin-top:14px;padding-top:14px">
      <div style="font-size:14px;font-weight:600;margin-bottom:8px;color:var(--text-bright)">📝 更新日志</div>
      <div style="font-size:12px;color:var(--text-dim);line-height:1.8">
        <div>• 磁盘圆形分割区域显示共享文件夹占用</div>
        <div>• 支持音乐/照片/视频/文件等文件夹分类</div>
        <div>• CPU/内存/网络/磁盘/容器点击弹出详情</div>
        <div>• 圆形比例图显示每核CPU占用</div>
        <div>• 物理内存 + Swap 交换空间详情</div>
        <div>• 网络流量实时速率 + 网卡详情</div>
      </div>
    </div>
    
    <div style="border-top:1px solid var(--border);margin-top:14px;padding-top:14px">
      <div style="font-size:14px;font-weight:600;margin-bottom:8px;color:var(--text-bright)">🔗 GitHub</div>
      <a href="${repoUrl}" target="_blank" style="display:block;padding:10px;background:var(--bg);border:1px solid var(--border);border-radius:8px;font-size:12px;color:var(--accent);text-decoration:none;text-align:center;word-break:break-all" onmouseover="this.style.borderColor='var(--accent)'" onmouseout="this.style.borderColor='var(--border)'">${repoUrl}</a>
    </div>
    
    <div style="border-top:1px solid var(--border);margin-top:14px;padding-top:14px;text-align:center">
      <button onclick="checkForUpdate(this)" style="padding:10px 24px;background:var(--accent-dim);color:#fff;border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;transition:all 0.2s" onmouseover="this.style.opacity='0.8'" onmouseout="this.style.opacity='1'">🔄 检查更新</button>
      <div id="updateCheckResult" style="margin-top:10px;font-size:12px;display:none"></div>
    </div>`;
  
  document.getElementById('aboutModalBody').innerHTML = html;
  document.getElementById('aboutModal').classList.add('show');
}

async function checkForUpdate(btn) {
  btn.disabled = true;
  btn.textContent = '⏳ 检查中...';
  const resultDiv = document.getElementById('updateCheckResult');
  resultDiv.style.display = 'block';
  resultDiv.textContent = '正在检查更新...';
  
  try {
    const resp = await fetch('/api/version');
    const data = await resp.json();
    
    if (data.has_update) {
      resultDiv.innerHTML = `<div style="color:var(--yellow);font-weight:600">🔔 有新版本可用!</div><div style="margin-top:6px">当前版本: <b>v${data.local}</b> → 最新版本: <b>v${data.remote}</b></div>${data.changelog && data.changelog.length > 0 ? `<div style="margin-top:8px;text-align:left"><b>更新内容:</b><ul style="margin:4px 0;padding-left:20px">${data.changelog.map(c => `<li>${c}</li>`).join('')}</ul></div>` : ''}<div style="margin-top:12px"><button onclick="doUpgrade(this)" style="padding:8px 20px;background:#3fb950;color:#fff;border:none;border-radius:6px;font-size:13px;font-weight:600;cursor:pointer"> 立即升级</button></div>`;
    } else {
      resultDiv.innerHTML = `<div style="color:var(--green);font-weight:600">✅ 已是最新版本 (v${data.local})</div><div style="margin-top:8px"><button onclick="doUpgrade(this)" style="padding:8px 20px;background:var(--accent-dim);color:#fff;border:none;border-radius:6px;font-size:13px;font-weight:600;cursor:pointer"> 重新部署</button></div>`;
    }
  } catch (e) {
    resultDiv.innerHTML = `<div style="color:var(--red)">❌ 检查失败: ${e.message}</div>`;
  }
  
  btn.disabled = false;
  btn.textContent = '🔄 检查更新';
}

async function doUpgrade(btn) {
  const originalText = btn ? btn.textContent : ' 立即升级';
  if (btn) {
    btn.disabled = true;
    btn.textContent = ' 升级中...';
  }
  try {
    const resp = await fetch('/api/upgrade', {method: 'POST'});
    const data = await resp.json();
    if (data.success) {
      showToast(' 升级成功，正在重启服务...', 'success');
      setTimeout(() => location.reload(), 5000);
    } else {
      showToast(' 升级失败: ' + (data.message || '未知错误'), 'error');
      if (btn) {
        btn.disabled = false;
        btn.textContent = originalText;
      }
    }
  } catch (e) {
    showToast(' 升级请求失败: ' + e.message, 'error');
    if (btn) {
      btn.disabled = false;
      btn.textContent = originalText;
    }
  }
}
function showCpuModal() {
  const cpu = lastSysData?.cpu || {};
  const pct = cpu.use_percent || 0;
  const c = pct > 85 ? 'var(--red)' : pct > 65 ? 'var(--yellow)' : 'var(--green)';
  const cores = cpu.cores || 1;
  const l1 = parseFloat(cpu.load_1m || 0);
  const l5 = parseFloat(cpu.load_5m || 0);
  const l15 = parseFloat(cpu.load_15m || 0);
  const loadPct1 = Math.min(100, Math.round((l1 / cores) * 100));
  const loadPct5 = Math.min(100, Math.round((l5 / cores) * 100));
  const loadPct15 = Math.min(100, Math.round((l15 / cores) * 100));
  const loadColor = (p) => p > 85 ? 'var(--red)' : p > 65 ? 'var(--yellow)' : 'var(--green)';
  
  // Per-core bars
  const perCore = cpu.per_core || [];
  let coreBars = '';
  perCore.forEach((cp, i) => {
    const cc = cp > 85 ? 'var(--red)' : cp > 65 ? 'var(--yellow)' : 'var(--green)';
    coreBars += `<div style="display:flex;align-items:center;gap:6px;margin-bottom:3px">
      <span style="font-size:10px;color:var(--text-dim);width:32px;text-align:right">CPU${i}</span>
      <div style="flex:1;height:8px;background:var(--border);border-radius:4px;overflow:hidden">
        <div style="height:100%;width:${cp}%;background:${cc};border-radius:4px;transition:width 0.3s"></div>
      </div>
      <span style="font-size:10px;font-weight:600;color:${cc};width:36px">${cp}%</span>
    </div>`;
  });
  
  const html = `
    <div style="text-align:center;padding:10px 0">
      <div style="font-size:42px;font-weight:800;color:${c}">${pct}%</div>
      <div style="color:var(--text-dim);margin-top:2px;font-size:13px">CPU 占用率 · ${cores} 核心</div>
      ${cpu.model ? `<div style="color:var(--text-dim);font-size:11px;margin-top:2px">${cpu.model}</div>` : ''}
      ${cpu.freq ? `<div style="color:var(--text-dim);font-size:11px">${cpu.freq}</div>` : ''}
      <div style="margin-top:16px;height:12px;background:var(--border);border-radius:6px;overflow:hidden">
        <div style="height:100%;width:${pct}%;background:${c};border-radius:6px;transition:width 0.5s"></div>
      </div>
    </div>
    <div style="border-top:1px solid var(--border);margin-top:14px;padding-top:14px">
      <div style="font-size:13px;font-weight:600;margin-bottom:10px;color:var(--text-bright)">📊 系统负载 (Load Average)</div>
      <div style="display:flex;gap:10px">
        <div style="flex:1;text-align:center;padding:10px;background:var(--bg);border-radius:8px;border:1px solid var(--border)">
          <div style="font-size:20px;font-weight:700;color:${loadColor(loadPct1)}">${cpu.load_1m || '-'}</div>
          <div style="font-size:11px;color:var(--text-dim);margin-top:2px">1 分钟</div>
          <div style="margin-top:6px;height:4px;background:var(--border);border-radius:2px;overflow:hidden">
            <div style="height:100%;width:${loadPct1}%;background:${loadColor(loadPct1)};border-radius:2px"></div>
          </div>
        </div>
        <div style="flex:1;text-align:center;padding:10px;background:var(--bg);border-radius:8px;border:1px solid var(--border)">
          <div style="font-size:20px;font-weight:700;color:${loadColor(loadPct5)}">${cpu.load_5m || '-'}</div>
          <div style="font-size:11px;color:var(--text-dim);margin-top:2px">5 分钟</div>
          <div style="margin-top:6px;height:4px;background:var(--border);border-radius:2px;overflow:hidden">
            <div style="height:100%;width:${loadPct5}%;background:${loadColor(loadPct5)};border-radius:2px"></div>
          </div>
        </div>
        <div style="flex:1;text-align:center;padding:10px;background:var(--bg);border-radius:8px;border:1px solid var(--border)">
          <div style="font-size:20px;font-weight:700;color:${loadColor(loadPct15)}">${cpu.load_15m || '-'}</div>
          <div style="font-size:11px;color:var(--text-dim);margin-top:2px">15 分钟</div>
          <div style="margin-top:6px;height:4px;background:var(--border);border-radius:2px;overflow:hidden">
            <div style="height:100%;width:${loadPct15}%;background:${loadColor(loadPct15)};border-radius:2px"></div>
          </div>
        </div>
      </div>
      <div style="font-size:11px;color:var(--text-dim);margin-top:8px;text-align:center">负载值 / 核心数 = 使用率，超过 100% 表示过载</div>
    </div>
    ${perCore.length > 0 ? `
    <div style="border-top:1px solid var(--border);margin-top:14px;padding-top:14px">
      <div style="font-size:13px;font-weight:600;margin-bottom:10px;color:var(--text-bright)">🔲 每核占用 (${perCore.length} 核)</div>
      <div style="max-height:200px;overflow-y:auto">
        ${coreBars}
      </div>
    </div>` : ''}`;
  document.getElementById('cpuModalBody').innerHTML = html;
  document.getElementById('cpuModal').classList.add('show');
}

function showMemModal() {
  const mem = lastSysData?.memory || {};
  const pct = mem.use_percent || 0;
  const c = pct > 85 ? 'var(--red)' : pct > 65 ? 'var(--yellow)' : 'var(--green)';
  const totalGB = mem.total_gb || (mem.total_mb / 1024).toFixed(2);
  const usedGB = mem.used_gb || (mem.used_mb / 1024).toFixed(2);
  const availGB = mem.available_gb || (mem.available_mb / 1024).toFixed(2);
  const bufMB = mem.buffers_mb || 0;
  const cacheMB = mem.cached_mb || 0;
  const activeMB = mem.active_mb || 0;
  const inactiveMB = mem.inactive_mb || 0;
  const slabMB = mem.slab_mb || 0;
  // Swap
  const swapTotalGB = mem.swap_total_gb || 0;
  const swapUsedGB = mem.swap_used_gb || 0;
  const swapPct = mem.swap_percent || 0;
  const swapC = swapPct > 85 ? 'var(--red)' : swapPct > 65 ? 'var(--yellow)' : 'var(--green)';
  const hasSwap = (mem.swap_total_mb || 0) > 0;
  
  const html = `
    <div style="text-align:center;padding:10px 0">
      <div style="font-size:42px;font-weight:800;color:${c}">${pct}%</div>
      <div style="color:var(--text-dim);margin-top:2px;font-size:13px">物理内存 · 共 ${totalGB} GB</div>
      <div style="margin-top:16px;height:12px;background:var(--border);border-radius:6px;overflow:hidden">
        <div style="height:100%;width:${pct}%;background:${c};border-radius:6px;transition:width 0.5s"></div>
      </div>
      <div style="font-size:12px;color:var(--text-dim);margin-top:6px">${usedGB} / ${totalGB} GB</div>
    </div>
    <div style="border-top:1px solid var(--border);margin-top:14px;padding-top:14px">
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px">
        <div style="padding:10px;background:var(--bg);border-radius:8px;border:1px solid var(--border)">
          <div style="font-size:10px;color:var(--text-dim)">可用</div>
          <div style="font-size:16px;font-weight:700;color:var(--green)">${availGB} <span style="font-size:11px">GB</span></div>
          <div style="font-size:10px;color:var(--text-dim)">${(mem.available_mb||0).toLocaleString()} MB</div>
        </div>
        <div style="padding:10px;background:var(--bg);border-radius:8px;border:1px solid var(--border)">
          <div style="font-size:10px;color:var(--text-dim)">已用</div>
          <div style="font-size:16px;font-weight:700;color:var(--text-bright)">${usedGB} <span style="font-size:11px">GB</span></div>
          <div style="font-size:10px;color:var(--text-dim)">${(mem.used_mb||0).toLocaleString()} MB</div>
        </div>
        <div style="padding:10px;background:var(--bg);border-radius:8px;border:1px solid var(--border)">
          <div style="font-size:10px;color:var(--text-dim)">总计</div>
          <div style="font-size:16px;font-weight:700;color:var(--text)">${totalGB} <span style="font-size:11px">GB</span></div>
          <div style="font-size:10px;color:var(--text-dim)">${(mem.total_mb||0).toLocaleString()} MB</div>
        </div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-top:8px">
        <div style="padding:8px;background:var(--bg);border-radius:8px;border:1px solid var(--border)">
          <div style="font-size:10px;color:var(--text-dim)">Buffers</div>
          <div style="font-size:14px;font-weight:600;color:var(--text)">${bufMB.toLocaleString()} <span style="font-size:10px">MB</span></div>
        </div>
        <div style="padding:8px;background:var(--bg);border-radius:8px;border:1px solid var(--border)">
          <div style="font-size:10px;color:var(--text-dim)">Cached</div>
          <div style="font-size:14px;font-weight:600;color:var(--text)">${cacheMB.toLocaleString()} <span style="font-size:10px">MB</span></div>
        </div>
        <div style="padding:8px;background:var(--bg);border-radius:8px;border:1px solid var(--border)">
          <div style="font-size:10px;color:var(--text-dim)">Slab</div>
          <div style="font-size:14px;font-weight:600;color:var(--text)">${slabMB.toLocaleString()} <span style="font-size:10px">MB</span></div>
        </div>
      </div>
    </div>
    ${hasSwap ? `
    <div style="border-top:1px solid var(--border);margin-top:14px;padding-top:14px">
      <div style="font-size:13px;font-weight:600;margin-bottom:10px;color:var(--text-bright)">💾 Swap 交换空间</div>
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
        <div style="flex:1">
          <div style="height:10px;background:var(--border);border-radius:5px;overflow:hidden">
            <div style="height:100%;width:${swapPct}%;background:${swapC};border-radius:5px"></div>
          </div>
        </div>
        <span style="font-size:13px;font-weight:700;color:${swapC};white-space:nowrap">${swapPct}%</span>
      </div>
      <div style="display:flex;justify-content:space-between;font-size:11px;color:var(--text-dim)">
        <span>已用 ${swapUsedGB} GB / 共 ${swapTotalGB} GB</span>
        <span>可用 ${(mem.swap_free_mb/1024).toFixed(2) || 0} GB</span>
      </div>
    </div>` : ''}`;
  document.getElementById('memModalBody').innerHTML = html;
  document.getElementById('memModal').classList.add('show');
}

function showNetModal() {
  const net = lastSysData?.network || {};
  const ifaces = net.interfaces || {};
  let ifaceRows = '';
  for (const [name, iface] of Object.entries(ifaces)) {
    if (name === 'lo') continue;
    ifaceRows += `<div style="display:flex;align-items:center;justify-content:space-between;padding:8px 10px;background:var(--bg);border-radius:6px;margin-bottom:4px">
      <div style="display:flex;align-items:center;gap:8px">
        <span style="font-size:16px">🔌</span>
        <span style="font-weight:600;font-size:13px">${name}</span>
      </div>
      <div style="text-align:right;font-size:12px">
        <div style="color:var(--accent)">↓ ${formatBytes(iface.rx_bytes||0)}</div>
        <div style="color:var(--green)">↑ ${formatBytes(iface.tx_bytes||0)}</div>
      </div>
    </div>`;
  }
  const html = `
    <div style="padding:10px 0">
      <div style="text-align:center;margin-bottom:14px">
        <div style="display:flex;justify-content:center;gap:24px">
          <div>
            <div style="font-size:12px;color:var(--text-dim)">⬇️ 下载</div>
            <div style="font-size:24px;font-weight:700;color:var(--accent)">${formatSpeed(net.speed_rx||0)}</div>
          </div>
          <div style="width:1px;background:var(--border)"></div>
          <div>
            <div style="font-size:12px;color:var(--text-dim)">⬆️ 上传</div>
            <div style="font-size:24px;font-weight:700;color:var(--green)">${formatSpeed(net.speed_tx||0)}</div>
          </div>
        </div>
      </div>
      <div style="background:var(--bg);border-radius:8px;padding:10px;border:1px solid var(--border)">
        <div style="display:flex;justify-content:space-between;font-size:12px;color:var(--text-dim);margin-bottom:4px">
          <span>总接收</span><b style="color:var(--text)">${formatBytes(net.total_rx_bytes||0)}</b>
        </div>
        <div style="display:flex;justify-content:space-between;font-size:12px;color:var(--text-dim)">
          <span>总发送</span><b style="color:var(--text)">${formatBytes(net.total_tx_bytes||0)}</b>
        </div>
      </div>
      <div style="margin-top:14px">
        <div style="font-size:13px;font-weight:600;margin-bottom:8px;color:var(--text-bright)">🔌 网卡详情 (${Object.keys(ifaces).filter(n=>n!=='lo').length} 个)</div>
        ${ifaceRows || '<div style="color:var(--text-dim);font-size:12px;text-align:center;padding:10px">无数据</div>'}
      </div>
    </div>`;
  document.getElementById('netModalBody').innerHTML = html;
  document.getElementById('netModal').classList.add('show');
}

function showContainersModal() {
  const containers = allContainers || [];
  let listHtml = '';
  containers.forEach(c => {
    const stateIcon = c.state === 'running' ? '🟢' : c.state === 'paused' ? '🟡' : '🔴';
    listHtml += `<div style="display:flex;align-items:center;gap:8px;padding:8px 0;border-bottom:1px solid var(--border);cursor:pointer" onclick="closeModal('containersModal');showDetail(allContainers.find(x=>x.id==='${c.id}'))">
      <span>${stateIcon}</span>
      <span style="font-weight:600;font-size:13px">${c.name}</span>
      <span style="color:var(--text-dim);font-size:11px;margin-left:auto">${c.image}</span>
    </div>`;
  });
  const html = `
    <div>
      <div style="font-size:13px;color:var(--text-dim);margin-bottom:8px">共 ${containers.length} 个容器</div>
      ${listHtml || '<div style="color:var(--text-dim);padding:20px 0;text-align:center">无容器</div>'}
    </div>`;
  document.getElementById('containersModalBody').innerHTML = html;
  document.getElementById('containersModal').classList.add('show');
}

function doUpdate() {
  const btn = document.getElementById('btnUpdate');
  const progress = document.getElementById('updateProgress');
  btn.disabled = true;
  btn.textContent = '检测中...';
  progress.classList.add('show');
  progress.innerHTML = '<div style="color:var(--text-dim)">⏳ 正在检测更新源...</div>';

  // Step 1: Check all sources
  fetch(API + '/api/sources')
    .then(r => r.json())
    .then(data => {
      const sources = data.sources || [];
      const okSources = sources.filter(s => s.status === 'ok');
      
      if (okSources.length === 0) {
        // All sources failed - show error with details
        showSourceError(sources, btn, progress);
        return;
      }
      
      // Pick the fastest source
      const best = okSources[0];
      btn.textContent = '更新中...';
      
      // Show source info
      progress.innerHTML = `
        <div style="margin-bottom:8px;font-size:12px;color:var(--text-dim)">
          📡 使用源: <b style="color:var(--accent)">${best.name}</b> (${best.latency_ms}ms)
        </div>
        <div style="margin-bottom:8px"><span id="updateStep1">⏳</span> 正在下载最新版本...</div>
        <div class="progress-bar"><div class="progress-fill" id="downloadProgressFill" style="width:0%"></div></div>
        <div style="font-size:11px;color:var(--text-dim);margin-top:4px" id="downloadProgressText">0%</div>
        <div style="margin-top:12px"><span id="updateStep2" style="opacity:0.3">⬜</span> 正在更新文件...</div>
        <div class="progress-bar"><div class="progress-fill" id="updateProgressFill" style="width:0%"></div></div>
        <div style="font-size:11px;color:var(--text-dim);margin-top:4px" id="updateProgressText">等待中</div>
      `;

      // Step 2: Download from best source
      const xhr = new XMLHttpRequest();
      xhr.open('POST', API + '/api/update', true);
      xhr.setRequestHeader('Content-Type', 'application/json');
      xhr.responseType = 'json';

      let downloadPct = 0;
      const downloadInterval = setInterval(() => {
        downloadPct += Math.random() * 15;
        if (downloadPct > 90) downloadPct = 90;
        const fill = document.getElementById('downloadProgressFill');
        const text = document.getElementById('downloadProgressText');
        if (fill) fill.style.width = downloadPct + '%';
        if (text) text.textContent = Math.round(downloadPct) + '%';
      }, 200);

      xhr.onload = function() {
        clearInterval(downloadInterval);
        const fill = document.getElementById('downloadProgressFill');
        const text = document.getElementById('downloadProgressText');
        if (fill) fill.style.width = '100%';
        if (text) text.textContent = '100%';
        const step1 = document.getElementById('updateStep1');
        if (step1) step1.textContent = '✅';

        if (xhr.status === 200 && xhr.response && xhr.response.success) {
          // Step 3: Update progress
          const step2 = document.getElementById('updateStep2');
          if (step2) { step2.textContent = '⏳'; step2.style.opacity = '1'; }
          let updatePct = 0;
          const updateInterval = setInterval(() => {
            updatePct += Math.random() * 20;
            if (updatePct > 95) updatePct = 95;
            const uf = document.getElementById('updateProgressFill');
            const ut = document.getElementById('updateProgressText');
            if (uf) uf.style.width = updatePct + '%';
            if (ut) ut.textContent = Math.round(updatePct) + '%';
          }, 150);

          setTimeout(() => {
            clearInterval(updateInterval);
            const uf = document.getElementById('updateProgressFill');
            const ut = document.getElementById('updateProgressText');
            const s2 = document.getElementById('updateStep2');
            if (uf) uf.style.width = '100%';
            if (ut) ut.textContent = '100%';
            if (s2) s2.textContent = '✅';
            progress.innerHTML = '<div style="color:var(--green)">✅ ' + (xhr.response.message || '更新成功') + '</div>';
            btn.textContent = '重启面板';
            btn.disabled = false;
            btn.onclick = doRestart;
          }, 1500);
        } else {
          const errMsg = (xhr.response && xhr.response.detail) || '更新失败';
          progress.innerHTML = '<div style="color:var(--red)">❌ ' + errMsg + '</div>';
          btn.textContent = '重试';
          btn.disabled = false;
          btn.onclick = doUpdate;
        }
      };

      xhr.onerror = function() {
        clearInterval(downloadInterval);
        progress.innerHTML = '<div style="color:var(--red)">❌ 网络错误，请检查连接</div>';
        btn.textContent = '重试';
        btn.disabled = false;
        btn.onclick = doUpdate;
      };

      xhr.send(JSON.stringify({target_version: updateInfo.remote, source_url: best.url}));
    })
    .catch(e => {
      progress.innerHTML = '<div style="color:var(--red)">❌ 检测失败: ' + e.message + '</div>';
      btn.textContent = '重试';
      btn.disabled = false;
      btn.onclick = doUpdate;
    });
}

function showSourceError(sources, btn, progress) {
  let html = '<div style="margin-bottom:12px;font-weight:600">⚠️ 无法连接到任何更新源</div>';
  html += '<div style="font-size:12px;color:var(--text-dim);margin-bottom:12px">';
  for (const s of sources) {
    const icon = s.status === 'ok' ? '✅' : '❌';
    const detail = s.status === 'ok' ? s.latency_ms + 'ms' : '超时/错误';
    html += `<div style="margin-bottom:4px">${icon} <b>${s.name}</b>: ${detail}</div>`;
  }
  html += '</div>';
  html += '<div style="display:flex;gap:8px;margin-top:12px">';
  html += '<button id="retryBtn" style="flex:1;padding:8px 16px;background:var(--accent-dim);color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:13px;font-weight:600">重试</button>';
  html += '<button id="cancelBtn" style="flex:1;padding:8px 16px;background:transparent;border:1px solid var(--border);border-radius:6px;cursor:pointer;font-size:13px;font-weight:600;color:var(--text)">取消</button>';
  html += '</div>';
  
  progress.innerHTML = html;
  btn.textContent = '更新';
  btn.disabled = false;
  
  document.getElementById('retryBtn').onclick = function() { doUpdate(); };
  document.getElementById('cancelBtn').onclick = function() { closeUpdateModal(); };
}

function doRestart() {
  const progress = document.getElementById('updateProgress');
  progress.innerHTML = '<div style="color:var(--yellow)">🔄 正在重启面板服务...</div>';
  fetch(API+'/api/restart', {method:'POST'}).then(()=>{
    setTimeout(()=>{
      progress.innerHTML = '<div style="color:var(--green)">✅ 面板正在重启，3秒后自动刷新...</div>';
      setTimeout(()=>location.reload(), 3000);
    }, 2000);
  }).catch(e=>{
    progress.innerHTML = '<div style="color:var(--red)">❌ 重启失败: ' + e.message + '</div>';
  });
}

// 初始检测版本
setTimeout(checkVersion, 1000);
// 每5分钟检测一次
setInterval(checkVersion, 300000);

loadData();
// 首次自动加载后，后续需手动点击加载按钮刷新
// setInterval 已移除，用户需点击"加载"按钮手动刷新
</script>

<!-- 更新弹窗 -->
<div class="update-overlay" id="updateOverlay" onclick="if(event.target===this)closeUpdateModal()">
  <div class="update-panel">
    <div class="update-header">
      <h2>🔄 版本更新</h2>
      <span class="close-btn" onclick="closeUpdateModal()">✕</span>
    </div>
    <div class="update-meta">
      <span>当前版本: <b id="updateLocalVer">-</b></span>
      <span>最新版本: <b id="updateRemoteVer" style="color:var(--green)">-</b></span>
      <span id="updateDate"></span>
    </div>
    <div class="update-changelog">
      <div class="cl-title">更新说明</div>
      <ul id="updateChangelog"></ul>
    </div>
    <div class="update-actions">
      <button class="btn-update" id="btnUpdate" onclick="doUpdate()">立即更新</button>
      <button onclick="closeUpdateModal()">稍后再说</button>
    </div>
    <div class="update-progress" id="updateProgress"></div>
  </div>
</div>

</body>
</html>
"""
