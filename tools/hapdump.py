"""Print the HomeKit services and characteristics of a thermostat.

Feed it Home Assistant diagnostics of a HomeKit Device (homekit_controller)
config entry:

    python tools/hapdump.py < config_entry-homekit_controller-XXXX.json

Useful when checking what a new Mysa model exposes locally.
"""

import json
import sys

d = json.load(sys.stdin)["data"]
accs = d.get("entity-map") or []
if not accs:
    print("keys:", list(d.keys()))
    sys.exit()
for a in accs:
    for s in a["services"]:
        print(f"service {s['type']}  iid={s['iid']}")
        for c in s["characteristics"]:
            extra = " ".join(
                f"{k}={c[k]}"
                for k in ("minValue", "maxValue", "minStep", "unit")
                if k in c
            )
            print(
                f"   char {c['type']:38s} perms={','.join(c.get('perms', [])):12s} fmt={c.get('format')} val={c.get('value')!r} {extra}"
            )
