#!/usr/bin/env python3
"""
Media Monitor — denný monitoring SK/CZ médií, Google News, GDELT a Redditu
podľa zadaných kľúčových slov, s odosielaním HTML emailového reportu.

Použitie:
    python media_monitor.py

Konfigurácia sa nastavuje nižšie v sekcii CONFIG alebo cez premenné prostredia
(odporúčané pre email credentials — nikdy necommituj heslo do kódu).
"""

import os
import re
import json
import hashlib
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import quote

import requests
import feedparser

# ============================== CONFIG ==============================

# Kľúčové slová, ktoré chceš sledovať. Dá sa prebiť premennou prostredia
# KEYWORDS (čiarkou oddelený zoznam) — vďaka tomu môže tá istá skriptová
# základňa bežať ako viacero nezávislých monitoringov (rôzne workflow súbory
# v .github/workflows/, každý s vlastným KEYWORDS/EMAIL_TO/SEEN_STORE_FILE).
_keywords_env = os.environ.get("KEYWORDS")
KEYWORDS = [k.strip() for k in _keywords_env.split(",") if k.strip()] if _keywords_env else ["Orange"]

# Za posledných koľko hodín hľadať. Nastav podľa frekvencie behu skriptu:
# - beží raz denne (cron "0 7 * * *")  -> HOURS_BACK = 24
# - beží raz za hodinu (cron "0 * * * *") -> HOURS_BACK = 1 (odporúčané o niečo
#   väčšie okno napr. 2, aby sa nestratili položky pri oneskorení feedov)
HOURS_BACK = 2

# Súbor, kam sa ukladajú ID už odoslaných výsledkov, aby sa pri hodinovom
# behu (prekrývajúce sa okná) neposielali duplicity. Pri dennom behu to
# tiež nevadí, len drží históriu o niečo dlhšie. Názov súboru sa dá prebiť
# premennou SEEN_STORE_FILE, aby si viacero monitoringov v tom istom repe
# neprepisovali navzájom históriu.
SEEN_STORE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    os.environ.get("SEEN_STORE_FILE", "seen_items.json"),
)
# Ako dlho (v hodinách) sa pamätajú už odoslané ID, kým sa vyčistia zo súboru
SEEN_RETENTION_HOURS = 72

# Ak True, email sa pošle len vtedy, keď sú nové výsledky (odporúčané pre
# hodinovú frekvenciu, aby ti nechodili prázdne emaily každú hodinu)
SEND_ONLY_IF_RESULTS = True

# RSS feedy slovenských/českých médií, ktoré sa budú prehľadávať
# (nie sú keyword-searchable, takže filtrujeme podľa nadpisu/perexu)
MEDIA_RSS_FEEDS = [
    ("SME", "https://www.sme.sk/rss-title"),
    ("Aktuality.sk", "https://www.aktuality.sk/rss/"),
    ("Pravda", "https://spravy.pravda.sk/rss/xml/"),
    ("Denník N", "https://dennikn.sk/feed/"),
    ("Živé.sk", "https://www.zive.sk/rss/sc-47/default.aspx"),
    ("iDNES.cz", "https://servis.idnes.cz/rss.aspx?c=zpravodaj"),
    ("Novinky.cz", "https://www.novinky.cz/rss"),
    ("iROZHLAS.cz", "https://www.irozhlas.cz/rss/irozhlas"),
]

# Reddit User-Agent (Reddit blokuje requesty bez neho)
REDDIT_USER_AGENT = "media-monitor-script/1.0 (by u/example)"

# Email nastavenia — odporúčané nastaviť cez premenné prostredia
SMTP_HOST = os.environ.get("SMTP_HOST") or "smtp.gmail.com"
SMTP_PORT = int(os.environ.get("SMTP_PORT") or "587")
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM") or SMTP_USER
EMAIL_TO = os.environ.get("EMAIL_TO", "")  # môže byť "a@x.com,b@y.com"

# =====================================================================


def now_utc():
    return datetime.now(timezone.utc)


def within_window(published_dt, hours=HOURS_BACK):
    if published_dt is None:
        return False
    return now_utc() - published_dt <= timedelta(hours=hours)


def make_id(url):
    return hashlib.sha1(url.encode("utf-8")).hexdigest()


# ------------------------- ZDROJ 1: Google News RSS -------------------------

