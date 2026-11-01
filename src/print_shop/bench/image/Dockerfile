# The small bench's image (mqucifer/print-shop#14): CadQuery as the model already writes
# it, Manifold and Shapely for the second checks, and OSMesa for renders. It holds no
# bench code: `print_shop.bench.host` mounts that read-only at run time, so a change to
# the code never costs Docker's disk another image. Built by `scripts/bench.py build`,
# which refuses when the disk would fall below its floor. No container of it has a
# network. It's built for arm64 on the Mac and amd64 in CI, from the same pins.
FROM python:3.12-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70

# These first three steps are mqucifer/model-performance's `cad_eval/image/Dockerfile`,
# byte for byte (its requirements too, in base/). That's the CadQuery 2.8.0 its numbers
# were measured on, and on a machine holding that image they cost no more disk.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libxrender1 libxext6 libsm6 \
    && rm -rf /var/lib/apt/lists/*
COPY base/requirements.txt /opt/cad-eval/requirements.txt
RUN pip install --no-cache-dir --no-deps -r /opt/cad-eval/requirements.txt \
    && python -c "import cadquery, trimesh; print(cadquery.__version__, trimesh.__version__)"

# What the bench adds, locked with hashes by `uv pip compile --generate-hashes`.
COPY requirements.txt /opt/bench/requirements.txt
RUN pip install --no-cache-dir --no-deps --require-hashes -r /opt/bench/requirements.txt \
    && python -c "import manifold3d, rtree, shapely; print(shapely.__version__)"

# OSMesa, so VTK renders with no display, at its pinned version from Debian's archive as
# it stood on a fixed date.
ARG SNAPSHOT=20261027T000000Z
RUN rm -f /etc/apt/sources.list.d/debian.sources \
    && printf '%s\n' \
       "deb http://snapshot.debian.org/archive/debian/${SNAPSHOT} bookworm main" \
       "deb http://snapshot.debian.org/archive/debian/${SNAPSHOT} bookworm-updates main" \
       "deb http://snapshot.debian.org/archive/debian-security/${SNAPSHOT} bookworm-security main" \
       > /etc/apt/sources.list.d/snapshot.list \
    && apt-get -o Acquire::Check-Valid-Until=false update \
    && apt-get install -y --no-install-recommends libosmesa6=22.3.6-1+deb12u2 \
    && rm -rf /var/lib/apt/lists/*

# It runs as the invoking user on a read-only filesystem: nothing here may need to write
# outside /work and /tmp.
ENV PYTHONDONTWRITEBYTECODE=1 MPLCONFIGDIR=/tmp NUMBA_CACHE_DIR=/tmp \
    VTK_DEFAULT_OPENGL_WINDOW=vtkOSOpenGLRenderWindow
