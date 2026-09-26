#!/bin/sh
# Download the public-domain and openly licensed sources into sources/ and check them.
set -eu
cd "$(dirname "$0")/../sources"
for id in 22 28900 37683 38390 51155 73237; do
  curl -fsSL -o "pg$id.txt" "https://www.gutenberg.org/cache/epub/$id/pg$id.txt"
done
curl -fsSL -o english-wordnet-2025.xml.gz \
  https://github.com/globalwordnet/english-wordnet/releases/download/2025-edition/english-wordnet-2025.xml.gz
if command -v sha256sum >/dev/null 2>&1; then
  sha256sum -c ../pipeline/sources.sha256
else
  shasum -a 256 -c ../pipeline/sources.sha256     # macOS
fi