def fetch_google_news(keyword):
    """Google News RSS. Google podporuje 'when:Nh' aj 'when:Nd', ale pre
    istotu si výsledky ešte raz sami odfiltrujeme podľa published_parsed."""
    when_value = f"{HOURS_BACK}h" if HOURS_BACK < 24 else f"{max(1, HOURS_BACK // 24)}d"
    url = (
        f"https://news.google.com/rss/search?q={quote(keyword)}+when:{when_value}"
        f"&hl=sk&gl=SK&ceid=SK:sk"
    )
    results = []
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries:
            published_dt = None
            if entry.get("published_parsed"):
                published_dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            if published_dt and not within_window(published_dt):
                continue
            results.append({
                "source": f"Google News ({entry.get('source', {}).get('title', 'N/A')})",
                "title": entry.get("title", ""),
                "link": entry.get("link", ""),
                "published": entry.get("published", ""),
                "keyword": keyword,
                "channel": "media",
            })
    except Exception as e:
        print(f"[Google News] chyba pre '{keyword}': {e}")
    return results


# ------------------------------ ZDROJ 2: GDELT ------------------------------

def fetch_gdelt(keyword):
    """GDELT Doc API — timespan sa odvodí z HOURS_BACK (GDELT podporuje
    formáty ako '2h', '1d', '15min')."""
    timespan = f"{HOURS_BACK}h" if HOURS_BACK < 24 else f"{max(1, HOURS_BACK // 24)}d"
    url = (
        "https://api.gdeltproject.org/api/v2/doc/doc"
        f"?query={quote(keyword)}&mode=artlist&maxrecords=100"
        f"&timespan={timespan}&format=json"
    )
    results = []
    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        for art in data.get("articles", []):
            results.append({
                "source": f"GDELT ({art.get('domain', 'N/A')})",
                "title": art.get("title", ""),
                "link": art.get("url", ""),
                "published": art.get("seendate", ""),
                "keyword": keyword,
                "channel": "media",
            })
    except Exception as e:
        print(f"[GDELT] chyba pre '{keyword}': {e}")
    return results


# ------------------------------ ZDROJ 3: Reddit ------------------------------

def fetch_reddit(keyword):
    """Verejné Reddit vyhľadávanie (bez OAuth) — funguje pre bežné použitie,
    ale je rate-limited a Reddit môže prístup kedykoľvek sprísniť."""
    url = f"https://www.reddit.com/search.json?q={quote(keyword)}&sort=new&limit=50"
    results = []
    try:
        resp = requests.get(url, headers={"User-Agent": REDDIT_USER_AGENT}, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        for child in data.get("data", {}).get("children", []):
            post = child.get("data", {})
            created = datetime.fromtimestamp(post.get("created_utc", 0), tz=timezone.utc)
            if not within_window(created):
                continue
            results.append({
                "source": f"Reddit (r/{post.get('subreddit', 'N/A')})",
                "title": post.get("title", ""),
                "link": f"https://www.reddit.com{post.get('permalink', '')}",
                "published": created.strftime("%Y-%m-%d %H:%M UTC"),
                "keyword": keyword,
                "channel": "social",
            })
    except Exception as e:
        print(f"[Reddit] chyba pre '{keyword}': {e}")
    return results


# ------------------------- ZDROJ 4: SK/CZ médiá RSS -------------------------

def fetch_media_rss(keywords):
    """Prejde zoznam RSS feedov SK/CZ médií a filtruje podľa kľúčových slov
    v nadpise alebo perexe, plus podľa času publikovania."""
    results = []
    patterns = {kw: re.compile(re.escape(kw), re.IGNORECASE) for kw in keywords}

    for media_name, feed_url in MEDIA_RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries:
                title = entry.get("title", "")
                summary = entry.get("summary", "")
                text = f"{title} {summary}"

                published_dt = None
                if entry.get("published_parsed"):
                    published_dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)

                if published_dt and not within_window(published_dt):
                    continue

                for kw, pattern in patterns.items():
                    if pattern.search(text):
                        results.append({
                            "source": media_name,
                            "title": title,
                            "link": entry.get("link", ""),
                            "published": entry.get("published", ""),
                            "keyword": kw,
                            "channel": "media",
                        })
        except Exception as e:
            print(f"[{media_name}] chyba: {e}")
    return results


