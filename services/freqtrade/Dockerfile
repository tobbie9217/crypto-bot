FROM freqtradeorg/freqtrade:stable

# Strategies in user_data/strategies/ that read from our Postgres need
# psycopg2. The base image doesn't ship it.
USER root
RUN pip install --no-cache-dir psycopg2-binary

# Install a sitecustomize.py so freqtrade accepts our custom pairlists.
# See sitecustomize.py for the explanation. We copy to whichever
# site-packages dir Python reports at build time so we're robust to
# Python version bumps in the base image.
COPY sitecustomize.py /tmp/sitecustomize.py
RUN cp /tmp/sitecustomize.py "$(python -c 'import site; print(site.getsitepackages()[0])')/sitecustomize.py" \
    && rm /tmp/sitecustomize.py

USER ftuser
