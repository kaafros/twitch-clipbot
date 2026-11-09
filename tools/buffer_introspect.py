import json, os, sys, requests
def q(query, v=None):
    r = requests.post("https://api.buffer.com", json={"query": query, "variables": v or {}},
                      headers={"Authorization": f"Bearer {os.environ['BUFFER_API_KEY']}"}, timeout=60)
    return r.json()
t = q('{ __type(name: "Post") { fields { name type { name kind ofType { name } } } } }')
print("POSTFIELDS", json.dumps([f["name"] for f in t["data"]["__type"]["fields"]]))
qs = q('{ __schema { queryType { fields { name args { name type { name kind ofType { name } } } } } } }')
print("QUERIES", json.dumps([(f["name"], [a["name"] for a in f["args"]]) for f in qs["data"]["__schema"]["queryType"]["fields"]]))
pid = sys.argv[1]
for query in ['query($id: PostId!) { post(input: {id: $id}) { id status externalLink } }',
              'query($id: PostId!) { post(input: {id: $id}) { id status } }']:
    print("TRY", json.dumps(q(query, {"id": pid}))[:800])
