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


# ====== 你提供的新版 FB/IG 抓取邏輯（改成可用 env 參數） ======

def get_fb_stable_stats(page_id: str, token: str) -> tuple[int | None, int | None]:
    """
    針對新版粉專優化的穩定抓取法：
    - 先換 Page Access Token
    - 再抓總追蹤/粉絲數
    - 再抓最新 5 篇貼文互動（reactions + comments）
    回傳：(total_fans, interaction_sum)，抓不到回 None
    """
    try:
        # 1) 換取 Page Access Token
        auth_url = f"https://graph.facebook.com/v19.0/{page_id}"
        auth_res = requests.get(
            auth_url,
            params={"fields": "access_token", "access_token": token},
            timeout=30,
        ).json()
        page_token = auth_res.get("access_token") or token

        # 2) 粉專基礎資料（總粉絲/追蹤）
        page_url = f"https://graph.facebook.com/v19.0/{page_id}"
        p_res = requests.get(
            page_url,
            params={"fields": "fan_count,followers_count", "access_token": page_token},
            timeout=30,
        ).json()
        total_fans_v = p_res.get("followers_count") or p_res.get("fan_count")
        total_fans = int(total_fans_v) if isinstance(total_fans_v, (int, float)) else None

        # 3) 最新 5 篇互動數（按讚+留言）
        feed_url = f"https://graph.facebook.com/v19.0/{page_id}/posts"
        f_params = {
            "fields": "reactions.summary(total_count),comments.summary(total_count)",
            "limit": 5,
            "access_token": page_token,
        }
        f_res = requests.get(feed_url, params=f_params, timeout=30).json()

        interaction_sum = 0
        has_any = False
        for post in f_res.get("data", []) or []:
            r_count = post.get("reactions", {}).get("summary", {}).get("total_count", 0) or 0
            c_count = post.get("comments", {}).get("summary", {}).get("total_count", 0) or 0
            interaction_sum += int(r_count) + int(c_count)
            has_any = True

        interactions = interaction_sum if has_any else 0
        return total_fans, interactions

    except Exception as _:
        return None, None


def get_ig_insights(ig_id: str, token: str, since: str, until: str) -> tuple[int | None, int | None]:
    """
    抓取 IG（昨天）reach/impressions。
    你的 since/until 會是同一天（昨天），就沿用你的主流程日期。
    回傳：(reach, impressions)，抓不到回 None
    """
    if not ig_id:
        return None, None

    try:
        url = f"https://graph.facebook.com/v19.0/{ig_id}/insights"
        params = {
            "metric": "reach,impressions",
            "period": "day",
            "since": since,
            "until": until,
            "access_token": token,
        }
        r = requests.get(url, params=params, timeout=30).json()
        data = r.get("data", []) or []

        reach = impr = None
        for item in data:
            name = item.get("name")
            values = item.get("values", []) or []
            if not values:
                continue
            v = values[0].get("value")
            if name == "reach" and isinstance(v, (int, float)):
                reach = int(v)
            if name == "impressions" and isinstance(v, (int, float)):
                impr = int(v)

        return reach, impr

    except Exception as _:
        return None, None


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

    # ====== FB（新版：總追蹤 + 近五篇互動） ======
    fb_fans = fb_interact = None
    if meta_token and fb_page_id:
        fb_fans, fb_interact = get_fb_stable_stats(fb_page_id, meta_token)

    # ====== IG（新版：昨日 reach + impressions） ======
    ig_reach = ig_impr = None
    if meta_token and ig_user_id:
        ig_reach, ig_impr = get_ig_insights(ig_user_id, meta_token, since, until)

    # ====== GA4（原本：activeUsers + totalUsers + screenPageViews） ======
    ga_active = ga_total = ga_views = None
    if ga4_property_id and ga4_credentials_json:
        ga_active, ga_total, ga_views = ga4_yesterday(
            ga4_property_id, ga4_credentials_json, since, until
        )

    msg = (
        f"📊 24 小時匯總（以昨天為單位）\n"
        f"日期：{since}\n\n"
        f"Facebook\n"
        f"- 總追蹤人數：{fmt(fb_fans)}\n"
        f"- 近五篇貼文互動：{fmt(fb_interact)}\n\n"
        f"Instagram\n"
        f"- 昨日觸及：{fmt(ig_reach)}\n"
        f"- 昨日曝光：{fmt(ig_impr)}\n\n"
        f"官網（GA4）\n"
        f"- 活躍使用者：{fmt(ga_active)}\n"
        f"- 使用者總數：{fmt(ga_total)}\n"
        f"- 頁面瀏覽次數：{fmt(ga_views)}"
    )

    line_push(line_token, line_to, msg)


if __name__ == "__main__":
    main()
