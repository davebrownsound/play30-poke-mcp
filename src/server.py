#!/usr/bin/env python3
"""
Poke MCP Server — PLAY30 Challenge Tracker

A 30-day challenge where users log movement, content creation, reading,
and hangout time with friends. Poke delivers daily challenges via iMessage
and logs everything to a Google Sheet in the user's Drive.
"""

import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken
from fastmcp.server.auth.providers.google import GoogleProvider
from fastmcp.server.dependencies import CurrentAccessToken
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from storage import FileKeyValue

load_dotenv()

# ---------------------------------------------------------------------------
# Google OAuth config
# ---------------------------------------------------------------------------
GOOGLE_CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]


# ---------------------------------------------------------------------------
# Auth — GoogleProvider (OAuth proxy with built-in token verification)
# ---------------------------------------------------------------------------
auth = GoogleProvider(
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    base_url=PUBLIC_BASE_URL,
    redirect_path="/auth/callback",
    valid_scopes=SCOPES,
    required_scopes=SCOPES,
    client_storage=FileKeyValue(),
    extra_authorize_params={
        "access_type": "offline",
        "prompt": "consent",
    },
)


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------
mcp = FastMCP("PLAY30 Challenge Tracker", auth=auth)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _sheets_service(token: AccessToken):
    creds = Credentials(token=token.token)
    return build("sheets", "v4", credentials=creds)


def _drive_service(token: AccessToken):
    creds = Credentials(token=token.token)
    return build("drive", "v3", credentials=creds)


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Sheet structure
# ---------------------------------------------------------------------------
ACTIVITY_HEADERS = [
    "Date",
    "Day #",
    "Category",       # Move, Create, Read, Play
    "What I Did",
    "Duration (min)",
    "Who With",
    "Notes",
]

CHALLENGE_HEADERS = [
    "Day #",
    "Date",
    "Challenge",
    "Category",
    "Completed",
]

CATEGORIES = ["Move", "Create", "Read", "Play"]


# ---------------------------------------------------------------------------
# Tools — Setup
# ---------------------------------------------------------------------------
@mcp.tool(description="Start a new PLAY30 challenge. Creates a Google Sheet with Activity Log and Daily Challenges tabs. Returns the sheet ID and URL.")
async def start_challenge(
    player_name: str = "Player",
    token: AccessToken = CurrentAccessToken(),
) -> dict:
    """Create the PLAY30 tracker spreadsheet."""
    sheets = _sheets_service(token)

    title = f"PLAY30 — {player_name}"

    spreadsheet = sheets.spreadsheets().create(
        body={
            "properties": {"title": title},
            "sheets": [
                {
                    "properties": {
                        "title": "Activity Log",
                        "sheetId": 0,
                        "gridProperties": {"frozenRowCount": 1},
                    }
                },
                {
                    "properties": {
                        "title": "Challenges",
                        "sheetId": 1,
                        "gridProperties": {"frozenRowCount": 1},
                    }
                },
            ],
        }
    ).execute()

    sid = spreadsheet["spreadsheetId"]
    url = spreadsheet["spreadsheetUrl"]

    # Write headers to both tabs
    sheets.spreadsheets().values().batchUpdate(
        spreadsheetId=sid,
        body={
            "valueInputOption": "RAW",
            "data": [
                {"range": "Activity Log!A1:G1", "values": [ACTIVITY_HEADERS]},
                {"range": "Challenges!A1:E1", "values": [CHALLENGE_HEADERS]},
            ],
        },
    ).execute()

    # Format both header rows
    sheets.spreadsheets().batchUpdate(
        spreadsheetId=sid,
        body={
            "requests": [
                _bold_header_request(sheet_id=0, col_count=7),
                _bold_header_request(sheet_id=1, col_count=5),
            ]
        },
    ).execute()

    return {
        "spreadsheet_id": sid,
        "url": url,
        "message": f"PLAY30 challenge started for {player_name}! Sheet is ready.",
    }


def _bold_header_request(sheet_id: int, col_count: int) -> dict:
    return {
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 0,
                "endRowIndex": 1,
                "startColumnIndex": 0,
                "endColumnIndex": col_count,
            },
            "cell": {
                "userEnteredFormat": {
                    "textFormat": {"bold": True},
                    "backgroundColor": {"red": 0.93, "green": 0.93, "blue": 0.93},
                }
            },
            "fields": "userEnteredFormat(textFormat,backgroundColor)",
        }
    }


