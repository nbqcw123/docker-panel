#!/bin/bash
# run-docker-panel.sh - 直接在 NAS 上用 Python 运行 Docker 管理面板
# 不需要 Docker 容器，直接在 NAS 上运行 Python

echo "=== 检查 Python ==="
python3 --version || exit 1

echo "=== 安装依赖 ==="
pip3 install --user fastapi uvicorn pydantic 2>&1 | tail -3

echo "=== 创建目录 ==="
mkdir -p /var/services/homes/nbtivon/docker-panel-app

echo "=== 启动面板 ==="
cd /var/services/homes/nbtivon/docker-panel-app

# 停止旧进程
pkill -f "uvicorn.*docker-panel" 2>/dev/null || true
pkill -f "python3.*docker-panel" 2>/dev/null || true
pkill -f "main.py" 2>/dev/null || true

# 停止同名 Docker 容器（如果存在）
sudo /volume1/@appstore/ContainerManager/usr/bin/docker stop docker-panel 2>/dev/null || true
sudo /volume1/@appstore/ContainerManager/usr/bin/docker rm docker-panel 2>/dev/null || true

# 启动（后台运行）
nohup python3 main.py > /tmp/docker-panel.log 2>&1 &
echo "PID: $!"

sleep 3

# 验证
if curl -s http://localhost:50087/api/system > /dev/null 2>&1; then
    echo "✅ 面板已启动!"
    echo "访问: http://100.113.206.33:50087"
else
    echo "⚠️ 启动中，查看日志: cat /tmp/docker-panel.log"
fi
