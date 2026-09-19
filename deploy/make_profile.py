#!/usr/bin/env python3
"""Create the App Store provisioning profile via the API, and install it.

Beats hunting through a portal Apple has redesigned. Needs the bundle ID's
resource id and a distribution certificate's id, both of which it looks up.
"""
import base64, json, os, subprocess, sys, time, urllib.request, urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from asc_query import token, get  # noqa: E402  (reuses the JWT signing)

NAME = "Lucy App Store"
BUNDLE = "ai.kaymo.Lucy"


def post(path, payload):
    req = urllib.request.Request(
        f"https://api.appstoreconnect.apple.com{path}",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {token()}",
                 "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()[:600]}")
        return None


bundle_id = None
for b in (get("/v1/bundleIds?limit=200") or {}).get("data", []):
    if b["attributes"].get("identifier") == BUNDLE:
        bundle_id = b["id"]
print(f"bundleId resource: {bundle_id}")

cert_id = None
for c in (get("/v1/certificates?limit=200") or {}).get("data", []):
    if c["attributes"].get("certificateType") in ("DISTRIBUTION",
                                                  "IOS_DISTRIBUTION"):
        cert_id = c["id"]
        print(f"certificate: {c['attributes'].get('name')} "
              f"({c['attributes'].get('certificateType')})")
if not (bundle_id and cert_id):
    print("missing bundle id or distribution certificate")
    raise SystemExit(1)

# An existing profile of the same name blocks creation.
for p in (get("/v1/profiles?limit=200") or {}).get("data", []):
    if p["attributes"].get("name") == NAME:
        print(f"a profile named {NAME!r} already exists — reusing it")
        prof = p
        break
else:
    prof = (post("/v1/profiles", {
        "data": {
            "type": "profiles",
            "attributes": {"name": NAME, "profileType": "IOS_APP_STORE"},
            "relationships": {
                "bundleId": {"data": {"id": bundle_id, "type": "bundleIds"}},
                "certificates": {"data": [{"id": cert_id,
                                           "type": "certificates"}]},
            },
        }
    }) or {}).get("data")

if not prof:
    raise SystemExit("could not create the profile")

content = prof["attributes"]["profileContent"]
out = os.path.expanduser(
    "~/Library/MobileDevice/Provisioning Profiles/"
    f"{prof['attributes']['uuid']}.mobileprovision")
os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out, "wb") as f:
    f.write(base64.b64decode(content))
print(f"installed: {out}")
print(f"name={prof['attributes']['name']} "
      f"expires={prof['attributes'].get('expirationDate')}")
