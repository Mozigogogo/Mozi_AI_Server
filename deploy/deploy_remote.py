"""部署数据代理到新加坡服务器并启动"""
import paramiko
import os

SERVER = '43.134.86.135'
PASSWORD = '7#Q9-nGk'
REMOTE_DIR = '/home/lighthouse/data_proxy'

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(SERVER, username='root', password=PASSWORD, timeout=15)
sftp = ssh.open_sftp()

# 创建目录
for d in [REMOTE_DIR, f'{REMOTE_DIR}/config']:
    try:
        sftp.mkdir(d)
    except IOError:
        pass  # 已存在

# 传输文件
files = [
    (f'{BASE_DIR}/deploy/data_proxy.py', f'{REMOTE_DIR}/data_proxy.py'),
    (f'{BASE_DIR}/config/__init__.py', f'{REMOTE_DIR}/config/__init__.py'),
    (f'{BASE_DIR}/config/settings.py', f'{REMOTE_DIR}/config/settings.py'),
]

for local, remote in files:
    print(f'  uploading {os.path.basename(local)}...')
    sftp.put(local, remote)

sftp.close()
print('Files uploaded.')

# 安装依赖并启动
cmds = [
    'pip install fastapi uvicorn pymysql -q 2>/dev/null',
    'pkill -f "uvicorn data_proxy" 2>/dev/null || true',
    'sleep 1',
    f'cd {REMOTE_DIR} && nohup python -c "import uvicorn; from data_proxy import app; uvicorn.run(app, host=\'0.0.0.0\', port=8001)" > {REMOTE_DIR}/proxy.log 2>&1 &',
    'sleep 3',
    'curl -s http://localhost:8001/health',
]

for cmd in cmds:
    print(f'>>> {cmd[:80]}...')
    stdin, stdout, stderr = ssh.exec_command(cmd)
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    if out:
        print(f'  {out}')

print('\nDone! Proxy: http://43.134.86.135:8001')
ssh.close()
