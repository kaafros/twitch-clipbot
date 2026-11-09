"""Cross-post finished Shorts to TikTok through Buffer's free API (Buffer publishes via TikTok's approved API).

Needs secret BUFFER_API_KEY (publish.buffer.com/settings/api) and a TikTok channel connected in Buffer.
Videos must be at a public URL, so each file is attached to a GitHub release called "media" first.
"""
import os
import time

import requests

API = "https://api.buffer.com"


def _gql(query, variables=None):
    r = requests.post(API, timeout=60, json={"query": query, "variables": variables or {}},
                      headers={"Authorization": f"Bearer {os.environ['BUFFER_API_KEY']}"})
    r.raise_for_status()
    j = r.json()
    if j.get("errors"):
        raise RuntimeError(j["errors"])
    return j["data"]


def tiktok_channel_id():
    orgs = _gql("query { account { organizations { id } } }")["account"]["organizations"]
    for org in orgs:
        chans = _gql("query($o: OrganizationId!) { channels(input: {organizationId: $o}) { id service } }",
                     {"o": org["id"]})["channels"]
        for c in chans:
            if c["service"].lower() == "tiktok":
                return c["id"]
    raise RuntimeError("No TikTok channel connected in Buffer")


def _gh(method, url, **kw):
    h = {"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}", "Accept": "application/vnd.github+json"}
    h.update(kw.pop("headers", {}))
    return requests.request(method, url, headers=h, timeout=300, **kw)


def host_video(path, name):
    """Upload the mp4 to the 'media' release and return its public download URL."""
    repo = os.environ["GITHUB_REPOSITORY"]
    r = _gh("GET", f"https://api.github.com/repos/{repo}/releases/tags/media")
    if r.status_code == 404:
        r = _gh("POST", f"https://api.github.com/repos/{repo}/releases",
                json={"tag_name": "media", "name": "media", "body": "Temporary video hosting for TikTok posts"})
    rel = r.json()
    for a in rel.get("assets", []):
        if a["name"] == name:  # already hosted (e.g. a retry)
            return a["browser_download_url"]
    # clean up assets older than 3 days
    for a in rel.get("assets", []):
        if time.time() - time.mktime(time.strptime(a["created_at"], "%Y-%m-%dT%H:%M:%SZ")) > 3 * 86400:
            _gh("DELETE", f"https://api.github.com/repos/{repo}/releases/assets/{a['id']}")
    with open(path, "rb") as f:
        up = _gh("POST", f"https://uploads.github.com/repos/{repo}/releases/{rel['id']}/assets?name={name}",
                 headers={"Content-Type": "video/mp4"}, data=f)
    up.raise_for_status()
    return up.json()["browser_download_url"]


CREATE = """mutation($input: CreatePostInput!) { createPost(input: $input) {
  ... on PostActionSuccess { post { id } }
  ... on MutationError { message } } }"""


def post(path, caption, name):
    if not os.environ.get("BUFFER_API_KEY"):
        return None
    url = host_video(path, name)
    base = {"text": caption[:2200], "channelId": tiktok_channel_id(), "schedulingType": "automatic",
            "mode": "shareNow"}
    last = None
    for assets in ({"videos": [{"url": url}]}, [{"video": {"url": url}}]):  # both documented shapes
        try:
            res = _gql(CREATE, {"input": {**base, "assets": assets}})["createPost"]
            if res.get("post"):
                return res["post"]["id"]
            last = res.get("message")
        except Exception as e:
            last = e
    raise RuntimeError(f"Buffer createPost failed: {last}")


def wait_for_link(post_id, timeout=300):
    """Poll Buffer until the TikTok post is live and return its URL (or None)."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            p = _gql("query($id: PostId!) { post(input: {id: $id}) { status externalLink error { message } } }",
                     {"id": post_id})["post"]
        except Exception:
            p = _gql("query($id: PostId!) { post(input: {id: $id}) { status externalLink } }", {"id": post_id})["post"]
        if p.get("externalLink"):
            return p["externalLink"]
        if p.get("status") == "error":
            print(f"TikTok post error: {p.get('error')}")
            return None
        time.sleep(15)
    return None
