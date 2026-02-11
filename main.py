import os
import json
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload
from datetime import datetime

def main():
    token_json = os.environ["GOOGLE_DRIVE_TOKEN"]
    folder_id = os.environ["DRIVE_FOLDER_ID"]

    creds = Credentials.from_authorized_user_info(json.loads(token_json))
    drive = build("drive", "v3", credentials=creds)

    content = f"Hello from GitHub OAuth!\n{datetime.now().isoformat()}".encode("utf-8")

    file_metadata = {
        "name": "oauth-test.txt",
        "parents": [folder_id],
    }

    media = MediaInMemoryUpload(content, mimetype="text/plain")

    file = drive.files().create(
        body=file_metadata,
        media_body=media,
        fields="id, name, webViewLink"
    ).execute()

    print("Uploaded:", file["name"])
    print("Link:", file["webViewLink"])

if __name__ == "__main__":
    main()
