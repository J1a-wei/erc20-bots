# 使用 Python 3.10 作为基础镜像
FROM python:3.10-slim

# 设置工作目录
WORKDIR /app

# 复制依赖文件
COPY requirements.txt .

# 安装依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY . .

# 创建数据卷，用于持久化数据库
VOLUME ["/app/data"]

# 设置环境变量
ENV PYTHONUNBUFFERED=1 \
    DB_DIR=/app/data 
# 运行应用
CMD ["python", "bot.py"] 