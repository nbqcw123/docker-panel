#!/bin/bash
# deploy-docker-panel.sh - 在群晖 NAS 上部署 Docker 管理面板
# 用法: sudo bash deploy-docker-panel.sh

set -e

IMAGE_TAR="/var/services/homes/nbtivon/docker-panel.tar.gz"
IMAGE_NAME="docker-panel:latest"
CONTAINER_NAME="docker-panel"
PORT=50087

echo "=== Docker 管理面板部署脚本 ==="

# 1. 加载镜像
if [ -f "$IMAGE_TAR" ]; then
    echo "[1/4] 加载 Docker 镜像..."
    sudo docker load -i "$IMAGE_TAR"
    echo "镜像加载完成"
else
    echo "错误: 找不到镜像文件 $IMAGE_TAR"
    exit 1
fi

# 2. 停止并删除旧容器
echo "[2/4] 清理旧容器..."
sudo docker stop "$CONTAINER_NAME" 2>/dev/null || true
sudo docker rm "$CONTAINER_NAME" 2>/dev/null || true

# 3. 启动新容器
echo "[3/4] 启动容器..."
sudo docker run -d \
    --name "$CONTAINER_NAME" \
    --restart unless-stopped \
    -p "$PORT:50087" \
    -v /var/run/docker.sock:/var/run/docker.sock:ro \
    "$IMAGE_NAME"

# 4. 验证
echo "[4/4] 验证部署..."
sleep 3
if sudo docker ps | grep -q "$CONTAINER_NAME"; then
    echo "✅ 容器运行中!"
    echo "访问地址: http://$(hostname -I | awk '{print $1}'):$PORT"
else
    echo "❌ 容器启动失败，查看日志:"
    sudo docker logs "$CONTAINER_NAME"
fi
