#!/bin/bash
set -e

# Install system packages required by Manim (Cairo + Pango for Text rendering, FFmpeg for video)
sudo apt-get install -y --no-install-recommends \
    ffmpeg \
    libcairo2-dev \
    libpango1.0-dev \
    libpangocairo-1.0-0 \
    pkg-config \
    fonts-dejavu-core \
    python3-dev \
    gcc \
    libffi-dev

pip install -r requirements.txt
