# Microduck X as a hosted demo: the dashboard and control service in MOCK mode (no MuJoCo, no robotd).
#
#   docker build -t microduck-x microduck-x
#   docker run -p 8765:8765 -e MDX_TOKEN=pick-one -e GROQ_API_KEY=... microduck-x
#   open http://localhost:8765/?token=pick-one
#
# MOCK mode is not the Microduck: the 3D view shows the mock stand-in with a banner, and the duck
# "walks" by integrating the twist. Chat, the Groq brain, the duck's voice and the UI are real.
# The real robot needs MuJoCo, robotd and the policies: scripts/x-setup.sh, scripts/x-start.sh.

FROM node:22-slim AS dashboard
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    MDX_MOCK=1 MDX_HOST=0.0.0.0 MDX_STATE=/tmp/duck-sim
WORKDIR /app
COPY backend/requirements-deploy.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY backend/ backend/
COPY configs/ configs/
COPY --from=dashboard /web/dist frontend/dist
RUN useradd --create-home --uid 10001 duck
USER duck
WORKDIR /app/backend
# Binds MDX_HOST:$PORT (Render sets PORT; 8765 otherwise). Refuses to start without MDX_TOKEN,
# because a non-local bind without one would hand robot control to anyone who finds the URL.
EXPOSE 8765
CMD ["python", "-m", "microduck_x"]
