import os
import json
import subprocess
import requests
from groq import Groq

YEMOT_USERNAME = os.getenv("PHONE_SYSTEM_DID")
YEMOT_PASSWORD = os.getenv("YEMOT_PASSWORD")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# מודל השפה לסינון ותיקון (אפשר להחליף דרך משתנה סביבה GROQ_MODEL)
GROQ_LLM_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

PROCESSED_FILES_LOG = "processed_files.json"

# רשת ביטחון: אם אחת המילים האלה מופיעה, ההודעה נפסלת מיד, בלי קשר ל-AI
BAD_KEYWORDS = [
    "חתן", "התארס", "התארוס", "איתארס", "נהיה חתן", "ניהיה חתן", "נעשה חתן",
    "אירוסין", "וורט", "פשנדווק", "פשנדווארט", "פשנדורט",
    "משה לייב פרידמן", "מוישי פרידמן", "משה לייב", "מוישי",
    "חדשות גור גרוע", "ביקורת על הקו", "נגד המערכת", "נגד הקו",
]

MIN_TEXT_LENGTH = 15

AI_SYSTEM_PROMPT = """אתה עורך ומסנן הודעות קוליות עבור קו חדשות של חסידות גור.
קיבלת תמלול אוטומטי (Whisper) של הודעה קולית. התמלול עלול להכיל טעויות שמיעה.

עליך לבצע שתי משימות:

1. החלטה: לאן ההודעה הולכת.
   ברירת המחדל היא אישור (approved=true): כל הודעה שיש לה קשר כלשהו לחסידות גור,
   גם קשר עקיף או חלקי (אדמו"רים, בתי מדרש, אנשי שלומנו, אירועי ציבור, שיעורים,
   תפילות ומניינים, עדכוני קהילה, ענייני חסידים וכו'), מאושרת לשידור.

   רק במקרים הבאים ההודעה לא מאושרת (approved=false) ועוברת לבדיקה ידנית של מנהל הקו:
   - תוכן נגד המערכת או הקו, נגד מפעיל הקו, או ביקורת עליהם
   - הודעות על חתנים, אירוסין, ווארט, פשנדווארט ודומיהם
   - תוכן נגד חסידות גור, נגד האדמו"ר או נגד אנשי שלומנו, או תוכן מזלזל ופוגעני
   - הודעה שאין לה שום קשר לחסידות גור, פרסומת, או הודעה ריקה / חסרת משמעות
   אם יש ספק אמיתי האם ההודעה פוגענית, נגד המערכת או שייכת לחתנים: approved=false.
   אבל אל תפסול רק בגלל שהקשר לגור חלש או עקיף, קשר כזה מספיק לאישור.

2. ניסוח: הפוך את התמלול לטקסט מדויק וברור שמתאים להקראה בקו.
   - תקן מילים שהתמלול טעה בהן לפי ההקשר (שמות, מונחי חסידות ותפילות,
     מילים שנשמעות דומה). חשוב על מה המדבר התכוון, ובחר את המילה שמתאימה להקשר.
   - אפשר לתקן דקדוק, להשלים מילת קישור חסרה ולנקות גמגומים, חזרות ומילות מילוי,
     כדי שהמשפט יהיה תקין וזורם.
   - אסור להוסיף מידע שלא נאמר, להשמיט עובדות, לשנות שמות, מספרים, שעות
     ותאריכים, או לשנות את המשמעות.
   - אם מילה לא ברורה ואי אפשר להבין מההקשר מה הייתה, אל תנחש:
     השאר אותה כפי שהיא ורשום אותה ב-unclear_words.

החזר אך ורק JSON במבנה הבא:
{
  "approved": true או false,
  "reason": "סיבה קצרה בעברית",
  "corrected_text": "הטקסט אחרי תיקון וליטוש",
  "corrections": ["מילה מקורית -> מילה מתוקנת"],
  "unclear_words": ["מילים שלא היה ברור מה הן"]
}"""


