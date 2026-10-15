FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Serve externally; cloud hosts inject $PORT. Override locally if needed.
ENV HOST=0.0.0.0 \
    PORT=8000
EXPOSE 8000

# The bot loop resumes its last run-state (data/runtime_state.json) on boot,
# so "started" stays started across restarts. Pair with the host's
# restart-on-crash policy (docker run --restart=always, or the platform default).
CMD ["python", "server.py"]
