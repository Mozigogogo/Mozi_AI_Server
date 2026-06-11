#!/bin/bash
# 部署远程数据代理到新加坡服务器
# 用法: bash deploy_proxy.sh

set -e

SERVER="root@43.134.86.135"
REMOTE_DIR="/home/lighthouse/data_proxy"
PASSWORD='7#Q9-nGk'

echo ">>> 传输文件到 ${SERVER}..."

sshpass -p "$PASSWORD" ssh -o StrictHostKeyChecking=no $SERVER "mkdir -p ${REMOTE_DIR}"

# 传输必要文件
sshpass -p "$PASSWORD" scp -o StrictHostKeyChecking=no \
    deploy/data_proxy.py \
    ${SERVER}:${REMOTE_DIR}/

sshpass -p "$PASSWORD" scp -o StrictHostKeyChecking=no \
    config/settings.py \
    ${SERVER}:${REMOTE_DIR}/

# 创建目录结构
sshpass -p "$PASSWORD" ssh $SERVER "mkdir -p ${REMOTE_DIR}/config"

sshpass -p "$PASSWORD" scp -o StrictHostKeyChecking=no \
    config/__init__.py \
    ${SERVER}:${REMOTE_DIR}/config/

sshpass -p "$PASSWORD" scp -o StrictHostKeyChecking=no \
    config/settings.py \
    ${SERVER}:${REMOTE_DIR}/config/settings.py

echo ">>> 启动代理服务..."
sshpass -p "$PASSWORD" ssh $SERVER "
    cd ${REMOTE_DIR}
    # 安装依赖
    pip install fastapi uvicorn pymysql -q 2>/dev/null
    # 杀旧进程
    pkill -f 'uvicorn data_proxy' 2>/dev/null || true
    sleep 1
    # 启动
    nohup python -c '
import uvicorn
from data_proxy import app
uvicorn.run(app, host=\"0.0.0.0\", port=8001)
' > ${REMOTE_DIR}/proxy.log 2>&1 &
    sleep 3
    curl -s http://localhost:8001/health
"

echo ""
echo ">>> 部署完成！代理地址: http://43.134.86.135:8001"