def get_yemot_token():
    url = "https://call2all.co.il/ym/api/Login"
    params = {"username": YEMOT_USERNAME, "password": YEMOT_PASSWORD}
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        if data.get("responseStatus") == "OK":
            return data.get("token")
        else:
            print(f"שגיאה בהתחברות לימות המשיח: {data}")
    except Exception as e:
        print(f"שגיאת תקשורת בהתחברות: {e}")
    return None


def load_processed_files():
    if os.path.exists(PROCESSED_FILES_LOG):
        try:
            with open(PROCESSED_FILES_LOG, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception:
            return []
    return []


def save_and_commit_processed_file(file_name):
    processed = load_processed_files()
    if file_name not in processed:
        processed.append(file_name)
        with open(PROCESSED_FILES_LOG, "w", encoding="utf-8") as f:
            json.dump(processed, f, ensure_ascii=False, indent=4)
        print(f"הקובץ {file_name} נוסף ללוג המעובדים ונשמר.")

        try:
            subprocess.run(["git", "config", "--global", "user.name", "github-actions[bot]"], check=False)
            subprocess.run(["git", "config", "--global", "user.email", "github-actions[bot]@users.noreply.github.com"], check=False)
            subprocess.run(["git", "add", PROCESSED_FILES_LOG], check=False)
            subprocess.run(["git", "commit", "-m", f"Automated update processed files: {file_name}"], check=False)

            github_token = os.getenv("GITHUB_TOKEN")
            repo = os.getenv("GITHUB_REPOSITORY")
            if github_token and repo:
                remote_url = f"https://x-access-token:{github_token}@github.com/{repo}.git"
                result = subprocess.run(["git", "push", remote_url, "HEAD:main"], capture_output=True, text=True)
                if result.returncode == 0:
                    print("לוג הקבצים המעובדים גובה ב-GitHub בהצלחה!")
                else:
                    print(f"שגיאה בביצוע push: {result.stderr}")
            else:
                subprocess.run(["git", "push"], check=False)
        except Exception as e:
            print(f"שגיאה בגיבוי לוג הקבצים ב-git: {e}")


def get_ivr2_files(token, folder_path):
    url = "https://call2all.co.il/ym/api/GetIVR2Dir"
    clean_path = str(folder_path).strip("/")
    params = {"token": token, "path": clean_path}
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"שגיאה בתקשורת מול ימות המשיח בשלוחה {folder_path}: {e}")
        return None


def download_file_from_yemot(token, file_name, dest_path="temp_audio.wav"):
    url = "https://call2all.co.il/ym/api/DownloadFile"
    paths_to_try = [
        f"ivr2:2/{file_name}",
        f"ivr2:2/{file_name}.wav" if "." not in file_name else None,
        f"2/{file_name}",
        f"2/{file_name}.wav" if "." not in file_name else None,
    ]
    for path in paths_to_try:
        if not path:
            continue
        params = {"token": token, "path": path}
        try:
            response = requests.get(url, params=params)
            if response.status_code == 200 and len(response.content) > 100:
                with open(dest_path, "wb") as f:
                    f.write(response.content)
                return dest_path
        except Exception:
            pass
    return None


def get_next_available_filename(token, target_folder, extension="tts"):
    data = get_ivr2_files(token, target_folder)
    max_num = 0
    if data and data.get("responseStatus") == "OK":
        files = data.get("files", []) or data.get("data", [])
        for f in files:
            name = f.get("name") if isinstance(f, dict) else str(f)
            base_name = name.split(".")[0]
            if base_name.isdigit():
                num = int(base_name)
                if num > max_num:
                    max_num = num
    return f"{max_num + 1:03d}.{extension}"


