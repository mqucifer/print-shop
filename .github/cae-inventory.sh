# The conda packages by name, version, build, channel subdir and MD5, from conda-meta.
/opt/conda/bin/python -I -c '
import json, glob
for f in sorted(glob.glob("/opt/conda/conda-meta/*.json")):
    d = json.load(open(f))
    print(d["name"], d["version"], d["build"], d.get("subdir"), d.get("md5"))
' > /out/$1-packages.txt
# Every file and link under /opt/conda but conda-meta (which records the install's own times),
# by SHA-256 or link target.
cd /opt/conda && find . -path ./conda-meta -prune -o \( -type f -print0 \) | sort -z | xargs -0 sha256sum > /out/$1-files.txt
find . -path ./conda-meta -prune -o \( -type l -printf '%p -> %l\n' \) | sort > /out/$1-links.txt
dpkg-query -W -f '${Package} ${Version}\n' > /out/$1-dpkg.txt
micromamba --version > /out/$1-micromamba.txt
