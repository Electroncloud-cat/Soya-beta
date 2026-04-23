#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
#  Soya · Zero 3W 一键部署脚本
#  功能：安装依赖 + Cloudflare Tunnel + systemd 开机自启
# ─────────────────────────────────────────────────────────────────────────────
set -e

SOYA_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$SOYA_DIR/venv/bin/python"
USER_NAME="$(whoami)"

echo ""
echo "╔══════════════════════════════════════╗"
echo "║     Soya · Zero 3W 部署脚本          ║"
echo "╚══════════════════════════════════════╝"
echo ""

# ── 1. 系统依赖 ───────────────────────────────────────────────────────────────
echo "[1/5] 安装系统依赖..."
sudo apt-get update -q
sudo apt-get install -y -q python3-pip python3-venv git curl

# ── 2. Python 虚拟环境 ─────────────────────────────────────────────────────────
echo "[2/5] 设置 Python 虚拟环境..."
cd "$SOYA_DIR"
python3 -m venv venv
source venv/bin/activate
pip install --quiet flask requests pymupdf pdfplumber ebooklib psutil

# ── 3. Cloudflare Tunnel ────────────────────────────────────────────────────────
echo "[3/5] 安装 Cloudflare Tunnel (cloudflared)..."
if ! command -v cloudflared &> /dev/null; then
    ARCH=$(uname -m)
    case $ARCH in
        aarch64) CF_ARCH="arm64" ;;
        armv7l)  CF_ARCH="arm"   ;;
        x86_64)  CF_ARCH="amd64" ;;
        *)       CF_ARCH="arm64" ;;
    esac
    curl -fsSL "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${CF_ARCH}" \
        -o /usr/local/bin/cloudflared
    chmod +x /usr/local/bin/cloudflared
    echo "  cloudflared 安装完成"
else
    echo "  cloudflared 已存在，跳过"
fi

# ── 4. systemd 服务 ───────────────────────────────────────────────────────────
echo "[4/5] 创建 systemd 服务..."
sudo tee /etc/systemd/system/soya.service > /dev/null <<EOF
[Unit]
Description=Soya AI Server
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=${SOYA_DIR}
ExecStart=${PYTHON} server.py
Restart=always
User=${USER_NAME}
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/soya-tunnel.service > /dev/null <<EOF
[Unit]
Description=Soya Cloudflare Tunnel
After=network-online.target soya.service
Wants=network-online.target

[Service]
ExecStart=/usr/local/bin/cloudflared tunnel --no-autoupdate run --url http://localhost:5000
Restart=always
User=${USER_NAME}

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable soya soya-tunnel
echo "  服务已注册"

# ── 5. 启动 ──────────────────────────────────────────────────────────────────
echo "[5/5] 启动服务..."
sudo systemctl start soya
sleep 2
sudo systemctl start soya-tunnel
sleep 3

echo ""
echo "部署完成！"
echo "  本机访问：http://localhost:5000"
echo ""
echo "  查看远程地址（Cloudflare Tunnel）："
echo "    sudo journalctl -u soya-tunnel -n 30"
echo "  找到形如 https://xxx.trycloudflare.com 的地址"
echo ""
echo "  将该地址填入 widget_config.json 的 server 字段，"
echo "  Windows 端运行 monitor_widget.py 即可远程监控"
echo ""
