# 🐳 Docker 管理面板

一个轻量级的 Docker 容器管理 Web 面板，支持 **群晖 (Synology)** 和 **飞牛 (fnOS)** 等 NAS 系统。

## 功能特性

- 📋 **容器列表**：一行一个容器，清晰展示状态、镜像、端口、CPU/内存
- 🔌 **端口占用**：左侧边栏独立展示所有已占用端口
- 🖥️ **系统监控**：实时显示系统内存、磁盘使用率、容器状态
- 🎨 **4 套主题**：暗色 / 亮色 / 海洋蓝 / 紫色之夜
- 📱 **响应式布局**：适配桌面和移动端
- ⚡ **实时刷新**：每 30 秒自动更新

## 系统适配

| 特性 | 群晖 Synology | 飞牛 fnOS | 标准 Linux |
|------|-------------|----------|-----------|
| Docker 路径 | `/volume1/@appstore/...` | `/usr/bin/docker` | `docker` |
| 磁盘检测 | `/volume1`, `/` | `/`, `/mnt/*`, `/data*` | `/` |
| 运行方式 | 直接 Python | 直接 Python / Docker | Docker |

> Docker 二进制路径和磁盘挂载点均为**自动检测**，无需手动配置。

## 部署方式

### 方式一：直接运行 Python（推荐）

**依赖**：Python 3.7+

```bash
# 安装依赖
pip3 install fastapi uvicorn pydantic

# 默认端口 50088
python3 -m uvicorn main:app --host 0.0.0.0 --port 50088

# 自定义端口
DOCKER_PANEL_PORT=8080 python3 run.py
```

### 方式二：Docker 容器

```bash
# 构建镜像
docker build -t docker-panel .

# 运行（需要挂载 Docker socket）
docker run -d \
  --name docker-panel \
  -p 50088:50088 \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  --restart unless-stopped \
  docker-panel
```

### 方式三：群晖 Docker socket 权限

群晖 NAS 上非 root 用户需要 Docker socket 访问权限：

```bash
# 在 NAS 上执行（需要 sudo）
sudo chown root:administrators /var/run/docker.sock
sudo chmod 660 /var/run/docker.sock
```

> ⚠️ 此权限在 NAS 重启后会重置，建议添加到启动脚本。

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | Web 面板页面 |
| GET | `/api/containers` | 获取所有容器列表 |
| GET | `/api/containers/all-stats` | 获取容器列表 + 实时统计 |
| GET | `/api/container/{id}/stats` | 获取单个容器统计 |
| GET | `/api/system` | 获取系统信息（内存、磁盘、端口） |
| POST | `/api/container/{id}/action` | 容器操作（start/stop/restart） |

### Docker socket 权限问题排查

如果 API 返回权限错误：

```bash
# 检查 Docker socket 权限
ls -la /var/run/docker.sock

# 群晖：将 socket 组改为 administrators
sudo chown root:administrators /var/run/docker.sock
sudo chmod 660 /var/run/docker.sock

# 通用：将当前用户加入 docker 组
sudo usermod -aG docker $USER
```

## 文件结构

```
├── main.py          # 后端 FastAPI + 前端 HTML（单文件）
├── run.py           # 启动入口
├── Dockerfile       # Docker 镜像构建
└── README.md        # 本文件

```

## 技术栈

- **后端**：Python FastAPI + 原生 Docker Unix Socket API
- **前端**：纯 HTML/CSS/JS（无框架依赖，单文件内嵌）
- **Docker 通信**：优先 Unix socket，失败自动 fallback 到 docker CLI

## License

MIT
