#!/bin/sh
# One-time, no sudo: unpack the libraries Remotion's headless Chrome needs on a bare WSL Ubuntu (NSS) plus an emoji
# font (the care app shows 🕒) into video/.local-libs/. Nothing is installed system-wide. render.sh and
# capture_screens.mjs pick them up automatically.
set -e
cd "$(dirname "$0")/.."
mkdir -p .local-libs/deb
( cd .local-libs/deb && apt-get download libnss3 libnspr4 fonts-noto-color-emoji && for d in *.deb; do dpkg -x "$d" ..; done )
cat > .local-libs/fonts.conf <<'EOF'
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <include ignore_missing="yes">/etc/fonts/fonts.conf</include>
  <dir prefix="relative">usr/share/fonts</dir>
  <cachedir prefix="relative">fc-cache</cachedir>
</fontconfig>
EOF
npx remotion browser ensure
echo "local libs ready in .local-libs/"
