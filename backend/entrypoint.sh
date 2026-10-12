#!/bin/bash
set -e

echo "Waiting for database..."
python -c "
import psycopg2, time, os
host = os.getenv('DB_HOST', 'postgres')
port = os.getenv('DB_PORT', '5432')
user = os.getenv('DB_USER', 'postgres')
password = os.getenv('DB_PASSWORD', 'postgres')
count = 0
while count < 30:
    try:
        psycopg2.connect(host=host, port=port, user=user, password=password, dbname='postgres')
        print('Database ready')
        break
    except Exception:
        time.sleep(1)
        count += 1
else:
    print('Database connection timeout')
    exit(1)
"

echo "Waiting for Redis..."
python -c "
import redis, time, os
host = os.getenv('REDIS_HOST', 'broker')
port = int(os.getenv('REDIS_PORT', '6379'))
count = 0
while count < 30:
    try:
        r = redis.Redis(host=host, port=port, socket_connect_timeout=1)
        r.ping()
        print('Redis ready')
        break
    except Exception:
        time.sleep(1)
        count += 1
else:
    print('Redis connection timeout')
    exit(1)
"

exec "$@"
