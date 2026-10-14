import os, json, time, re, requests
from groq import Groq

SYSTEM = "0772023014"
TOKEN = f"{SYSTEM}:{os.environ['YEMOT_PASSWORD']}"
API = "https://www.call2all.co.il/ym/api"
IN_EXT, OUT_EXT = "2", "1"
STATE_PATH = "ivr2:/9/state.json"   # המצב נשמר בימות, לא ב-GitHub
STT_MODEL = "whisper-large-v3-turbo"
STATE_VERSION = 6
# הודעה שמזכירה אחד מהשמות האלה לא תעלה. אפשר להוסיף כאן כתיבים נוספים
BLOCKED_NAMES = ["מוישי", "משה לייב פרידמן", "משה ליב פרידמן", "משה לייב פריידמן", "משה ליב פריידמן"]
HOLD_SENSITIVE = True   # True = דיווחי פטירה/פציעה/פיגוע לא עולים אוטומטית. False = גם הם עולים
MAX_AGE_H = 24

# סגנונות כתיבה גמישים ואנושיים ללא פתיחים מאולצים קבועים
REPORTERS = [
    {"name": "עדכון מערכת", "style": "תמציתי, ענייני, משפטים קצרים וברורים."},
    {"name": "הודעת מערכת", "style": "חם ואישי, פונה לציבור המאזינים בצורה מכובדת."},
    {"name": "מבזק חסידי", "style": "רשמי, בנימה של עדכון חצר מכובד וראוי."},
]
RULES = ("כתוב בעברית נקייה, בלשון המקובלת והמכובדת בציבור החסידי (חסידות גור). "
         'הקפד על כבוד הראוי לאדמו"ר שליט"א, לרבנים ולחצר החסידית בכל פנייה וניסוח. '
         "אל תוסיף שום פרט שלא מופיע במקורות, ואל תוסיף ביקורת או שבח שלא נאמרו. "
         "שמות אנשים מהמקורות נשארים כפי שנאמרו. "
         "התאם את פתיחת הדיווח והניסוח אך ורק לתוכן ההודעה בפועל (לדוגמה: לבקשת המאזינים, הודעת מערכת, או עדכון מהשטח) "
         "בצורה טבעית, אנושית וזורמת לחלוטין, ללא מילים או משפטי תבנית קבועים שלא קשורים לתוכן.")

client = Groq(api_key=os.environ["GROQ_API_KEY"])

PREFERRED = ["openai/gpt-oss-120b", "openai/gpt-oss-20b",
             "llama-3.3-70b-versatile", "llama-3.1-8b-instant"]

IDS = set()

def pick_model():
    global IDS
    try:
        ids = {m.id for m in client.models.list().data}
        IDS = ids
    except Exception as e:
        print("models list fail", e)
        return PREFERRED[0]
    for m in PREFERRED:
        if m in ids:
            return m
    print("אף מודל מהרשימה לא זמין. מודלים זמינים:", sorted(ids))
    return PREFERRED[0]

LLM_MODEL = pick_model()
print("LLM model:", LLM_MODEL)
SEARCH_MODEL = next((m for m in ["groq/compound", "groq/compound-mini", "compound-beta"] if m in IDS), None)
print("SEARCH model:", SEARCH_MODEL)

def ask(system, user, search=False, as_json=False):
    kw = {}
    r = client.chat.completions.create(
        model=SEARCH_MODEL if (search and SEARCH_MODEL) else LLM_MODEL,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}], **kw)
    return r.choices[0].message.content or ""

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
        if "pending" in s and s.get("v") == STATE_VERSION:
            if s.get("next", 0) < 242:
                s["next"] = 242
            return s
    except Exception:
        pass
    return {"v": STATE_VERSION, "seen": [], "pending": [], "held": [], "next": 242, "rr": 0, "last_decide": 0}

def save_state(s):
    y_text_upload(STATE_PATH, json.dumps(s, ensure_ascii=False))

# ---------- שלבים ----------
def transcribe(raw):
    r = client.audio.transcriptions.create(file=("a.wav", raw), model=STT_MODEL, language="he")
    return getattr(r, "text", r)

def triage(text):
    p = ('סנן הודעה של מאזין למערכת חדשות חסידית. כל הודעה עם תוכן מתפרסמת: '
         'ידיעות, סקופים, שמועות, עדכונים על חצרות, אישים ואירועים. החזר JSON בלבד: '
         '{"empty":bool (שקט, רעש או לא ברור בכלל),'
         '"against_system":bool (פגיעה, ביקורת, קללה או לשון הרע נגד מנהל המערכת, המערכת או הכתבים שלה),'
         '"sensitive":bool (דיווח על פטירה, פציעה קשה או פיגוע בלבד),'
         '"topic":"","summary":"","facts":[]}\n\nהודעה:\n' + text)
    return jparse(ask("אתה עורך חדשות.", p, as_json=True))

