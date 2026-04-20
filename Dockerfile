FROM python:3.12-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY pyproject.toml README.md ./
COPY src/ src/

# 安装 Python 依赖
RUN pip install --no-cache-dir -e .

# 创建数据目录
RUN mkdir -p /app/data /app/memory /app/skills

# 暴露端口
EXPOSE 8000

# 默认启动 API 服务
CMD ["team-agent", "serve", "--host", "0.0.0.0", "--port", "8000"]
