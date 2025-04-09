FROM nvidia/cuda:12.8.0-devel-ubuntu22.04

# Instalar Python 3.11
RUN apt-get update && apt-get install -y \
    python3.11 \
    python3.11-dev \
    python3-pip \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxrender1 \
    libxext6 \
    git \
    curl && \
    rm -rf /var/lib/apt/lists/*

# Definir Python 3.11 como padrão
RUN ln -sf /usr/bin/python3.11 /usr/bin/python3 && \
    ln -sf /usr/bin/python3 /usr/bin/python && \
    ln -sf /usr/bin/pip3 /usr/bin/pip

# Define variáveis de ambiente
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Define diretório de trabalho
WORKDIR /app

# Copia e instala dependências Python
COPY requirements.txt .

# Instalar dependências, forçando versão específica do transformers e PyTorch com CUDA
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir --force-reinstall transformers==4.46.0 && \
    pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cu128

# Configurar para usar GPU
ENV CUDA_VISIBLE_DEVICES=0

# Copia o código da aplicação
COPY app/ ./app

# Comando para iniciar a API
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]