def norm(t):
    return " ".join(re.sub(r"[^\u05d0-\u05ea ]", " ", t).split())

def analyze_audio(raw):
    text = transcribe(raw)
    t = norm(text)
    if any(norm(n) in t for n in BLOCKED_NAMES):
        return {"blocked_name": True}
    if len(t) < 6:
        return {"empty": True}
    m = triage(text)
    m["empty"] = False
    return m

def is_quota(e):
    return "429" in str(e) or "rate_limit" in str(e).lower()

def decide(pending):
    items = [{"id": i["id"], "topic": i["topic"], "summary": i["summary"],
              "facts": i["facts"], "age_min": int((time.time() - i["ts"]) / 60)} for i in pending]
    p = ('קבץ את ההודעות לפי אירוע. החזר JSON: {"groups":[{"ids":[],"ready":bool}]}. '
         'ready=true לכל קבוצה, גם אם יש בה הודעה אחת בלבד.\n' + json.dumps(items, ensure_ascii=False))
    return jparse(ask("אתה עורך חדשות זהיר.", p, as_json=True))["groups"]

def write_story(group, reporter):
    src = json.dumps([{"summary": g["summary"], "facts": g["facts"]} for g in group], ensure_ascii=False)
    multi = len(group) > 1
    p = (f"{RULES}\nסגנון הדיווח: {reporter['style']}\n"
         f"מקורות מהמאזינים ({len(group)} הודעות):\n{src}\n\n"
         "נסח את הידיעה בצורה אנושית, טבעית ומכובדת, מותאמת בדיוק לתוכן ההודעה (למשל: פניית מאזין, הודעת מערכת, או עדכון אירוע). "
         "הימנע מתוספות מיותרות או ממשפטי פתיחה רובוטיים. "
         '\u05d4\u05d7\u05d6\u05e8 JSON בלבד: {"publishable":true,"text":"טקסט הדיווח המלא, 3-5 משפטים זורמים ומכובדים"}')
    return jparse(ask("אתה עורך בכיר במערכת חדשות טלפונית.", p, search=True))

# ---------- ריצה ----------
def main():
    s = load_state()
    now = time.time()
    quota_hit = False
    new = 0

    for name in y_list(IN_EXT):
        if name in s["seen"]:
            continue
        try:
            m = analyze_audio(y_download(f"ivr2:/{IN_EXT}/{name}"))
        except Exception as e:
            print("fail", name, e)
            if is_quota(e):
                quota_hit = True
                break
            s.setdefault("fails", {})
            s["fails"][name] = s["fails"].get(name, 0) + 1
            if s["fails"][name] >= 3:
                s["seen"].append(name)
            continue
        s["seen"].append(name)
        new += 1
        if m.get("blocked_name"):
            print("msg", name, "שם-חסום-נחסם")
            continue
        hold = bool(m.get("sensitive")) and HOLD_SENSITIVE
        print("msg", name,
              "ריק" if m.get("empty") else "",
              "נגד-המערכת-נחסם" if m.get("against_system") else "",
              "רגיש-ממתין-לאישור" if hold else "")
        m.pop("transcript", None)
        if m.get("empty") or m.get("against_system"):
            continue
        m.update(id=name, ts=now)
        (s["held"] if hold else s["pending"]).append(m)

    s["pending"] = [p for p in s["pending"] if now - p["ts"] < MAX_AGE_H * 3600]

    if s["pending"] and not quota_hit and (new or now - s.get("last_decide", 0) > 1200):
        s["last_decide"] = now
        try:
            groups = decide(s["pending"])
        except Exception as e:
            print("decide fail", e); groups = []
            quota_hit = is_quota(e)
        by_id = {p["id"]: p for p in s["pending"]}
        for g in groups:
            if quota_hit or not g.get("ready"):
                continue
            group = [by_id[i] for i in g["ids"] if i in by_id]
            if not group:
                continue
            rep = REPORTERS[s["rr"] % len(REPORTERS)]
            try:
                res = write_story(group, rep)
            except Exception as e:
                print("write fail", e)
                if is_quota(e):
                    quota_hit = True
                continue
            if not res.get("publishable"):
                for p in group:
                    p["tries"] = p.get("tries", 0) + 1
                continue
            n = s["next"]; s["next"] += 1; s["rr"] += 1
            y_text_upload(f"ivr2:/{OUT_EXT}/{n:03d}.tts", res["text"])
            s["pending"] = [p for p in s["pending"] if p["id"] not in g["ids"]]
            print("published", f"{n:03d}.tts")
        s["pending"] = [p for p in s["pending"] if p.get("tries", 0) < 3]

    if quota_hit:
        print("QUOTA: המכסה החינמית נגמרה, ננסה שוב בהרצה הבאה")
    if s["held"]:
        print("ממתינות לאישור ידני:", len(s["held"]))
    save_state(s)

if __name__ == "__main__":
    main()
