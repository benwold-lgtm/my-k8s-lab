FROM nvidia/cuda:12.1.0-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y \
    python3 python3-pip git libgl1 libglib2.0-0 ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN git clone https://github.com/comfyanonymous/ComfyUI.git .

RUN pip3 install --no-cache-dir torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121

RUN pip3 install --no-cache-dir -r requirements.txt

EXPOSE 8188

CMD ["python3", "main.py", "--listen", "0.0.0.0", "--port", "8188"]
