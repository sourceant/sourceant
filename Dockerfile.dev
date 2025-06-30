FROM python:3.9-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    libpq-dev gcc python3-dev sqlite3 && \
    rm -rf /var/lib/apt/lists/*

COPY . /app/
RUN python -m pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

RUN ln -s /app/sourceant /usr/local/bin/sourceant

EXPOSE 8000

CMD ["/app/start.sh"]
