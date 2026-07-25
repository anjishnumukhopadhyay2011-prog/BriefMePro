import os
import certifi

# Make sure Python trusts a real certificate bundle for outbound HTTPS
# (news feed fetching, country data, etc.)
os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

from ooe.config import load_settings
from ooe.server import serve

if __name__ == "__main__":
    settings = load_settings()
    serve(settings)
