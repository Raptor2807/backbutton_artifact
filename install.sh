#!/usr/bin/env bash
# Set up a measurement host. Debian/Ubuntu. Run as root or with sudo.
#
# Pin Chrome and chromedriver to the same major version. A mismatch produces a
# run in which every measurement fails, which is the most common way this
# breaks and it fails silently per-domain rather than at startup.
set -euo pipefail

CHROME_VERSION="${CHROME_VERSION:-}"   # e.g. 131.0.6778.85; see crawler/VERSIONS.txt
VENV="${VENV:-/opt/bbh_venv}"

if [ -z "$CHROME_VERSION" ]; then
  echo "Set CHROME_VERSION to the version in crawler/VERSIONS.txt, e.g."
  echo "  CHROME_VERSION=131.0.6778.85 ./install.sh"
  exit 1
fi

apt-get update
apt-get install -y python3 python3-venv python3-pip unzip curl openvpn

# Chrome
curl -fsSL -o /tmp/chrome.deb \
  "https://dl.google.com/linux/chrome/deb/pool/main/g/google-chrome-stable/google-chrome-stable_${CHROME_VERSION}-1_amd64.deb"
apt-get install -y /tmp/chrome.deb

# chromedriver for the same version
curl -fsSL -o /tmp/chromedriver.zip \
  "https://storage.googleapis.com/chrome-for-testing-public/${CHROME_VERSION}/linux64/chromedriver-linux64.zip"
unzip -o -j /tmp/chromedriver.zip 'chromedriver-linux64/chromedriver' -d /usr/local/bin/
chmod +x /usr/local/bin/chromedriver

# Python environment
python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip
"$VENV/bin/pip" install -r requirements.txt

echo
google-chrome --version
chromedriver --version
"$VENV/bin/python" -c "import selenium; print('selenium', selenium.__version__)"
echo
echo "Major versions of Chrome and chromedriver must match."
