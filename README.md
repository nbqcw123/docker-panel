# 🐳 Docker 管理面板 v1.2.0

轻量级 Docker 容器管理 Web 面板，支持 **群晖 Synology**、**飞牛 fnOS** 等 NAS 系统。

## 页面预览

Based on actual HTML layout v1.2.0:

```
┌────────────────────────────────────────────────────────────────────────────┐
│ 🐳 Docker Panel v1.2.0  │ [🧠 71%] [💾 36%] [🐳 2/40] │ [🌙☀️🌊🔮] [⟳] │
│ header-left             │ hdr-stats (right-aligned)      │ theme + refresh│
├─────────────────────────┴──────────────────────────────────────────────────┤
│ 📦 全部(40) │ 🟢 使用中(2) │ 🔴 未使用(38)          ← category tabs      │
├─────────────┬──────────────────────────────────────────────────────────────┤
│ 🔌 已占用端口 │ 🔍 搜索容器名称 / 镜像...                                    │
│ (8)         │                                                              │
│ 50088 → dp  │ ● 使用中 (1)                                                 │
│ 50086 → iptv│ ┌────────────────────────────────────────────────────────────┐│
│ 58000 → her │ │ 🟢 hermes-agent                                           ││
│ 443 → nginx │ │ nousresearch/hermes-agent:latest                           ││
│ 80 → nginx  │ │ Up 6 days | CPU: 12% | MEM: 1.2GB/4GB | NET: 2.3GB/890MB││
│ 3306 → maria│ │ [58000] [8000] ...                     [🔄重启] [⏹停止]    ││
│ 6443 → k3s  │ └────────────────────────────────────────────────────────────┘│
│ 8123 → hass │ ● 未使用 (1)                                                 │
│             │ ┌────────────────────────────────────────────────────────────┐│
│             │ │ 🔴 chromium                                               ││
│             │ │ trim-chromium:latest                                       ││
│             │ │ Exited (137) 7 days ago                [▶️启动]             ││
│             │ └────────────────────────────────────────────────────────────┘│
└─────────────┴──────────────────────────────────────────────────────────────┘
```

> 分隔符: `│ ←` `│`  
> 数据行: 状态点 + 名称 + 镜像 + 端口标签(蓝色) + 统计 + 操作按钮(绿/红/黄)

访问地址：
- **群晖**: `http://100.113.206.33:50088`
- **飞牛**: `http://fnnas:50088`

## 功能

- 📋 **容器列表**：一行一个容器，清晰展示状态、镜像、端口、CPU/内存
- 🔌 **端口占用**：左侧边栏独立展示所有已占用端口
- 🖥️ **系统监控**：顶部通栏实时显示系统内存、磁盘使用率、容器状态
- 🎨 **4 套主题**：暗色 / 亮色 / 海洋蓝 / 紫色之夜
- 📱 **响应式布局**：适配桌面和移动端
- ⚡ **实时刷新**：每 30 秒自动更新
- 🔧 **容器操作**：启动 / 停止 / 一键重启

## 布局

```
┌─────────────────────────────────────────────┐
│  🐳 Docker 管理面板          [主题] [刷新]   │  ← Header
├──────────┬──────────────────────────────────┤
│ 🧠 内存   │ 使用中 (N 个容器)                  │  ← Top Status
│ 💾 磁盘   ├──────────────────────────────────┤  (内存/磁盘
│ 🐳 容器   │ 容器名 镜像 端口 CPU 操作           │   容器状态)
├──────────┤ 容器名 镜像 端口 CPU 操作           │
│ 🔌 已占用 │ ...                               │
│ 端口      │ 未使用 (N 个容器)                  │
│ 444→443  │ 容器名 镜像 端口 CPU 操作           │
│ 1880→1880│                                   │
│ ...       │                                   │
└──────────┴──────────────────────────────────┘
 ↑ 左侧边栏    ↑ 右侧容器列表
```

## 系统适配

| 特性 | 群晖 Synology | 飞牛 fnOS | 标准 Linux |
|------|-------------|----------|-----------|
| Docker 路径 | `/volume1/@appstore/...` | `/usr/bin/docker` | `docker` |
| 磁盘检测 | `/volume1`, `/` | `/`, `/mnt/*`, `/data*` | `/` |
| 运行方式 | 直接 Python | Docker 容器 / 直接 Python | Docker 容器 |

Docker 二进制路径和磁盘挂载点均为**自动检测**，无需手动配置。

## 部署方式

### Docker 容器（推荐）

```bash
# 构建镜像
docker build -t docker-panel .

# 运行
docker run -d \
  --name docker-panel \
  -p 50088:50088 \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  --restart unless-stopped \
  docker-panel
```

自定义端口：
```bash
docker run -d \
  --name docker-panel \
  -p 8080:50088 \
  -e DOCKER_PANEL_PORT=50088 \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  --restart unless-stopped \
  docker-panel
```

### 直接运行 Python

```bash
pip3 install fastapi uvicorn pydantic
python3 -m uvicorn main:app --host 0.0.0.0 --port 50088
# 或
DOCKER_PANEL_PORT=8080 python3 run.py
```

### 群晖 Docker socket 权限

群晖 NAS 上非 root 用户需要 Docker socket 访问权限（重启后需重新执行）：

```bash
sudo chown root:administrators /var/run/docker.sock
sudo chmod 660 /var/run/docker.sock
```

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | Web 面板页面 |
| GET | `/api/containers` | 获取所有容器列表 |
| GET | `/api/containers/all-stats` | 容器列表 + 实时统计 |
| GET | `/api/container/{id}/stats` | 单个容器统计 |
| GET | `/api/system` | 系统信息（内存、磁盘、端口） |
| POST | `/api/container/{id}/action` | 容器操作（start/stop/restart） |

## 文件结构

| 文件 | 说明 |
|------|------|
| `main.py` | 后端 FastAPI + 前端 HTML（单文件） |
| `run.py` | 启动入口 |
| `Dockerfile` | Docker 镜像构建 |
| `README.md` | 本文档 |

## License

MIT
