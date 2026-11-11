"""Channel management (needs the YouTube 'manage' permission):
  python tools/manage.py stats            -> views/likes of every Short on the channel
  python tools/manage.py delete ID [ID..] -> delete videos by YouTube id"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import clipbot

yt = clipbot.youtube()
if sys.argv[1] == "delete":
    for vid in sys.argv[2:]:
        yt.videos().delete(id=vid).execute()
        print("DELETED", vid)
else:
    ch = yt.channels().list(part="contentDetails,statistics", mine=True).execute()["items"][0]
    print("CHANNEL", ch["statistics"])
    uploads = ch["contentDetails"]["relatedPlaylists"]["uploads"]
    ids, tok = [], None
    while True:
        r = yt.playlistItems().list(part="contentDetails", playlistId=uploads, maxResults=50, pageToken=tok).execute()
        ids += [i["contentDetails"]["videoId"] for i in r["items"]]
        tok = r.get("nextPageToken")
        if not tok:
            break
    for i in range(0, len(ids), 50):
        for v in yt.videos().list(part="snippet,statistics", id=",".join(ids[i:i + 50])).execute()["items"]:
            st = v["statistics"]
            print(f"STATS {v['id']} | views {st.get('viewCount')} | likes {st.get('likeCount', 0)} | {v['snippet']['publishedAt'][:16]} | {v['snippet']['title']}")
