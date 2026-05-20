from py_vapid import Vapid
import base64

vapid = Vapid()
vapid.generate_keys()

# Export private and public keys in base64url format
def b64url(b):
    return base64.urlsafe_b64encode(b).decode('utf-8').rstrip('=')

# Extract key coordinates/parameters
# Vapid stores keys using cryptography.hazmat.primitives.asymmetric.ec
private_key = vapid.private_key
public_key = private_key.public_key()

# To get the public key in uncompressed form (65 bytes) required for applicationServerKey
from cryptography.hazmat.primitives import serialization
pub_bytes = public_key.public_bytes(
    encoding=serialization.Encoding.X962,
    format=serialization.PublicFormat.UncompressedPoint
)
priv_bytes = private_key.private_numbers().private_value.to_bytes(32, byteorder='big')

print("VAPID_PUBLIC_KEY =", b64url(pub_bytes))
print("VAPID_PRIVATE_KEY =", b64url(priv_bytes))
