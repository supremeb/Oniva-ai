FROM e2bdev/code-interpreter:latest

# Data science (some may already be in base)
RUN pip install pandas numpy matplotlib seaborn scipy statsmodels scikit-learn

# Finance
RUN pip install yfinance

# Web scraping
RUN pip install requests beautifulsoup4 httpx

# Switch to root for system package installs
USER root

# Dev tools
RUN apt-get update && apt-get install -y jq curl wget && rm -rf /var/lib/apt/lists/*

# WeasyPrint system deps (pango, cairo, gdk-pixbuf)
RUN apt-get update && apt-get install -y \
    libpango1.0-dev libcairo2-dev libgdk-pixbuf-xlib-2.0-dev libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Browser automation (Playwright + Browser Use)
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN pip install playwright==1.49.1 browser-use==0.1.40 \
    && playwright install --with-deps chromium \
    && chmod -R 777 /ms-playwright \
    && echo 'export PLAYWRIGHT_BROWSERS_PATH=/ms-playwright' >> /etc/profile.d/playwright.sh \
    && echo 'export PLAYWRIGHT_BROWSERS_PATH=/ms-playwright' >> /root/.bashrc \
    && echo 'export PLAYWRIGHT_BROWSERS_PATH=/ms-playwright' >> /home/user/.bashrc 2>/dev/null || true

# Document generation (output engine)
RUN pip install weasyprint python-pptx python-docx openpyxl plotly kaleido PyMuPDF

# Create workspace directory
RUN mkdir -p /workspace && chmod 777 /workspace
WORKDIR /workspace

# Switch back to default user
USER user
