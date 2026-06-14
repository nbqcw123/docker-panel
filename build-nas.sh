#!/bin/bash
# build-and-deploy.sh - 在 NAS 上构建并部署 docker-panel
# 需要先通过 SCP/SSH 把 main.py 和 Dockerfile 传到 NAS

set -e

echo "=== 安装依赖 ==="
# 群晖自带 python3
pip3 install --user fastapi uvicorn pydantic 2>/dev/null || true

echo "=== 创建目录 ==="
mkdir -p /var/services/homes/nbtivon/docker-panel-app

echo "=== 启动面板 ==="
cd /var/services/homes/nbtivon/docker-panel-app

# 停止旧容器（如果通过 Docker 运行）
sudo /volume1/@appstore/ContainerManager/usr/bin/docker stop docker-panel 2>/dev/null || true
sudo /volume1/@appstore/ContainerManager/usr/bin/docker rm docker-panel 2>/dev/null || true

echo "=== 部署完成 ==="
echo "访问: http://100.113.206.33:50087"
