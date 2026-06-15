#!/usr/bin/env python3
"""Docker Management Panel - Backend (FastAPI)"""
import json, subprocess, asyncio, re, os, socket, ssl
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

VERSION = "1.2.0"

app = FastAPI(title="Docker Panel")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DOCKER_SOCKET = "/var/run/docker.sock"

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
        result = subprocess.run(["df", "-h", "--output=target,pcent"], capture_output=True, text=True, timeout=5)
        for line in result.stdout.strip().split("\n")[1:]:
            parts = line.split()
            if len(parts) >= 2:
                mount = parts[0]
                try: int(parts[-1].replace("%", ""))
                except ValueError: continue
                if mount in ("/", "/boot", "/boot/efi"): targets.append(mount)
                elif re.match(r"^/volume\d+$", mount): targets.append(mount)
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

def get_disk_usage():
    try:
        tgts = _detect_disk_targets()
        r = subprocess.run(["df","-h"]+tgts, capture_output=True, text=True, timeout=5)
        ds = {}
        for ln in r.stdout.strip().split("\n")[1:]:
            p = ln.split()
            if len(p) >= 6: ds[p[5]] = {"size":p[1],"used":p[2],"available":p[3],"use_percent":p[4].replace("%","")}
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
        return {"total_mb":round(t/1024),"used_mb":round((t-a)/1024),"available_mb":round(a/1024),"use_percent":round((t-a)/t*100,1) if t>0 else 0}
    except: return {}

@app.get("/api/containers")
async def list_containers():
    loop = asyncio.get_event_loop()
    raw = await loop.run_in_executor(None, lambda: docker_api("GET", "/containers/json?all=true"))
    if isinstance(raw, dict) and "error" in raw: raise HTTPException(500, raw["error"])
    cs = []
    for c in raw:
        ps = [{"container_port":p.get("PrivatePort"),"host_port":p.get("PublicPort"),"host_ip":p.get("IP","0.0.0.0"),"type":p.get("Type","tcp")} for p in c.get("Ports",[])]
        cs.append({"id":c.get("Id","")[:12],"name":c.get("Names",[""])[0].lstrip("/"),"image":c.get("Image",""),"status":c.get("Status",""),"state":c.get("State",""),"created":c.get("Created",0),"ports":ps})
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
    return {"id":cid[:12],"cpu_percent":cpu,"memory_usage":mu,"memory_limit":ml,"memory_usage_mb":round(mu/1024/1024,1),"memory_limit_mb":round(ml/1024/1024,1),"memory_percent":mp,"network_rx":sum(n.get("rx_bytes",0) for n in ns.values()),"network_tx":sum(n.get("tx_bytes",0) for n in ns.values())}

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
    return {"memory":get_system_memory(),"disk":get_disk_usage(),"ports":up,"ports_count":len(up)}

class ActionRequest(BaseModel):
    action: str

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

/* Status pills in header */
.hdr-stats {display:flex;gap:10px;flex:1;min-width:0;}
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
</style>
</head>
<body>

<div class="header">
  <div class="header-left">
    <h1><span class="icon">🐳</span> Docker Panel</h1>
    <span class="hdr-ver">v1.2.0</span>
  </div>
  <div class="hdr-stats" id="hdrStats"></div>
  <div class="header-right">
    <div class="theme-switcher">
      <button class="theme-btn active" data-theme="dark" title="暗色">🌙</button>
      <button class="theme-btn" data-theme="light" title="亮色">☀️</button>
      <button class="theme-btn" data-theme="ocean" title="海洋蓝">🌊</button>
      <button class="theme-btn" data-theme="purple" title="紫色">🔮</button>
    </div>
    <button class="refresh-btn" id="refreshBtn" onclick="loadData()"><span id="refreshIcon">⟳</span> 刷新</button>
  </div>
</div>

<div id="errorBanner"></div>