# ---------------------------------------------------------------------------
# Tools — Log Activities
# ---------------------------------------------------------------------------
@mcp.tool(description="Log a movement or workout. Examples: ran 3 miles, yoga class, bike ride, walked the dog.")
async def log_movement(
    spreadsheet_id: str,
    what: str,
    duration_min: int = 0,
    who_with: str = "",
    notes: str = "",
    token: AccessToken = CurrentAccessToken(),
) -> dict:
    """Log a Move activity."""
    return _log_activity(token, spreadsheet_id, "Move", what, duration_min, who_with, notes)


@mcp.tool(description="Log content creation. Examples: wrote a blog post, recorded a video, designed a poster, made a playlist.")
async def log_content(
    spreadsheet_id: str,
    what: str,
    duration_min: int = 0,
    who_with: str = "",
    notes: str = "",
    token: AccessToken = CurrentAccessToken(),
) -> dict:
    """Log a Create activity."""
    return _log_activity(token, spreadsheet_id, "Create", what, duration_min, who_with, notes)


@mcp.tool(description="Log something you read. Examples: read 30 pages of Dune, finished an article on AI, read a newsletter.")
async def log_reading(
    spreadsheet_id: str,
    what: str,
    duration_min: int = 0,
    notes: str = "",
    token: AccessToken = CurrentAccessToken(),
) -> dict:
    """Log a Read activity."""
    return _log_activity(token, spreadsheet_id, "Read", what, duration_min, "", notes)


@mcp.tool(description="Log play time or a hangout with friends. Examples: game night, coffee with Sam, played basketball with the crew.")
async def log_play(
    spreadsheet_id: str,
    what: str,
    duration_min: int = 0,
    who_with: str = "",
    notes: str = "",
    token: AccessToken = CurrentAccessToken(),
) -> dict:
    """Log a Play activity."""
    return _log_activity(token, spreadsheet_id, "Play", what, duration_min, who_with, notes)


def _log_activity(
    token: AccessToken,
    spreadsheet_id: str,
    category: str,
    what: str,
    duration_min: int,
    who_with: str,
    notes: str,
) -> dict:
    sheets = _sheets_service(token)

    day_num = _get_current_day(token, spreadsheet_id)
    row = [_today(), str(day_num), category, what, str(duration_min) if duration_min else "", who_with, notes]

    sheets.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range="Activity Log!A:G",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]},
    ).execute()

    return {
        "message": f"Logged: {category} — {what}",
        "day": day_num,
        "category": category,
    }


# ---------------------------------------------------------------------------
# Tools — Challenges
# ---------------------------------------------------------------------------
@mcp.tool(description="Add a daily challenge to the challenge board.")
async def add_challenge(
    spreadsheet_id: str,
    day_number: int,
    challenge: str,
    category: str,
    token: AccessToken = CurrentAccessToken(),
) -> dict:
    """Post a daily challenge (called by the Poke agent)."""
    sheets = _sheets_service(token)

    if category not in CATEGORIES:
        return {"error": f"Category must be one of: {', '.join(CATEGORIES)}"}

    row = [str(day_number), _today(), challenge, category, "No"]

    sheets.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range="Challenges!A:E",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]},
    ).execute()

    return {
        "message": f"Day {day_number} challenge posted: {challenge}",
        "category": category,
    }


@mcp.tool(description="Mark a daily challenge as completed.")
async def complete_challenge(
    spreadsheet_id: str,
    day_number: int,
    token: AccessToken = CurrentAccessToken(),
) -> dict:
    """Mark a challenge done by day number."""
    sheets = _sheets_service(token)

    result = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range="Challenges!A:E",
    ).execute()

    rows = result.get("values", [])
    for i, row in enumerate(rows[1:], start=2):
        if row and row[0] == str(day_number):
            sheets.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=f"Challenges!E{i}",
                valueInputOption="RAW",
                body={"values": [["Yes"]]},
            ).execute()
            return {"message": f"Day {day_number} challenge completed!"}

    return {"error": f"No challenge found for day {day_number}."}


