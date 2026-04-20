#!/bin/bash
# Team Agent 一键部署脚本 — 支持宝塔面板

set -e

echo "======================================"
echo "  Team Agent 一键部署脚本"
echo "======================================"

# 检查 Docker
if ! command -v docker &> /dev/null; then
    echo "❌ Docker 未安装，正在安装..."
    curl -fsSL https://get.docker.com | sh
    systemctl start docker
    systemctl enable docker
    echo "✅ Docker 安装完成"
else
    echo "✅ Docker 已安装: $(docker --version)"
fi

# 检查 Docker Compose
if ! command -v docker compose &> /dev/null; then
    echo "❌ Docker Compose 未安装"
    echo "请手动安装: https://docs.docker.com/compose/install/"
    exit 1
else
    echo "✅ Docker Compose 已安装"
fi

# 创建 .env 文件
if [ ! -f .env ]; then
    echo ""
    echo "📝 配置环境变量..."
    read -p "请输入 OpenAI API Key (可留空): " OPENAI_KEY
    read -p "请输入 Anthropic API Key (可留空): " ANTHROPIC_KEY

    cat > .env << EOF
OPENAI_API_KEY=${OPENAI_KEY}
ANTHROPIC_API_KEY=${ANTHROPIC_KEY}
EOF
    echo "✅ .env 文件已创建"
fi

# 创建必要目录
mkdir -p data memory skills

# 构建并启动
echo ""
echo "🚀 正在构建和启动服务..."
docker compose up -d --build

echo ""
echo "======================================"
echo "  ✅ 部署完成!"
echo "======================================"
echo ""
echo "  API 地址: http://$(hostname -I | awk '{print $1}'):8000"
echo "  API 文档: http://$(hostname -I | awk '{print $1}'):8000/docs"
echo ""
echo "  管理命令:"
echo "    查看日志: docker compose logs -f"
echo "    停止服务: docker compose down"
echo "    重启服务: docker compose restart"
echo ""
