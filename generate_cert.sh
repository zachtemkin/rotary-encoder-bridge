#!/bin/bash
# Generate self-signed SSL certificate for rotary encoder bridge

echo "Generating self-signed SSL certificate..."

# Generate private key and certificate
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes \
    -subj "/C=US/ST=CA/L=San Francisco/O=Rotary Encoder Bridge/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,DNS:127.0.0.1,IP:127.0.0.1,IP:0.0.0.0"

echo "Certificate generated:"
echo "  cert.pem - Certificate file"
echo "  key.pem - Private key file"
echo ""
echo "You can now run the encoder bridge with HTTPS/WSS support!"
