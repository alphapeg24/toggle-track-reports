"""
Toggl → (CSV) → Google Drive に「Googleスプレッドシート」として保存するスクリプト。

- 最新版：toggl_time_entries_latest（毎回上書き）
- 日付版：toggl_time_entries_YYYY-MM-DD（任意で毎回作成）

必要な環境変数（GitHub Secrets推奨）
- TOGGL_API_TOKEN        : Toggl API token
- TOGGL_WORKSPACE_ID     : Toggl workspace id
- DRIVE_FOLDER_ID        : （任意）保存先フォルダID（マイドライブ内フォルダ推奨）
- START_DATE             : （任意）YYYY-MM-DD
- END_DATE               : （任意）YYYY-MM-DD
- DAYS                   : （任意）START/END未指定の場合の過去日数（例：90）
- WRITE_DAILY_COPY       : （任意）"true"で日付版も作成（デフォルトtrue）

認証（重要）
- GitHub Actions で google-github-actions/auth@v2 (WIF) を使い、
  create_credentials_file: true / export_environment_variables: true を有効にしておくこと。
- 本スクリプトは google.auth.default() (ADC) で認証情報を取得します。
"""

import os
import json
import datetime as dt
from typing import Optional

import requests
import google.auth
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload

GOOGLE_SHEETS_MIME = "application/vnd.google-apps.spreadsheet"


# ----------------------------
# Helpers
# ----------------------------
def require_env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"Missing required env var: {name}")
    return v


def env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")


def iso_date(d: dt.date) -> str:
    return d.strftime("%Y-%m-%d")


def resolve_date_range() -> tuple[str, str]:
    start = os.environ.get("START_DATE")
    end = os.environ.get("END_DATE")
    if start and end:
        return start, end

    days = int(os.environ.get("DAYS", "90"))
    end_d = dt.date.today()
    start_d = end_d - dt.timedelta(days=days)
    return iso_date(start_d), iso_date(end_d)


# ----------------------------
# Toggl (Reports API v3) fetch
# ----------------------------
def fetch_toggl_csv(
    workspace_id: str,
    toggl_api_token: str,
    start_date: str,
    end_date: str,
) -> bytes:
    """
    Toggl Reports API v3: time entries CSV
    POST /reports/api/v3/workspace/{workspace_id}/search/time_entries.csv

    GETだと 405 Method Not Allowed になりやすいので POST + JSON body で送ります。
    """
    url = f"https://api.track.toggl.com/reports/api/v3/workspace/{workspace_id}/search/time_entries.csv"
    headers = {"Accept": "text/csv", "Content-Type": "application/json"}
    payload = {"start_date": start_date, "end_date": end_date}

    r = requests.post(
        url,
        headers=headers,
        json=payload,
        auth=(toggl_api_token, "api_token"),
        timeout=90,
    )
    r.raise_for_status()
    return r.content


# ----------------------------
# Google Drive (ADC via WIF) service
# ----------------------------
def get_drive_service_from_adc():
    """
    GitHub Actions の WIF (google-github-actions/auth) が作った ADC を使って Drive client を作る。
    """
    scopes = [
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/spreadsheets",
    ]
    creds, _ = google.auth.default(scopes=scopes)
    return build("drive", "v3", credentials=creds)


# ----------------------------
# Drive: Upsert CSV as Google Sheet
# ----------------------------
def find_file_id_by_name(drive_service, name: str, folder_id: Optional[str]) -> Optional[str]:
    # フォルダ指定がある場合はフォルダ内だけ検索
    safe_name = name.replace("'", "\\'")
    q_parts = [f"name = '{safe_name}'", "trashed = false"]
    if folder_id:
        q_parts.append(f"'{folder_id}' in parents")
    q = " and ".join(q_parts)

    res = drive_service.files().list(
        q=q,
        spaces="drive",
        fields="files(id,name,modifiedTime)",
        pageSize=5,
    ).execute()

    files = res.get("files", [])
    if not files:
        return None
    # 同名が複数あれば最初の1件（運用上は latest は1件に寄せるのが推奨）
    return files[0]["id"]


def upsert_csv_as_google_sheet(
    drive_service,
    csv_bytes: bytes,
    sheet_name: str,
    folder_id: Optional[str] = None,
) -> dict:
    """
    - 同名ファイルがあれば update（上書き）
    - なければ create（CSV→Google Sheetsに変換して作成）
    """
    media = MediaInMemoryUpload(csv_bytes, mimetype="text/csv", resumable=False)
    existing_id = find_file_id_by_name(drive_service, sheet_name, folder_id)

    if existing_id:
        # 既存SheetをCSV内容で上書き
        updated = drive_service.files().update(
            fileId=existing_id,
            media_body=media,
            fields="id,name,webViewLink",
        ).execute()
        return updated

    metadata = {"name": sheet_name, "mimeType": GOOGLE_SHEETS_MIME}
    if folder_id:
        metadata["parents"] = [folder_id]

    created = drive_service.files().create(
        body=metadata,
        media_body=media,
        fields="id,name,webViewLink",
    ).execute()
    return created


# ----------------------------
# Main
# ----------------------------
def main():
    # Required
    toggl_api_token = require_env("TOGGL_API_TOKEN")
    workspace_id = require_env("TOGGL_WORKSPACE_ID")

    # Optional
    folder_id = os.environ.get("DRIVE_FOLDER_ID")  # My Drive 内フォルダ推奨（未指定なら直下）
    #write_daily = env_bool("WRITE_DAILY_COPY", True)
    write_daily = False
  
    start_date, end_date = resolve_date_range()

    print(f"[INFO] Fetching Toggl CSV: workspace={workspace_id}, range={start_date}..{end_date}")
    csv_bytes = fetch_toggl_csv(
        workspace_id=workspace_id,
        toggl_api_token=toggl_api_token,
        start_date=start_date,
        end_date=end_date,
    )
    print(f"[INFO] CSV bytes: {len(csv_bytes)}")

    print("[INFO] Building Drive client (ADC via WIF)")
    drive = get_drive_service_from_adc()
    about = drive.about().get(fields="user(emailAddress),storageQuota").execute()
    print("[DEBUG] Drive user:", about.get("user"))
    print("[DEBUG] Drive storageQuota:", about.get("storageQuota"))

    # 1) latest（固定名で上書き）
    latest_name = "toggl_time_entries_latest"
    latest = upsert_csv_as_google_sheet(
        drive_service=drive,
        csv_bytes=csv_bytes,
        sheet_name=latest_name,
        folder_id=folder_id,
    )
    print("✅ latest saved:", latest["name"])
    print("   link:", latest.get("webViewLink"))

    # 2) 日付版（任意）
    if write_daily:
        today = dt.date.today().strftime("%Y-%m-%d")
        daily_name = f"toggl_time_entries_{today}"
        daily = upsert_csv_as_google_sheet(
            drive_service=drive,
            csv_bytes=csv_bytes,
            sheet_name=daily_name,
            folder_id=folder_id,
        )
        print("✅ daily saved:", daily["name"])
        print("   link:", daily.get("webViewLink"))


if __name__ == "__main__":
    main()
