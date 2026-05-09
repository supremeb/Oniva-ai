FROM pgvector/pgvector:pg16

# Install build dependencies
RUN apt-get update && apt-get install -y \
    git \
    build-essential \
    postgresql-server-dev-16 \
    && rm -rf /var/lib/apt/lists/*

# Clone and install pg_textsearch
# RUN git clone https://github.com/timescale/pg_textsearch.git /tmp/pg_textsearch \
#    && cd /tmp/pg_textsearch \
#    && make \
#    && make install \
#    && rm -rf /tmp/pg_textsearch

# Clean up build dependencies
RUN apt-get purge -y git build-essential postgresql-server-dev-16 && apt-get autoremove -y
