import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import DateRange, Metric, RunReportRequest

TZ_TAIPEI = timezone(timedelta(hours=8))
SNAPSHOT_PATH = Path(__file__).with_name("daily_metrics.json")


def ymd(d: date) -> str:
    return d.isoformat()


def line_push(channel_access_token: str, to_id: str, message: str) -> None:
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Authorization": f"Bearer {channel_access_token}",
        "Content-Type": "application/json",
    }
    payload = {"to": to_id, "messages": [{"type": "text", "text": message}]}
    r = requests.post(url, headers=headers, json=payload, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"LINE push failed: {r.status_code} {r.text}")


def fmt(n: int | None) -> str:
    return "N/A" if n is None else f"{n:,}"


def fmt_comparison(current: int | None, previous: int | None) -> str:
    """Format a value together with its change from the previous day."""
    if current is None:
        return "N/A"
    if previous is None:
        return f"{fmt(current)}（無前日資料）"

    delta = current - previous
    if delta > 0:
        marker = "▲"
    elif delta < 0:
        marker = "▼"
    else:
        marker = "—"

    if previous == 0:
        percent = "無法計算" if delta else "0.0%"
    else:
        percent = f"{delta / previous:+.1%}"

    delta_text = f"{abs(delta):,}" if delta else "0"
    return f"{fmt(current)}（較前日 {marker} {delta_text} / {percent}）"


def load_snapshot() -> dict:
    try:
        data = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_snapshot(report_date: str, fb_followers: int | None, ig_followers: int | None) -> None:
    data = {
        "date": report_date,
        "fb_followers": fb_followers,
        "ig_followers": ig_followers,
    }
    SNAPSHOT_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def meta_followers_report(page_id: str, token: str) -> tuple[int | None, int | None, str | None]:
    """Return current FB followers, IG followers and IG username."""
    try:
        url = f"https://graph.facebook.com/v19.0/{page_id}"
        params = {
            "fields": "followers_count,instagram_business_account{username,followers_count}",
            "access_token": token,
        }
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        res = response.json()

        fb_v = res.get("followers_count")
        fb_followers = int(fb_v) if isinstance(fb_v, (int, float)) else None

        ig = res.get("instagram_business_account") or None
        ig_followers = None
        ig_username = None
        if isinstance(ig, dict):
            ig_username = ig.get("username")
            ig_v = ig.get("followers_count")
            if isinstance(ig_v, (int, float)):
                ig_followers = int(ig_v)

        return fb_followers, ig_followers, ig_username
    except (requests.RequestException, ValueError, TypeError):
        return None, None, None


def ga4_report(client: BetaAnalyticsDataClient, property_id: str, report_date: str):
    req = RunReportRequest(
        property=f"properties/{property_id}",
        date_ranges=[DateRange(start_date=report_date, end_date=report_date)],
        metrics=[
            Metric(name="activeUsers"),
            Metric(name="totalUsers"),
            Metric(name="screenPageViews"),
        ],
    )
    resp = client.run_report(req)
    if not resp.rows:
        return None, None, None

    row = resp.rows[0]
    return tuple(int(value.value) for value in row.metric_values)


def ga4_two_days(property_id: str, credentials_json: str, yesterday: str, day_before: str):
    cred_path = "/tmp/ga4_sa.json"
    with open(cred_path, "w", encoding="utf-8") as f:
        f.write(credentials_json)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = cred_path

    client = BetaAnalyticsDataClient()
    return (
        ga4_report(client, property_id, yesterday),
        ga4_report(client, property_id, day_before),
    )


def main():
    line_token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    line_to = os.environ.get("LINE_TO_ID", "")
    if not line_token or not line_to:
        raise RuntimeError("Missing LINE_CHANNEL_ACCESS_TOKEN or LINE_TO_ID")

    meta_token = os.environ.get("META_ACCESS_TOKEN", "")
    fb_page_id = os.environ.get("FB_PAGE_ID", "")
    ga4_property_id = os.environ.get("GA4_PROPERTY_ID", "")
    ga4_credentials_json = os.environ.get("GA4_CREDENTIALS_JSON", "")

    today_tw = datetime.now(TZ_TAIPEI).date()
    yesterday = ymd(today_tw - timedelta(days=1))
    day_before = ymd(today_tw - timedelta(days=2))

    previous_snapshot = load_snapshot()
    previous_fb = previous_snapshot.get("fb_followers")
    previous_ig = previous_snapshot.get("ig_followers")

    fb_followers = ig_followers = None
    ig_username = None
    if meta_token and fb_page_id:
        fb_followers, ig_followers, ig_username = meta_followers_report(fb_page_id, meta_token)

    ga_current = (None, None, None)
    ga_previous = (None, None, None)
    if ga4_property_id and ga4_credentials_json:
        ga_current, ga_previous = ga4_two_days(
            ga4_property_id, ga4_credentials_json, yesterday, day_before
        )

    ga_active, ga_total, ga_views = ga_current
    prev_active, prev_total, prev_views = ga_previous
    ig_title = f"@{ig_username}" if ig_username else "(未連結/權限不足)"

    msg = (
        f"📊 每日數據報告\n"
        f"日期：{yesterday}（比較基準：{day_before}）\n\n"
        f"Facebook\n"
        f"- 總追蹤人數：{fmt_comparison(fb_followers, previous_fb)}\n\n"
        f"Instagram {ig_title}\n"
        f"- 總追蹤人數：{fmt_comparison(ig_followers, previous_ig)}\n\n"
        f"官網（GA4）\n"
        f"- 活躍使用者：{fmt_comparison(ga_active, prev_active)}\n"
        f"- 使用者總數：{fmt_comparison(ga_total, prev_total)}\n"
        f"- 頁面瀏覽次數：{fmt_comparison(ga_views, prev_views)}"
    )

    line_push(line_token, line_to, msg)
    save_snapshot(yesterday, fb_followers, ig_followers)


if __name__ == "__main__":
    main()
