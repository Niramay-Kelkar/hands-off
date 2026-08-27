import json, sys, urllib.request

SID = sys.argv[1]
def turn(msg):
    body = json.dumps({"session_id": SID, "message": msg}).encode()
    req = urllib.request.Request("http://127.0.0.1:8200/chat", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=1200) as r:
        j = json.loads(r.read())
    print(f"\n>>> USER: {msg}")
    print(f"<<< BOT: {j.get('reply') or j.get('error')}")

for line in sys.argv[2:]:
    turn(line)
