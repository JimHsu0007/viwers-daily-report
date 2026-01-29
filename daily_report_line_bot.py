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


def meta_insights_multi(object_id: str, token: str, metrics: list[str], since: str, until: str) -> list[int | None]:
    """
    回傳與 metrics 對齊的 list，抓不到就 None
    period=day, since/until 設成同一天代表「那一天」
    """
    url = f"https://graph.facebook.com/v19.0/{object_id}/insights"
    params = {
        "metric": ",".join(metrics),
        "period": "day",
        "since": since,
        "until": until,
        "access_token": token,
    }
    r = requests.get(url, params=params, timeout=30)
    if r.status_code != 200:
        print("[Meta] error:", r.status_code, r.text)
        return [None] * len(metrics)

    data = r.json().get("data", [])
    # 轉成按 metric name 查值（不用 dict 也行：用兩層迴圈對齊）
    out: list[int | None] = []
    for m in metrics:
        found = None
        for item in data:
            if item.get("name") == m:
                values = item.get("values", [])
                if values:
                    v = values[0].get("value")
                    if isinstance(v, (int, float)):
                        found = int(v)
                break
        out.append(found)
    return out


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


def fmt(n: int | None) -> str:
    return "N/A" if n is None else f"{n:,}"


def main():
    # LINE
    line_token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    line_to = os.environ.get("LINE_TO_ID", "")
    if not line_token or not line_to:
        raise RuntimeError("Missing LINE_CHANNEL_ACCESS_TOKEN or LINE_TO_ID")

    # Meta
    meta_token = os.environ.get("META_ACCESS_TOKEN", "")
    fb_page_id = os.environ.get("FB_PAGE_ID", "")
    ig_user_id = os.environ.get("IG_USER_ID", "")

    # GA4
    ga4_property_id = os.environ.get("GA4_PROPERTY_ID", "")
    ga4_credentials_json = os.environ.get("GA4_CREDENTIALS_JSON", "")

    # 昨天（台北時區）
    today_tw = datetime.now(TZ_TAIPEI).date()
    yday = today_tw - timedelta(days=1)
    since = ymd(yday)
    until = ymd(yday)

    # FB：用 page_impressions_unique 當「reach(近似唯一觸及)」，page_impressions 當「impressions」
    fb_reach = fb_impr = None
    if meta_token and fb_page_id:
        fb_reach, fb_impr = meta_insights_multi(
            fb_page_id,
            meta_token,
            metrics=["page_impressions_unique", "page_impressions"],
            since=since,
            until=until,
        )

    # IG：reach + impressions
    ig_reach = ig_impr = None
    if meta_token and ig_user_id:
        ig_reach, ig_impr = meta_insights_multi(
            ig_user_id,
            meta_token,
            metrics=["reach", "impressions"],
            since=since,
            until=until,
        )

    # GA4：activeUsers + totalUsers + screenPageViews
    ga_active = ga_total = ga_views = None
    if ga4_property_id and ga4_credentials_json:
        ga_active, ga_total, ga_views = ga4_yesterday(ga4_property_id, ga4_credentials_json, since, until)

    msg = (
        f"📊 24 小時匯總（以昨天為單位）\n"
        f"日期：{since}\n\n"
        f"FB\n"
        f"- Reach(唯一)：{fmt(fb_reach)}\n"
        f"- Impressions：{fmt(fb_impr)}\n\n"
        f"IG\n"
        f"- Reach：{fmt(ig_reach)}\n"
        f"- Impressions：{fmt(ig_impr)}\n\n"
        f"官網（GA4）\n"
        f"- activeUsers：{fmt(ga_active)}\n"
        f"- totalUsers：{fmt(ga_total)}\n"
        f"- pageViews：{fmt(ga_views)}"
    )

    line_push(line_token, line_to, msg)


if __name__ == "__main__":
    main()