<div class="toolbar">
  <div class="category-bar">
    <div class="cat-tab active" data-cat="all" onclick="setCategory('all')">📦 全部 <span class="count" id="count-all">0</span></div>
    <div class="cat-tab" data-cat="running" onclick="setCategory('running')">🟢 使用中 <span class="count" id="count-running">0</span></div>
    <div class="cat-tab" data-cat="stopped" onclick="setCategory('stopped')">🔴 未使用 <span class="count" id="count-stopped">0</span></div>
  </div>
</div>

<div class="main-layout">
  <div class="ports-sidebar">
    <div class="ports-bar">
      <div class="ports-bar-header">
        <div class="ports-bar-title">🔌 已占用端口</div>
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

<div class="toast" id="toast"></div>

<script>
const API = '';
let allContainers = [];
let currentCategory = 'all';
let searchQuery = '';

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
  const mem = sys.memory || {}, disk = sys.disk || {};
  const running = allContainers.filter(c=>c.state==='running').length, total = allContainers.length;
  let html = '';

  if (mem.total_mb) {
    const pct = mem.use_percent||0, c = pct>85?'var(--red)':pct>65?'var(--yellow)':'var(--green)';
    html += `<div class="hdr-pill"><span class="icon">🧠</span><span class="val">${mem.use_percent}%</span><span class="sub">内存</span><div class="bar"><div class="bar-fill" style="width:${pct}%;background:${c}"></div></div></div>`;
  }

  for (const [mnt, info] of Object.entries(disk)) {
    const pct = parseInt(info.use_percent)||0;
    const c = pct>85?'var(--red)':pct>65?'var(--yellow)':'var(--accent)';
    const lbl = mnt==='/'?'系统':mnt.replace('/volume','存储');
    html += `<div class="hdr-pill"><span class="icon">💾</span><span class="val">${pct}%</span><span class="sub">${lbl}</span><div class="bar"><div class="bar-fill" style="width:${pct}%;background:${c}"></div></div></div>`;
  }

  html += `<div class="hdr-pill green"><span class="icon">🐳</span><span class="val">${running}/${total}</span><span class="sub">容器</span></div>`;

  document.getElementById('hdrStats').innerHTML = html;
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
  // sort alphabetically by name
  cs.sort((a,b) => (a.name||'').localeCompare(b.name||'', 'zh-CN'));
  return cs;
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
  return `<div class="container-row" id="card-${c.id}" onclick="showDetail(allContainers.find(x=>x.id==='${c.id}'))">
    <span class="row-status ${state}"></span>
    <span class="row-name" title="${esc(c.name)}">${esc(c.name)}</span>
    <span class="row-image" title="${esc(c.image||'')}">${esc(c.image||'-')}</span>
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

  document.getElementById('detailPanel').innerHTML = `
    <span class="close-btn" onclick="closeDetail()">✕</span>
    <div class="detail-header">
      <span class="status-dot ${state}"></span>
      <h2>${esc(c.name)}</h2>
    </div>
    <div class="detail-section">
      <div class="detail-label">基本信息</div>
      <div class="detail-value"><b>ID:</b> ${esc(c.id)} &nbsp; <b>状态:</b> ${esc(c.status||state)}</div>
      <div class="detail-value mono" style="margin-top:6px">${esc(c.image||'-')}</div>
    </div>
    <div class="detail-section">
      <div class="detail-label">资源使用</div>
      <div class="detail-stats">
        <div class="detail-stat"><div class="stat-val">${cpu}</div><div class="stat-label">CPU</div></div>
        <div class="detail-stat"><div class="stat-val">${memUse}</div><div class="stat-label">内存 / ${memLimit}</div></div>
        <div class="detail-stat"><div class="stat-val">${memPct}</div><div class="stat-label">内存占用</div></div>
      </div>
      <div class="detail-value" style="margin-top:8px;font-size:12px;color:var(--text-dim)">网络: ↓${netRx} ↑${netTx}</div>
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

function showToast(msg,type) {
  const t=document.getElementById('toast');
  t.textContent=msg; t.className='toast show '+type;
  setTimeout(()=>t.className='toast',3000);
}

loadData();
setInterval(loadData,30000);
</script>
</body>
</html>
"""
