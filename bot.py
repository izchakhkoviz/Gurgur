import os, json, time, re, requests
from google import genai
from google.genai import types

SYSTEM = "0772023014"
TOKEN = f"{SYSTEM}:{os.environ['YEMOT_PASSWORD']}"
API = "https://www.call2all.co.il/ym/api"
IN_EXT, OUT_EXT = "2", "1"
STATE_PATH = "ivr2:/9/state.json"   # המצב נשמר בימות, לא ב-GitHub
MODEL = "gemini-flash-latest"
MAX_AGE_H = 24

# >>> ערוך כאן את הכתבים: כינוי וסגנון <<<
REPORTERS = [
    {"name": "כתב א'", "style": "תמציתי, ענייני, משפטים קצרים."},
    {"name": "כתב ב'", "style": "חם ואישי, פונה למאזין בגוף שני."},
    {"name": "כתב ג'", "style": "רשמי, בנימה של מבזק חדשות."},
]
RULES = ("כתוב בעברית נקייה, בלשון המקובלת בציבור החסידי. "
         "אל תוסיף שום פרט שלא מופיע במקורות. אל תכלול שמות של אנשים פרטיים. "
         "פרט לא מאומת יסומן 'על פי דיווחים'.")

client = genai.Client(api_key=os.environ['GEMINI_API_KEY'])

def ask(system, user, search=False):
    cfg = types.GenerateContentConfig(system_instruction=system)
    if search:
        cfg.tools = [types.Tool(google_search=types.GoogleSearch())]
    r = client.models.generate_content(model=MODEL, contents=user, config=cfg)
    return r.text or ""

def jparse(t):
    t = re.sub(r"```(json)?", "", t).strip()
    return json.loads(t[t.find("{"):t.rfind("}") + 1])

# ---------- ימות ----------
def y_list(ext):
    r = requests.get(f"{API}/GetIVR2Dir", params={"token": TOKEN, "path": f"ivr2:/{ext}"}).json()
    return [f["name"] for f in r.get("files", []) if f["name"].lower().endswith(".wav")]

def y_download(path):
    return requests.get(f"{API}/DownloadFile", params={"token": TOKEN, "path": path}).content

def y_text_upload(path, text):
    requests.post(f"{API}/UploadTextFile", data={"token": TOKEN, "what": path, "contents": text})

def load_state():
    try:
        s = json.loads(y_download(STATE_PATH))
        if "pending" in s:
            return s
    except Exception:
        pass
    return {"seen": [], "pending": [], "held": [], "next": 0, "rr": 0}

def save_state(s):
    y_text_upload(STATE_PATH, json.dumps(s, ensure_ascii=False))

# ---------- שלבים ----------
def transcribe(raw):
    r = client.models.generate_content(model=MODEL, contents=[
        types.Part.from_bytes(data=raw, mime_type="audio/wav"),
        "תמלל במדויק את ההקלטה בעברית. החזר רק את הטקסט."])
    return r.text or ""

def triage(text):
    p = ('סנן הודעה של מאזין למערכת חדשות. החזר JSON בלבד: '
         '{"newsworthy":bool,"spam":bool,"sensitive":bool (פטירה/פציעה/פיגוע/אדם פרטי/לשון הרע),'
         '"topic":"","summary":"","facts":[]}\n\nהודעה:\n' + text)
    return jparse(ask("אתה עורך חדשות זהיר.", p))

def decide(pending):
    items = [{"id": i["id"], "topic": i["topic"], "summary": i["summary"],
              "facts": i["facts"], "age_min": int((time.time() - i["ts"]) / 60)} for i in pending]
    p = ('קבץ את ההודעות לפי אירוע. החזר JSON: {"groups":[{"ids":[],"ready":bool}]}. '
         'ready=true רק אם יש לפחות 2 הודעות על אותו אירוע, או הודעה אחת בת 15 דקות ומעלה. '
         'השאר ready=false.\n' + json.dumps(items, ensure_ascii=False))
    return jparse(ask("אתה עורך חדשות זהיר.", p))["groups"]

def write_story(group, reporter):
    src = json.dumps([{"summary": g["summary"], "facts": g["facts"]} for g in group], ensure_ascii=False)
    multi = len(group) > 1
    p = (f"{RULES}\nסגנון הכתב ({reporter['name']}): {reporter['style']}\n"
         f"מקורות מהמאזינים ({len(group)} הודעות):\n{src}\n\n"
         "חפש ברשת אימות והשלמות באתרי חדשות ומקורות רשמיים. "
         + ("יש כמה הודעות, אפשר לפרסם גם בלי אימות מהרשת. " if multi else
            "זו הודעה בודדת: publishable=true רק אם אומתה ברשת. ")
         + 'החזר JSON בלבד: {"publishable":bool,"text":"הידיעה, 3-5 משפטים, בפתיח: כאן '
         + reporter["name"] + '"}')
    return jparse(ask("אתה כתב במערכת חדשות טלפונית.", p, search=True))

# ---------- ריצה ----------
def main():
    s = load_state()
    now = time.time()

    for name in y_list(IN_EXT):
        if name in s["seen"]:
            continue
        s["seen"].append(name)
        try:
            t = transcribe(y_download(f"ivr2:/{IN_EXT}/{name}"))
            m = triage(t)
        except Exception as e:
            print("fail", name, e); continue
        if m["spam"] or not m["newsworthy"]:
            continue
        m.update(id=name, ts=now)
        (s["held"] if m["sensitive"] else s["pending"]).append(m)

    s["pending"] = [p for p in s["pending"] if now - p["ts"] < MAX_AGE_H * 3600]

    if s["pending"]:
        by_id = {p["id"]: p for p in s["pending"]}
        for g in decide(s["pending"]):
            if not g.get("ready"):
                continue
            group = [by_id[i] for i in g["ids"] if i in by_id]
            if not group:
                continue
            rep = REPORTERS[s["rr"] % len(REPORTERS)]
            res = write_story(group, rep)
            if not res.get("publishable"):
                continue
            n = s["next"]; s["next"] += 1; s["rr"] += 1
            y_text_upload(f"ivr2:/{OUT_EXT}/{n:03d}.tts", res["text"])
            s["pending"] = [p for p in s["pending"] if p["id"] not in g["ids"]]
            print("published", n)

    if s["held"]:
        print("ממתינות לאישור ידני:", len(s["held"]))
    save_state(s)

if __name__ == "__main__":
    main()