# ------------------------------ SPRACOVANIE ------------------------------

def dedupe(results):
    seen = set()
    unique = []
    for r in results:
        uid = make_id(r["link"]) if r["link"] else make_id(r["title"])
        if uid not in seen:
            seen.add(uid)
            unique.append(r)
    return unique


def load_seen_store():
    """Načíta {id: iso_timestamp} už odoslaných položiek a odstráni staré."""
    if not os.path.exists(SEEN_STORE_PATH):
        return {}
    try:
        with open(SEEN_STORE_PATH, "r", encoding="utf-8") as f:
            store = json.load(f)
    except Exception:
        return {}

    cutoff = now_utc() - timedelta(hours=SEEN_RETENTION_HOURS)
    cleaned = {}
    for uid, ts in store.items():
        try:
            if datetime.fromisoformat(ts) >= cutoff:
                cleaned[uid] = ts
        except Exception:
            continue
    return cleaned


def save_seen_store(store):
    try:
        os.makedirs(os.path.dirname(SEEN_STORE_PATH), exist_ok=True)
        with open(SEEN_STORE_PATH, "w", encoding="utf-8") as f:
            json.dump(store, f)
    except Exception as e:
        print(f"Nepodarilo sa uložiť {SEEN_STORE_PATH}: {e}")


def filter_unseen(results, seen_store):
    """Vráti len tie výsledky, ktoré ešte neboli odoslané, a zároveň
    obohatí seen_store o nové ID (mutuje seen_store)."""
    new_results = []
    now_iso = now_utc().isoformat()
    for r in results:
        uid = make_id(r["link"]) if r["link"] else make_id(r["title"])
        if uid not in seen_store:
            seen_store[uid] = now_iso
            new_results.append(r)
    return new_results


def collect_all():
    all_results = []
    for kw in KEYWORDS:
        all_results += fetch_google_news(kw)
        all_results += fetch_gdelt(kw)
        all_results += fetch_reddit(kw)
    all_results += fetch_media_rss(KEYWORDS)
    return dedupe(all_results)


# -------------------------------- REPORT --------------------------------

def build_html_report(results):
    """Krátka notifikácia na položku — nadpis (link) + zdroj/dátum, bez
    kategorizácie a súhrnov, aby bol email na prvý pohľad jasný."""
    if not results:
        return "<p style='color:#888;'>Žiadne nové výsledky.</p>"

    rows = ""
    for r in results:
        rows += f"""
        <div style="padding:10px 0;border-bottom:1px solid #eee;">
          <a href="{r['link']}" style="color:#1a73e8;text-decoration:none;font-weight:600;">
            🆕 {r['title']}
          </a><br/>
          <span style="color:#666;font-size:12px;">{r['source']} · {r['published']}</span>
        </div>"""

    return f"""
    <html>
    <body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;">
      {rows}
    </body>
    </html>
    """


def send_email(html_body, subject):
    if not SMTP_USER or not SMTP_PASS or not EMAIL_TO:
        print("Email nie je nakonfigurovaný (SMTP_USER/SMTP_PASS/EMAIL_TO) — report sa iba vypíše nižšie.\n")
        print(html_body)
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg.attach(MIMEText(html_body, "html"))

    context = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls(context=context)
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(EMAIL_FROM, EMAIL_TO.split(","), msg.as_string())
    print(f"Report odoslaný na {EMAIL_TO}")


def main():
    print("Zbieram výsledky...")
    results = collect_all()
    print(f"Nájdených {len(results)} unikátnych výsledkov v okne {HOURS_BACK}h.")

    seen_store = load_seen_store()
    new_results = filter_unseen(results, seen_store)
    save_seen_store(seen_store)
    print(f"Z toho {len(new_results)} nových (ešte neodoslaných).")

    if not new_results and SEND_ONLY_IF_RESULTS:
        print("Žiadne nové výsledky, email sa neposiela.")
        return

    if len(new_results) == 1:
        subject = f"🆕 Nová zmienka: {new_results[0]['title']}"
    else:
        subject = f"🆕 {len(new_results)} nových zmienok o {', '.join(KEYWORDS)}"

    html = build_html_report(new_results)
    send_email(html, subject)


if __name__ == "__main__":
    main()
