FROM freqtradeorg/freqtrade:stable

# Strategies in user_data/strategies/ that read from our Postgres need
# psycopg2. The base image doesn't ship it.
USER root
RUN pip install --no-cache-dir psycopg2-binary
USER ftuser
