set -eu

CERT_DIR=/etc/nginx/certs
CRT="$CERT_DIR/gideon.crt"
KEY="$CERT_DIR/gideon.key"

if [ -f "$CRT" ] && [ -f "$KEY" ]; then
    echo "self-signed-cert: existing cert found at $CRT — leaving it in place"
    exit 0
fi

mkdir -p "$CERT_DIR"
echo "self-signed-cert: generating a self-signed cert at $CRT"
openssl req -x509 -newkey rsa:2048 -nodes \
    -keyout "$KEY" -out "$CRT" -days 3650 \
    -subj "/CN=gideon.local" \
    -addext "subjectAltName=DNS:localhost,DNS:gideon.local,IP:127.0.0.1" \
    2>/dev/null
chmod 600 "$KEY"
