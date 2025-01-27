# 镜像名称和标签
IMAGE_NAME = erc20-monitor-bot
IMAGE_TAG = latest

# 加载环境变量
ifneq (,$(wildcard .env))
    include .env
    export
endif

# 检查必要的环境变量
check-env:
	@if [ -z "$$TG_BOT_TOKEN" ]; then \
		echo "Error: TG_BOT_TOKEN is not set"; \
		exit 1; \
	fi

# 创建必要的目录
init:
	mkdir -p data

# 默认目标
.PHONY: all
all: check-env build

# 构建 Docker 镜像
.PHONY: build
build:
	docker build -t $(IMAGE_NAME):$(IMAGE_TAG) .

# 运行容器
.PHONY: run
run: check-env
	docker run -d \
		--name $(IMAGE_NAME) \
		-v $(PWD)/data:/app/data \
		-e TG_BOT_TOKEN=${TG_BOT_TOKEN} \
		$(IMAGE_NAME):$(IMAGE_TAG)

# 停止并删除容器
.PHONY: stop
stop:
	docker stop $(IMAGE_NAME) || true
	docker rm $(IMAGE_NAME) || true

# 清理所有资源
.PHONY: clean
clean: stop
	docker rmi $(IMAGE_NAME):$(IMAGE_TAG) || true
	rm -rf data/*.db

# 查看日志
.PHONY: logs
logs:
	docker logs -f $(IMAGE_NAME)

# 本地开发运行
.PHONY: dev
dev: check-env init
	python3 bot.py

# 安装依赖
.PHONY: install
install:
	pip install -r requirements.txt

# 检查 Python 环境
.PHONY: check-python
check-python:
	@python3 -c "import sys; print('Python version:', sys.version)" || (echo "Error: Python 3 is not installed"; exit 1)
	@pip -V || (echo "Error: pip is not installed"; exit 1)

# 帮助信息
.PHONY: help
help:
	@echo "可用的命令:"
	@echo "  make install    - 安装依赖"
	@echo "  make init      - 初始化项目目录"
	@echo "  make dev       - 本地开发运行"
	@echo "  make build     - 构建 Docker 镜像"
	@echo "  make run       - 运行 Docker 容器"
	@echo "  make stop      - 停止 Docker 容器"
	@echo "  make logs      - 查看容器日志"
	@echo "  make clean     - 清理所有资源" 