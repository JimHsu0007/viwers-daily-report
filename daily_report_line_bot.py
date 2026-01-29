import os
import requests
from datetime import datetime, date, timedelta, timezone

from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import RunReportRequest, DateRange, Metric

TZ_TAIPEI = timezone(timedelta(hours=8))


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


# ====== 新版：一次抓 FB/IG 追蹤人數（從 FB Page 取 integrated IG business account） ======

def meta_followers_report(page_id: str, token: str) -> tuple[int | None, int | None, str | None]:
    """
    回傳：(fb_followers, ig_followers, ig_username)
    - FB followers_count
    - IG followers_count 來自 instagram_business_account
    權限不夠或抓不到回 None
    """
    try:
        url = f"https://graph.facebook.com/v19.0/{page_id}"
        params = {
            "fields": "followers_count,instagram_business_account{username,followers_count}",
            "access_token": token,
        }
        res = requests.get(url, params=params, timeout=30).json()

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
    except Exception:
        return None, None, None


# ====== GA4（原封不動） ======

def ga4_yesterday(property_id: str, credentials_json: str, since: str, until: str):
    cred_path = "/tmp/ga4_sa.json"
    with open(cred_path, "w", encoding="utf-8") as f:
        f.write(credentials_json)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = cred_path

    client = BetaAnalyticsDataClient()
    req = RunReportRequest(
        property=f"properties/{property_id}",
        date_ranges=[DateRange(start_date=since, end_date=until)],
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
    active_users = int(row.metric_values[0].value)
    total_users = int(row.metric_values[1].value)
    page_views = int(row.metric_values[2].value)
    return active_users, total_users, page_views


def main():
    # LINE
    line_token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    line_to = os.environ.get("LINE_TO_ID", "")
    if not line_token or not line_to:
        raise RuntimeError("Missing LINE_CHANNEL_ACCESS_TOKEN or LINE_TO_ID")

    # Meta（不改你的名字）
    meta_token = os.environ.get("META_ACCESS_TOKEN", "")
    fb_page_id = os.environ.get("FB_PAGE_ID", "")

    # GA4（不改你的名字）
    ga4_property_id = os.environ.get("GA4_PROPERTY_ID", "")
    ga4_credentials_json = os.environ.get("GA4_CREDENTIALS_JSON", "")

    # 昨天（台北時區）
    today_tw = datetime.now(TZ_TAIPEI).date()
    yday = today_tw - timedelta(days=1)
    since = ymd(yday)
    until = ymd(yday)

    # ====== FB/IG（新版：只抓追蹤人數） ======
    fb_followers = ig_followers = None
    ig_username = None
    if meta_token and fb_page_id:
        fb_followers, ig_followers, ig_username = meta_followers_report(fb_page_id, meta_token)

    # ====== GA4 ======
    ga_active = ga_total = ga_views = None
    if ga4_property_id and ga4_credentials_json:
        ga_active, ga_total, ga_views = ga4_yesterday(
            ga4_property_id, ga4_credentials_json, since, until
        )

    ig_title = f"@{ig_username}" if ig_username else "(未連結/權限不足)"

    msg = (
        f"📊 24 小時匯總（以昨天為單位）\n"
        f"日期：{since}\n\n"
        f"Facebook\n"
        f"- 總追蹤人數：{fmt(fb_followers)}\n\n"
        f"Instagram {ig_title}\n"
        f"- 總追蹤人數：{fmt(ig_followers)}\n\n"
        f"官網（GA4）\n"
        f"- 活躍使用者：{fmt(ga_active)}\n"
        f"- 使用者總數：{fmt(ga_total)}\n"
        f"- 頁面瀏覽次數：{fmt(ga_views)}"
    )

    line_push(line_token, line_to, msg)


if __name__ == "__main__":
    main()
