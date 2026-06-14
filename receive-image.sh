#!/bin/bash
# receive-image.sh - 在 NAS 上接收分块的 docker 镜像
# 用法: cat receive-image.sh | ssh ... | bash

echo "=== 清理旧文件 ==="
rm -f /var/services/homes/nbtivon/dp_chunk_* /var/services/homes/nbtivon/docker-panel-new.tar.gz

echo "=== 等待接收分块 ==="
echo "请从本地机器运行: bash /tmp/send-chunks.sh"
echo ""

# 等待所有分块到达
EXPECTED=12
for i in $(seq 1 60); do
    COUNT=$(ls /var/services/homes/nbtivon/dp_chunk_* 2>/dev/null | wc -l)
    if [ "$COUNT" -ge "$EXPECTED" ]; then
        echo "✅ 收到 $COUNT 个分块"
        break
    fi
    echo "等待中... ($COUNT/$EXPECTED)"
    sleep 5
done

echo "=== 合并分块 ==="
cat /var/services/homes/nbtivon/dp_chunk_* > /var/services/homes/nbtivon/docker-panel-new.tar.gz

echo "=== 验证大小 ==="
ls -lh /var/services/homes/nbtivon/docker-panel-new.tar.gz

echo "=== 加载镜像 ==="
sudo /volume1/@appstore/ContainerManager/usr/bin/docker load -i /var/services/homes/nbtivon/docker-panel-new.tar.gz

echo "=== 停止旧容器 ==="
sudo /volume1/@appstore/ContainerManager/usr/bin/docker stop docker-panel 2>/dev/null || true
sudo /volume1/@appstore/ContainerManager/usr/bin/docker rm docker-panel 2>/dev/null || true

echo "=== 启动新容器 ==="
sudo /volume1/@appstore/ContainerManager/usr/bin/docker run -d \
  --name docker-panel \
  --restart unless-stopped \
  -p 50087:50087 \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  docker-panel:latest

echo "=== 验证 ==="
sleep 3
sudo /volume1/@appstore/ContainerManager/usr/bin/docker ps --filter name=docker-panel --format '{{.Names}} {{.Status}}'

echo "=== 完成 ==="