def upload_tts_to_yemot(token, text_content, target_folder):
    url = "https://call2all.co.il/ym/api/UploadFile"
    clean_folder = str(target_folder).strip("/")
    target_file_name = get_next_available_filename(token, clean_folder, extension="tts")
    temp_tts_file = f"temp_{target_file_name}"

    with open(temp_tts_file, "w", encoding="utf-8") as f:
        f.write(text_content)

    paths_to_try = [f"ivr2:{clean_folder}/{target_file_name}", f"{clean_folder}/{target_file_name}"]
    success = False
    for upload_path in paths_to_try:
        params = {"token": token, "path": upload_path}
        try:
            with open(temp_tts_file, "rb") as f:
                response = requests.post(url, params=params, files={"file": (target_file_name, f)})
                if response.json().get("responseStatus") == "OK":
                    success = True
                    break
        except Exception:
            pass
    if os.path.exists(temp_tts_file):
        os.remove(temp_tts_file)
    return success


def transcribe_audio_with_groq(audio_path):
    """מחזיר טקסט (יכול להיות מחרוזת ריקה אם אין דיבור), או None אם התמלול נכשל."""
    if not GROQ_API_KEY:
        print("חסר GROQ_API_KEY")
        return None
    client = Groq(api_key=GROQ_API_KEY)
    try:
        with open(audio_path, "rb") as file:
            transcription = client.audio.transcriptions.create(
                file=(audio_path, file.read()),
                model="whisper-large-v3",
                language="he",
            )
        return transcription.text
    except Exception as e:
        print(f"שגיאה בתמלול: {e}")
        return None


def find_bad_keyword(text):
    for bad in BAD_KEYWORDS:
        if bad in text:
            return bad
    return None


def ai_review_text(text):
    """שולח את התמלול למודל שפה. מחזיר dict, או None אם הקריאה/הפענוח נכשלו."""
    client = Groq(api_key=GROQ_API_KEY)
    try:
        completion = client.chat.completions.create(
            model=GROQ_LLM_MODEL,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": AI_SYSTEM_PROMPT},
                {"role": "user", "content": f"התמלול:\n{text}"},
            ],
        )
        result = json.loads(completion.choices[0].message.content)
        if not isinstance(result, dict) or not isinstance(result.get("approved"), bool):
            print(f"תשובת AI במבנה לא תקין: {result}")
            return None
        return result
    except Exception as e:
        print(f"שגיאה בניתוח AI: {e}")
        return None


def is_correction_safe(original, corrected):
    """הגנה מפני 'הזיות': התיקון לא יכול לשנות את אורך הטקסט בצורה חריגה."""
    if not corrected or not corrected.strip():
        return False
    ratio = len(corrected.strip()) / max(len(original.strip()), 1)
    return 0.5 <= ratio <= 1.5


def analyze_and_process_text(text):
    """
    מחזיר {"approved": bool, "processed_text": str}
    כל מקרה של ספק או כשל מוביל ל-approved=False (בדיקה ידנית בשלוחה 0/1).
    """
    if not text or len(text.strip()) < MIN_TEXT_LENGTH:
        print("הודעה קצרה מדי או לא ברורה - מועברת לשלוחה 0/1")
        return {"approved": False, "processed_text": text}

    # שלב 1: רשת ביטחון של מילות פסילה (על התמלול הגולמי)
    bad = find_bad_keyword(text)
    if bad:
        print(f"זוהתה מילת פסילה ('{bad}') - מעביר מיד לשלוחה 0/1")
        return {"approved": False, "processed_text": text}

    # שלב 2: סינון ותיקון על ידי AI
    review = ai_review_text(text)
    if review is None:
        print("ה-AI לא החזיר תשובה תקינה - מועבר לשלוחה 0/1 לבדיקה ידנית")
        return {"approved": False, "processed_text": text}

    print(f"החלטת AI: approved={review['approved']} | סיבה: {review.get('reason', '')}")

    final_text = text
    corrected = review.get("corrected_text")
    if isinstance(corrected, str) and is_correction_safe(text, corrected):
        final_text = corrected.strip()
        if review.get("corrections"):
            print(f"תיקונים שבוצעו: {review['corrections']}")
    else:
        print("תיקון ה-AI נדחה (ריק או חריג באורכו), נשמר הטקסט המקורי")

    if review.get("unclear_words"):
        print(f"מילים לא ברורות שלא תוקנו: {review['unclear_words']}")

    approved = review["approved"]

    # שלב 3: בדיקה חוזרת של מילות פסילה על הטקסט המתוקן
    if approved:
        bad_after = find_bad_keyword(final_text)
        if bad_after:
            print(f"מילת פסילה ('{bad_after}') בטקסט המתוקן - מועבר לשלוחה 0/1")
            approved = False

    return {"approved": approved, "processed_text": final_text}


