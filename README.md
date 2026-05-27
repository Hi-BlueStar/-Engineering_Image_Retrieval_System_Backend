# 文件

後端文件

## 1. Docker

```bash
docker build -t="engineering_image_retrieval_system_backend:v1.0" .

# docker compose build --no-cache backend

docker compose down

docker compose up -d

```

或是

```bash
docker build -t="engineering_image_retrieval_system_backend:v1.0" . && docker compose down && docker compose up -d
```

## 2. 進入 Docker 容器

```bash
docker exec -ti engineering_image_retrieval_system_backend bash
```

## 3. 執行腳本

```bash
uv run python scripts/analyze_pth.py model/checkpoint_best.pth

uv run python scripts/analyze_pth.py model/checkpoint_best.pth model/checkpoint_best_0512.pth
```

<!-- 
或是

```bash
docker run -d --gpus all --name engineering_image_retrieval_system_dev engineering_image_retrieval_system_backend:v1.0
```

```bash
/opt/venv/bin/python
```

## 執行專案

使用以下指令，在本地虛擬環境啟動後端

```bash
uv run uvicorn src.main:app --reload --port 8002
``` -->
