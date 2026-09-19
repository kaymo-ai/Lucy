#!/usr/bin/env python3
"""Ask App Store Connect what it actually thinks exists.

Signs an ES256 JWT with openssl -- no pyjwt, no cryptography -- so it runs on a
bare Python. Prints the apps the key can see and the bundle IDs registered,
which is the difference between "the app record is missing" and "the key cannot
see it", and those need opposite fixes.
"""
import base64, json, os, subprocess, sys, time, urllib.request

KEY_ID = os.environ["ASC_KEY_ID"]
ISSUER = os.environ["ASC_ISSUER_ID"]
KEY = os.path.expanduser(f"~/.appstoreconnect/private_keys/AuthKey_{KEY_ID}.p8")


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def token() -> str:
    header = {"alg": "ES256", "kid": KEY_ID, "typ": "JWT"}
    payload = {"iss": ISSUER, "iat": int(time.time()),
               "exp": int(time.time()) + 600, "aud": "appstoreconnect-v1"}
    signing_input = f"{b64(json.dumps(header).encode())}." \
                    f"{b64(json.dumps(payload).encode())}"
    # openssl emits a DER signature; JWS wants raw r||s, so unpack it.
    der = subprocess.run(["openssl", "dgst", "-sha256", "-sign", KEY],
                         input=signing_input.encode(),
                         capture_output=True, check=True).stdout
    # DER: 30 len 02 rlen r 02 slen s
    i = 4 if der[3] != 0x00 else 4
    rlen = der[3]
    r = der[4:4 + rlen]
    slen = der[5 + rlen]
    s = der[6 + rlen:6 + rlen + slen]
    r = r.lstrip(b"\x00").rjust(32, b"\x00")
    s = s.lstrip(b"\x00").rjust(32, b"\x00")
    return f"{signing_input}.{b64(r + s)}"


def get(path: str):
    req = urllib.request.Request(
        f"https://api.appstoreconnect.apple.com{path}",
        headers={"Authorization": f"Bearer {token()}"})
    try:
        with urllib.request.urlopen(req) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"  HTTP {e.code}: {body[:400]}")
        return None


if __name__ == "__main__":
    print("== apps this key can see ==")
    apps = get("/v1/apps")
    if apps is not None:
        if not apps.get("data"):
            print("  NONE. No app record exists (or the key cannot see any).")
        for a in apps.get("data", []):
            at = a["attributes"]
            print(f"  {at.get('bundleId'):32} {at.get('name')}")

    print("\n== bundle IDs registered to the team ==")
    bids = get("/v1/bundleIds?limit=200")
    if bids is not None:
        found = False
        for b in bids.get("data", []):
            at = b["attributes"]
            if "kaymo" in (at.get("identifier") or "").lower():
                print(f"  {at.get('identifier'):32} {at.get('name')}")
                found = True
        if not found:
            print(f"  no ai.kaymo.* identifier among {len(bids.get('data', []))}")

    print("\n== certificates the key can see ==")
    certs = get("/v1/certificates?limit=200")
    if certs is not None:
        kinds = {}
        for c in certs.get("data", []):
            t = c["attributes"].get("certificateType")
            kinds[t] = kinds.get(t, 0) + 1
        print(f"  {kinds or 'none'}")
