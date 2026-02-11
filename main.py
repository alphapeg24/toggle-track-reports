from google.auth import default
from google.auth.transport.requests import Request

def main():
    creds, project_id = default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(Request())
    print("Authenticated OK")
    print("Project ID (maybe None):", project_id)
    print("Access token starts with:", creds.token[:10])

if __name__ == "__main__":
    main()
