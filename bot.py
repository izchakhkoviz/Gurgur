import os
import json
import requests
from groq import Groq

YEMOT_USERNAME = os.getenv("PHONE_SYSTEM_DID")
YEMOT_PASSWORD = os.getenv("YEMOT_PASSWORD")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

PROCESSED_FILES_LOG = "processed_files.json"


def get_yemot_token():
    url = "https://call2all.co.il/ym/api/Login"
    params = {
        "username": YEMOT_USERNAME,
        "password": YEMOT_PASSWORD
    }
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


def save_processed_file(file_name):
    processed = load_processed_files()
    if file_name not in processed:
        processed.append(file_name)
        with open(PROCESSED_FILES_LOG, "w", encoding="utf-8") as f:
            json.dump(processed, f, ensure_ascii=False, indent=4)


def get_ivr2_files(token, folder_path):
    url = "https://call2all.co.il/ym/api/GetIVR2Dir"
    clean_path = str(folder_path).strip("/")
    params = {
        "token": token,
        "path": clean_path
    }
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
        f"2/{file_name}.wav" if "." not in file_name else None
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

    try:
        with open(temp_tts_file, "w", encoding="utf-8") as f:
            f.write(text_content)

        paths_to_try = [
            f"ivr2:{clean_folder}/{target_file_name}",
            f"{clean_folder}/{target_file_name}"
        ]
        for upload_path in paths_to_try:
            params = {"token": token, "path": upload_path}
            try:
                with open(temp_tts_file, "rb") as f:
                    response = requests.post(
                        url,
                        params=params,
                        files={"file": (target_file_name, f)}
                    )
                if response.ok and response.json().get("responseStatus") == "OK":
                    print(f"הקובץ הועלה בהצלחה: {upload_path}")
                    return True
            except Exception as e:
                print(f"ניסיון העלאה נכשל ({upload_path}): {e}")
        return False
    finally:
        if os.path.exists(temp_tts_file):
            os.remove(temp_tts_file)


def transcribe_audio_with_groq(audio_path):
    if not GROQ_API_KEY:
        print("חסר GROQ_API_KEY בסביבה.")
        return None
    client = Groq(api_key=GROQ_API_KEY)
    try:
        with open(audio_path, "rb") as file:
            transcription = client.audio.transcriptions.create(
                file=(os.path.basename(audio_path), file.read()),
                model="whisper-large-v3",
                language="he"
            )
        return transcription.text
    except Exception as e:
        print(f"שגיאה בתמלול: {e}")
        return None


def analyze_and_process_text(text):
    if not text:
        return {"approved": False, "processed_text": text}

    # בדיקת מילות פסילה (כולל פשנדווק וביקורת שלילית)
    bad_keywords = [
        "פשנדווק", "משה לייב פרידמן", "מוישי פרידמן",
        "חדשות גור גרוע", "ביקורת על הקו"
    ]
    normalized_text = text.casefold()
    for bad in bad_keywords:
        if bad.casefold() in normalized_text:
            print("מצא מילת פסילה או פשנדווק - נדחה לשלוחה 0/1")
            return {"approved": False, "processed_text": text}

    # בדיקת מילות אישור (חסידות גור, תפילות, אדמו"ר וכו' - ללא פשנדווק)
    gour_keywords = [
        "גור", "רבי", "אדמו", "שחרית", "מנחה", "מעריב",
        "שטרנבוך", "שטיבל", "טיש", 'ענ"ש',
        "אנשי שלומנו", "בית המדרש", "מניין"
    ]

    is_approved = any(keyword.casefold() in normalized_text for keyword in gour_keywords)
    print(f"תוצאת ניתוח תוכן (האם קשור לגור/תפילות?): {is_approved}")

    return {
        "approved": is_approved,
        "processed_text": text
    }


def trigger_tzintuk(token):
    """ניסיון להפעיל קמפיין צינטוקים; יש לוודא את פרטי ה-API מול ימות המשיח."""
    url = "https://call2all.co.il/ym/api/RunCampaign"
    params = {
        "token": token,
        "varName": "200"
    }
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        if data.get("responseStatus") == "OK":
            print("קמפיין הצינטוקים הופעל בהצלחה.")
            return True
        print(f"הפעלת קמפיין הצינטוקים נכשלה או לא אושרה: {data}")
    except Exception as e:
        print(f"שגיאה בהפעלת הצינטוק: {e}")
    return False


def main():
    token = get_yemot_token()
    if not token:
        return

    processed_files = load_processed_files()
    print(f"קבצים שכבר עובדו בעבר ({len(processed_files)} קבצים): {processed_files}")

    data = get_ivr2_files(token, "2")
    if not data or data.get("responseStatus") != "OK":
        return

    files = data.get("files", []) or data.get("data", [])
    new_files_processed_count = 0

    for file in files:
        file_name = file.get("name") if isinstance(file, dict) else str(file)
        if not file_name or file_name.endswith(".ini") or file_name.endswith(".ymgr"):
            continue
        if not (
            file_name.endswith(".wav")
            or file_name.endswith(".mp3")
            or file_name.isdigit()
            or file_name.startswith("wav.")
        ):
            continue
        if file_name in processed_files:
            continue

        print(f"\n--- מטפל בהודעה קולית חדשה: {file_name} ---")
        local_audio = download_file_from_yemot(token, file_name)
        if not local_audio:
            print(f"לא הצלחתי להוריד את הקובץ {file_name}; הוא לא סומן כמעובד.")
            continue

        try:
            raw_text = transcribe_audio_with_groq(local_audio)
            if not raw_text:
                print(f"לא התקבל תמלול עבור {file_name}; הוא לא סומן כמעובד.")
                continue

            print(f"תמלול גולמי: {raw_text}")
            analysis = analyze_and_process_text(raw_text)
            is_approved = analysis.get("approved", False)
            final_text = analysis.get("processed_text") or raw_text

            target_folder = "1" if is_approved else "0/1"
            print(f"יעד העלאה נבחר: שלוחה {target_folder}")

            if upload_tts_to_yemot(token, final_text, target_folder):
                save_processed_file(file_name)
                processed_files.append(file_name)
                new_files_processed_count += 1
            else:
                print(f"העלאת הטקסט נכשלה עבור {file_name}; הקובץ לא סומן כמעובד.")
        finally:
            if os.path.exists(local_audio):
                os.remove(local_audio)

    # מפעילים צינטוק רק אם לפחות הודעה אחת הועלתה בהצלחה.
    if new_files_processed_count > 0:
        print("הודעות חדשות הועלו בהצלחה, מנסה להפעיל צינטוק...")
        trigger_tzintuk(token)
    else:
        print("לא נמצאו הודעות חדשות שעובדו והועלו בהצלחה בריצה זו.")


if __name__ == "__main__":
    main()