# ---------------------------------------------------------------------------
# Tools — Progress & Stats
# ---------------------------------------------------------------------------
@mcp.tool(description="Get the user's PLAY30 progress summary — total activities, streak, category breakdown.")
async def get_progress(
    spreadsheet_id: str,
    token: AccessToken = CurrentAccessToken(),
) -> dict:
    """Pull stats from the activity log."""
    sheets = _sheets_service(token)

    result = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range="Activity Log!A:G",
    ).execute()

    rows = result.get("values", [])
    if len(rows) <= 1:
        return {"message": "No activities logged yet. Get moving!", "total": 0}

    headers = rows[0]
    entries = []
    for row in rows[1:]:
        padded = row + [""] * (len(headers) - len(row))
        entries.append(dict(zip(headers, padded)))

    # Category counts
    cats = {}
    for e in entries:
        cat = e.get("Category", "Other")
        cats[cat] = cats.get(cat, 0) + 1

    # Unique active days
    active_days = sorted(set(e.get("Date", "") for e in entries if e.get("Date")))

    # Total duration
    total_min = 0
    for e in entries:
        try:
            total_min += int(e.get("Duration (min)", 0) or 0)
        except ValueError:
            pass

    # Challenge completion
    ch_result = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range="Challenges!A:E",
    ).execute()
    ch_rows = ch_result.get("values", [])
    challenges_total = max(len(ch_rows) - 1, 0)
    challenges_done = sum(
        1 for r in ch_rows[1:] if len(r) >= 5 and r[4].strip().lower() == "yes"
    )

    current_day = _get_current_day(token, spreadsheet_id)

    return {
        "current_day": current_day,
        "total_activities": len(entries),
        "active_days": len(active_days),
        "total_minutes": total_min,
        "by_category": cats,
        "challenges_completed": f"{challenges_done}/{challenges_total}",
        "message": f"Day {current_day} of 30 — {len(entries)} activities logged across {len(active_days)} days. Keep it up!",
    }


@mcp.tool(description="View the activity log — optionally filter by category (Move, Create, Read, Play).")
async def view_log(
    spreadsheet_id: str,
    category: str = "",
    token: AccessToken = CurrentAccessToken(),
) -> dict:
    """Read back logged activities, optionally filtered."""
    sheets = _sheets_service(token)

    result = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range="Activity Log!A:G",
    ).execute()

    rows = result.get("values", [])
    if len(rows) <= 1:
        return {"entries": [], "message": "No activities yet."}

    headers = rows[0]
    entries = []
    for row in rows[1:]:
        padded = row + [""] * (len(headers) - len(row))
        entry = dict(zip(headers, padded))
        if category and entry.get("Category", "").lower() != category.lower():
            continue
        entries.append(entry)

    return {"entries": entries, "count": len(entries), "filter": category or "all"}


@mcp.tool(description="View all daily challenges and their completion status.")
async def view_challenges(
    spreadsheet_id: str,
    token: AccessToken = CurrentAccessToken(),
) -> dict:
    """List all challenges with done/not-done status."""
    sheets = _sheets_service(token)

    result = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range="Challenges!A:E",
    ).execute()

    rows = result.get("values", [])
    if len(rows) <= 1:
        return {"challenges": [], "message": "No challenges posted yet."}

    headers = rows[0]
    challenges = []
    for row in rows[1:]:
        padded = row + [""] * (len(headers) - len(row))
        challenges.append(dict(zip(headers, padded)))

    return {"challenges": challenges, "count": len(challenges)}


# ---------------------------------------------------------------------------
# Tools — Manage
# ---------------------------------------------------------------------------
@mcp.tool(description="List all PLAY30 tracker spreadsheets in the user's Google Drive.")
async def list_trackers(
    token: AccessToken = CurrentAccessToken(),
) -> dict:
    """Find all PLAY30 sheets."""
    drive = _drive_service(token)

    results = drive.files().list(
        q="mimeType='application/vnd.google-apps.spreadsheet' and name contains 'PLAY30'",
        spaces="drive",
        fields="files(id, name, createdTime, modifiedTime, webViewLink)",
        orderBy="modifiedTime desc",
        pageSize=10,
    ).execute()

    files = results.get("files", [])
    return {
        "trackers": [
            {
                "id": f["id"],
                "name": f["name"],
                "url": f.get("webViewLink", ""),
                "modified": f.get("modifiedTime", ""),
            }
            for f in files
        ],
        "count": len(files),
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _get_current_day(token: AccessToken, spreadsheet_id: str) -> int:
    """Figure out what day of the challenge we're on based on logged dates."""
    sheets = _sheets_service(token)

    result = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range="Activity Log!A:A",
    ).execute()

    rows = result.get("values", [])
    dates = set()
    for row in rows[1:]:
        if row and row[0]:
            dates.add(row[0])

    today = _today()
    if today in dates:
        return len(dates)
    return len(dates) + 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    host = "0.0.0.0"

    print(f"Starting PLAY30 Challenge Tracker on {host}:{port}")
    print(f"MCP endpoint: {PUBLIC_BASE_URL}/mcp")
    print(f"OAuth callback: {PUBLIC_BASE_URL}/auth/callback")

    mcp.run(
        transport="http",
        host=host,
        port=port,
        stateless_http=True,
    )
