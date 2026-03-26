# syntax=docker/dockerfile:1
# 啟用 BuildKit 增強語法

# ==========================================
# Stage 1: Builder (編譯與依賴安裝層)
# ==========================================
FROM nvidia/cuda:12.9.1-cudnn-runtime-ubuntu22.04 AS builder

# 1. 建置期變數與 uv 配置
ENV DEBIAN_FRONTEND=noninteractive
# 設定 uv 建立 venv 的位置，這很重要，確保 builder 階段就建立在 /opt/venv
ENV UV_PROJECT_ENVIRONMENT="/opt/venv" \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_CACHE_DIR="/root/.cache/uv"

# 2. 系統依賴 (使用 apt 快取掛載)
RUN rm -f /etc/apt/apt.conf.d/docker-clean; \
    echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    build-essential python3 python3-dev python-is-python3 git ca-certificates

# 3. 安裝 uv (從官方二進位檔複製)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /build

# 4. 安裝 Python 依賴 (使用 uv 快取掛載)
# 先複製定義檔以利用 Layer Cache
COPY pyproject.toml uv.lock* .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

# ==========================================
# Stage 2: Final (生產與開發執行層)
# ==========================================
FROM nvidia/cuda:12.9.1-cudnn-runtime-ubuntu22.04 AS final

# --- 關鍵修正 1: 全域環境變數定義 ---
# 定義在 Docker 層，確保 docker exec/run 有效
ENV VIRTUAL_ENV=/opt/venv \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    LD_LIBRARY_PATH="/usr/local/cuda/lib64:${LD_LIBRARY_PATH}" \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

# 2. 系統依賴安裝 (使用 apt 快取掛載)
RUN rm -f /etc/apt/apt.conf.d/docker-clean; \
    echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    python3 python-is-python3 ca-certificates \
    # GUI & OpenCV 必要庫
    libgl1 libglx-mesa0 libegl1 libglx0 libsm6 libxext6 libxrender1 \
    libglib2.0-0 \
    libgl1-mesa-glx \
    # 工具與 SSH
    openssh-server sudo libdbus-1-3 tmux curl git wget vim nano \
    && rm -rf /var/lib/apt/lists/*

# 3. 複製編譯好的虛擬環境與 uv 工具
COPY --from=builder /opt/venv /opt/venv

# 4. 安裝 uv (方便在容器內執行 uv add/sync)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 7. 建置期環境驗證 (吸納 B 版本優點)
# 驗證 CUDA 與 Python 環境是否正常銜接
RUN python -c "import sys; print(f'Python Path: {sys.executable}');" && \
    python -c "import torch; print(f'Torch OK. Version: {torch.__version__}, CUDA: {torch.version.cuda}')" || echo "Warning: Torch or CUDA not found during build."

# 8. Entrypoint 與啟動設定
WORKDIR /workspace
EXPOSE 22

COPY <<EOF /entrypoint.sh
#!/bin/bash
set -e
echo "==========================================="
echo "          Development Environment          "
echo "==========================================="
echo "啟動時間: $(date)"
echo "當前用戶: $(whoami) (UID: $(id -u))"

# 1. 驗證 Python 虛擬環境
echo "--- Environment Check ---"
if [ -n "$VIRTUAL_ENV" ]; then
    echo "✅ VIRTUAL_ENV 偵測成功: $VIRTUAL_ENV"
else
    echo "⚠️  警告: 未偵測到 VIRTUAL_ENV 變數"
fi

# 2. 顯示關鍵路徑，方便排錯
echo "Which Python: $(which python)"
echo "Python Version: $(python --version 2>&1)"
echo "CUDA Library Path: $LD_LIBRARY_PATH"
echo "-------------------------------------------"

# 3. 執行 CMD 傳入的指令 (關鍵步驟)
# 使用 exec 確保傳入的指令（如 sshd）接管 PID 1
exec "\$@"
EOF

RUN chmod +x /entrypoint.sh

RUN python -c "import torch; print(f'Torch: {torch.__version__}, CUDA: {torch.version.cuda}')" || true

ENTRYPOINT ["/entrypoint.sh"]
# CMD ["uv", "run", "uvicorn", "src.main:app", "--reload", "--port", "8002"]