def trigger_tzintuk(token):
    url = "https://call2all.co.il/ym/api/RunCampaign"
    params = {"token": token, "varName": "200"}
    try:
        response = requests.get(url, params=params)
        data = response.json()
        if data.get("responseStatus") == "OK":
            print("קמפיין הצינטוקים הופעל בהצלחה דרך שלוחה 200.")
        else:
            alt_url = "https://call2all.co.il/ym/api/GetIVR2Dir"
            requests.get(alt_url, params={"token": token, "path": "200"})
            print("שלוחה 200 הופעלה ליזום את הצינטוק.")
    except Exception as e:
        print(f"שגיאה בהפעלת הצינטוק: {e}")


def process_file(token, file_name):
    """
    מטפל בהודעה אחת.
    מחזיר (done, approved):
      done=True  -> הטיפול הסתיים ואפשר לסמן את הקובץ כמעובד
      done=False -> משהו נכשל (הורדה/תמלול/העלאה), הקובץ ינוסה שוב בריצה הבאה
    """
    local_audio = download_file_from_yemot(token, file_name)
    if not local_audio:
        print(f"הורדת {file_name} נכשלה - לא מסומן כמעובד, ינוסה שוב.")
        return False, False

    try:
        raw_text = transcribe_audio_with_groq(local_audio)
    finally:
        if os.path.exists(local_audio):
            os.remove(local_audio)

    if raw_text is None:
        print(f"תמלול {file_name} נכשל - לא מסומן כמעובד, ינוסה שוב.")
        return False, False

    if not raw_text.strip():
        print(f"{file_name}: לא זוהה דיבור בהקלטה, מדלג.")
        return True, False

    print(f"תמלול גולמי: {raw_text}")

    analysis = analyze_and_process_text(raw_text)
    is_approved = analysis["approved"]
    final_text = analysis["processed_text"]

    target_folder = "1" if is_approved else "0/1"
    print(f"יעד העלאה נבחר: שלוחה {target_folder}")

    if not upload_tts_to_yemot(token, final_text, target_folder):
        print(f"העלאת {file_name} נכשלה - לא מסומן כמעובד, ינוסה שוב.")
        return False, False

    return True, is_approved


def main():
    token = get_yemot_token()
    if not token:
        return

    processed_files = load_processed_files()
    print(f"קבצים שכבר עובדו בעבר ({len(processed_files)} קבצים)")

    data = get_ivr2_files(token, "2")
    if not data or data.get("responseStatus") != "OK":
        return

    files = data.get("files", []) or data.get("data", [])
    approved_to_folder_1_count = 0

    for file in files:
        file_name = file.get("name") if isinstance(file, dict) else str(file)
        if not file_name or file_name.endswith(".ini") or file_name.endswith(".ymgr"):
            continue
        if not (file_name.endswith(".wav") or file_name.endswith(".mp3") or file_name.isdigit() or file_name.startswith("wav.")):
            continue

        if file_name in processed_files:
            print(f"הקובץ {file_name} כבר עובד בעבר, מדלג עליו.")
            continue

        print(f"\n--- מטפל בהודעה קולית חדשה: {file_name} ---")

        done, approved = process_file(token, file_name)
        if done:
            save_and_commit_processed_file(file_name)
            if approved:
                approved_to_folder_1_count += 1

    if approved_to_folder_1_count > 0:
        print("הודעה אושרה לשלוחה 1, מפעיל צינטוק בשלוחה 200...")
        trigger_tzintuk(token)
    else:
        print("לא הועברו הודעות חדשות לשלוחה 1 בריצה זו, הצינטוק לא הופעל.")


if __name__ == "__main__":
    main()
