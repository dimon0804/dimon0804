"""Собирает карточки профиля и блок «Сейчас в работе» по данным GitHub API.

Запуск: GH_TOKEN=<токен> python scripts/build.py
Токену нужен доступ на чтение ко всем репозиториям, иначе закрытые не попадут в счёт.
"""

import http.client
import json
import os
import re
import time
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
MSK = timezone(timedelta(hours=3))
EXCLUDE = {"Cython", "Jupyter Notebook", "Batchfile"}
TOKEN = os.environ.get("GH_TOKEN") or os.environ["GITHUB_TOKEN"]

THEMES = {
    "light": dict(bg="#ffffff", card="#f8fafc", border="#e2e8f0", text="#0f172a",
                  muted="#64748b", accent="#4f46e5", soft="#e0e7ff", empty="#eef2f7"),
    "dark": dict(bg="#0d1117", card="#161b22", border="#30363d", text="#e6edf3",
                 muted="#8b949e", accent="#7b74f2", soft="#2a2a5a", empty="#1f242c"),
}

TEXT = {
    "ru": dict(
        title="Статистика GitHub", updated="обновлено",
        repos="репозиториев", repos_sub="{pub} открытых · {priv} закрытых",
        year="вкладов за год", year_sub="коммиты, PR, задачи, ревью",
        commits="коммитов за всё время", commits_sub="с {since}",
        active="активных дней за год", active_sub="рекорд: {peak} за один день",
        months="Вклады по месяцам", langs="Языки во всех репозиториях",
        month_names=["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"],
        recent_title="Открытые репозитории, в которые я пушил за последние 30 дней",
        private_note="и ещё {n} в закрытых репозиториях",
        ago=lambda d: "сегодня" if d == 0 else "вчера" if d == 1 else f"{d} дн. назад",
    ),
    "en": dict(
        title="GitHub stats", updated="updated",
        repos="repositories", repos_sub="{pub} public · {priv} private",
        year="contributions this year", year_sub="commits, PRs, issues, reviews",
        commits="commits all time", commits_sub="since {since}",
        active="active days this year", active_sub="record: {peak} in a single day",
        months="Contributions by month", langs="Languages across all repositories",
        month_names=["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
        recent_title="Public repositories I pushed to in the last 30 days",
        private_note="plus {n} private repositories",
        ago=lambda d: "today" if d == 0 else "yesterday" if d == 1 else f"{d} days ago",
    ),
}


def gql(query, **variables):
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": f"bearer {TOKEN}", "User-Agent": "dimon0804-profile"},
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = json.load(resp)
            break
        except (OSError, http.client.HTTPException, json.JSONDecodeError):
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
    if body.get("errors"):
        raise RuntimeError(body["errors"])
    return body["data"]


def collect():
    base = gql("""query{viewer{login createdAt
      pub:repositories(ownerAffiliations:OWNER,privacy:PUBLIC){totalCount}
      priv:repositories(ownerAffiliations:OWNER,privacy:PRIVATE){totalCount}}}""")["viewer"]

    repos, cursor = [], None
    while True:
        page = gql("""query($c:String){viewer{repositories(first:100,after:$c,ownerAffiliations:OWNER,isFork:false){
          pageInfo{hasNextPage endCursor}
          nodes{name isPrivate pushedAt description url stargazerCount
            languages(first:10,orderBy:{field:SIZE,direction:DESC}){edges{size node{name color}}}}}}}""",
                   c=cursor)["viewer"]["repositories"]
        repos += page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]

    created = datetime.fromisoformat(base["createdAt"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    days, commits = {}, 0
    start = created
    while start < now:
        end = min(start + timedelta(days=365), now)
        cc = gql("""query($f:DateTime,$t:DateTime){viewer{contributionsCollection(from:$f,to:$t){
          totalCommitContributions restrictedContributionsCount
          contributionCalendar{weeks{contributionDays{date contributionCount}}}}}}""",
                 f=start.isoformat(), t=end.isoformat())["viewer"]["contributionsCollection"]
        commits += cc["totalCommitContributions"]
        for w in cc["contributionCalendar"]["weeks"]:
            for d in w["contributionDays"]:
                days[d["date"]] = max(days.get(d["date"], 0), d["contributionCount"])
        start = end

    return base, repos, days, commits, created


def fmt(n, lang):
    return f"{n:,}".replace(",", " " if lang == "ru" else ",")


def stats_svg(data, lang, theme):
    t, c = TEXT[lang], THEMES[theme]
    base, repos, days, commits, created = data
    today = datetime.now(MSK).date()
    year_total = sum(n for d, n in days.items() if date.fromisoformat(d) > today - timedelta(days=365))
    last_year = {d: n for d, n in days.items() if date.fromisoformat(d) > today - timedelta(days=365)}
    active_days = sum(1 for n in last_year.values() if n)
    peak_day = max(last_year.values(), default=0)

    months = []
    for i in range(11, -1, -1):
        y, m = today.year, today.month - i
        while m <= 0:
            m += 12
            y -= 1
        total = sum(n for d, n in days.items() if d.startswith(f"{y:04d}-{m:02d}"))
        months.append((t["month_names"][m - 1], total))

    lang_size, lang_color = Counter(), {}
    for r in repos:
        for e in r["languages"]["edges"]:
            if e["node"]["name"] in EXCLUDE:
                continue
            lang_size[e["node"]["name"]] += e["size"]
            lang_color[e["node"]["name"]] = e["node"]["color"] or c["muted"]
    total_size = sum(lang_size.values()) or 1
    top = lang_size.most_common(6)
    other = total_size - sum(s for _, s in top)

    W, H = 860, 430
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
           f'font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Inter,Helvetica,Arial,sans-serif">',
           f"""<style>
.f{{opacity:0;animation:fade .5s ease-out forwards}}
.b{{transform-box:fill-box;transform-origin:bottom;transform:scaleY(0);animation:grow .7s cubic-bezier(.2,.8,.2,1) forwards}}
.h{{transform-box:fill-box;transform-origin:left;transform:scaleX(0);animation:growx .8s cubic-bezier(.2,.8,.2,1) forwards}}
@keyframes fade{{to{{opacity:1}}}}
@keyframes grow{{to{{transform:scaleY(1)}}}}
@keyframes growx{{to{{transform:scaleX(1)}}}}
@media (prefers-reduced-motion:reduce){{.f,.b,.h{{animation:none;opacity:1;transform:none}}}}
</style>""",
           f'<rect x=".5" y=".5" width="{W-1}" height="{H-1}" rx="14" fill="{c["bg"]}" stroke="{c["border"]}"/>',
           f'<text x="32" y="44" font-size="18" font-weight="700" fill="{c["text"]}">{t["title"]}</text>',
           f'<text x="{W-32}" y="44" font-size="12" fill="{c["muted"]}" text-anchor="end">'
           f'{t["updated"]} {today.strftime("%d.%m.%Y")}</text>']

    kpis = [
        (fmt(base["pub"]["totalCount"] + base["priv"]["totalCount"], lang), t["repos"],
         t["repos_sub"].format(pub=base["pub"]["totalCount"], priv=base["priv"]["totalCount"])),
        (fmt(year_total, lang), t["year"], t["year_sub"]),
        (fmt(commits, lang), t["commits"], t["commits_sub"].format(since=created.year)),
        (fmt(active_days, lang), t["active"], t["active_sub"].format(peak=peak_day)),
    ]
    kw, gap = (W - 64 - 3 * 14) / 4, 14
    for i, (big, label, sub) in enumerate(kpis):
        x = 32 + i * (kw + gap)
        out.append(f'<g class="f" style="animation-delay:{i * 0.08:.2f}s">'
                   f'<rect x="{x:.1f}" y="66" width="{kw:.1f}" height="104" rx="10" fill="{c["card"]}" stroke="{c["border"]}"/>'
                   f'<text x="{x + 18:.1f}" y="110" font-size="32" font-weight="700" fill="{c["accent"] if i == 3 else c["text"]}">{big}</text>'
                   f'<text x="{x + 18:.1f}" y="134" font-size="13" font-weight="600" fill="{c["text"]}">{escape(label)}</text>'
                   f'<text x="{x + 18:.1f}" y="154" font-size="11" fill="{c["muted"]}">{escape(sub)}</text></g>')

    # вклады по месяцам
    out.append(f'<text x="32" y="208" font-size="13" font-weight="600" fill="{c["text"]}">{t["months"]}</text>')
    peak = max(n for _, n in months) or 1
    cx, cw, ch, cy = 32, 470, 120, 360
    bw = cw / 12
    for i, (name, n) in enumerate(months):
        h = max(3, n / peak * ch)
        x = cx + i * bw + 5
        fill = c["accent"] if n == peak else c["soft"] if n else c["empty"]
        out.append(f'<rect class="b" style="animation-delay:{0.3 + i * 0.04:.2f}s" x="{x:.1f}" y="{cy - h:.1f}" '
                   f'width="{bw - 10:.1f}" height="{h:.1f}" rx="4" fill="{fill}"/>')
        if n:
            out.append(f'<text class="f" style="animation-delay:{0.8 + i * 0.04:.2f}s" x="{x + (bw - 10) / 2:.1f}" '
                       f'y="{cy - h - 6:.1f}" font-size="10" fill="{c["muted"]}" text-anchor="middle">{n}</text>')
        out.append(f'<text x="{x + (bw - 10) / 2:.1f}" y="{cy + 18}" font-size="11" fill="{c["muted"]}" '
                   f'text-anchor="middle">{name}</text>')

    # языки
    lx, lw = 540, W - 32 - 540
    out.append(f'<text x="{lx}" y="208" font-size="13" font-weight="600" fill="{c["text"]}">{t["langs"]}</text>')
    out.append(f'<clipPath id="lc"><rect x="{lx}" y="226" width="{lw}" height="12" rx="6"/></clipPath><g clip-path="url(#lc)">')
    x = lx
    parts = top + ([("Other", other)] if other else [])
    for i, (name, size) in enumerate(parts):
        w = size / total_size * lw
        color = lang_color.get(name, c["muted"])
        out.append(f'<rect class="h" style="animation-delay:{0.3 + i * 0.06:.2f}s" x="{x:.1f}" y="226" '
                   f'width="{w + 0.5:.1f}" height="12" fill="{color}"/>')
        x += w
    out.append("</g>")
    for i, (name, size) in enumerate(top):
        y = 268 + i * 22
        out.append(f'<g class="f" style="animation-delay:{0.6 + i * 0.06:.2f}s">'
                   f'<circle cx="{lx + 6}" cy="{y - 4}" r="5" fill="{lang_color[name]}"/>'
                   f'<text x="{lx + 18}" y="{y}" font-size="12" fill="{c["text"]}">{escape(name)}</text>'
                   f'<text x="{lx + lw}" y="{y}" font-size="12" fill="{c["muted"]}" text-anchor="end">'
                   f'{size / total_size * 100:.1f}%</text></g>')

    out.append(f'<text x="32" y="{H - 22}" font-size="11" fill="{c["muted"]}">github.com/{base["login"]} · CLV-DIGITAL</text>')
    out.append("</svg>")
    return "\n".join(out)


LEVELS = {
    "light": ["#c7d2fe", "#818cf8", "#4f46e5", "#3730a3"],
    "dark": ["#3b3790", "#5b54d6", "#7b74f2", "#aba6ff"],
}
ISO_TEXT = {
    "ru": dict(title="Год в коммитах", total="вкладов за 12 месяцев", best_day="лучший день",
               best_month="самый жаркий месяц", active="вкладов за лучшую неделю", weekdays="чаще всего коммичу в",
               day_names=["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"],
               month_full=["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август",
                           "сентябрь", "октябрь", "ноябрь", "декабрь"]),
    "en": dict(title="A year in commits", total="contributions in 12 months", best_day="best day",
               best_month="busiest month", active="contributions in the best week", weekdays="most active on",
               day_names=["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
               month_full=["January", "February", "March", "April", "May", "June", "July", "August",
                           "September", "October", "November", "December"]),
}


def shade(hex_color, k):
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(v * k))) for v in (r, g, b))


def iso_svg(data, lang, theme):
    t, c, lv = ISO_TEXT[lang], THEMES[theme], LEVELS[theme]
    days = data[2]
    today = datetime.now(MSK).date()
    start = today - timedelta(days=364)
    start -= timedelta(days=start.weekday())
    cells = []
    d = start
    while d <= today:
        cells.append(((d - start).days // 7, d.weekday(), days.get(d.isoformat(), 0), d))
        d += timedelta(days=1)
    peak = max((n for *_, n, _ in cells), default=1) or 1

    W, H = 860, 360
    ox, oy, top = 352, 96, 84
    ax, ay, bx, by = 9.0, 2.7, -6.6, 5.0

    def pt(x, y):
        return f"{x:.1f},{y:.1f}"

    def quad(p, q, r, u, fill, cls="", style=""):
        attrs = f' class="{cls}" style="{style}"' if cls else ""
        return f'<polygon{attrs} fill="{fill}" points="{pt(*p)} {pt(*q)} {pt(*r)} {pt(*u)}"/>'

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
           f'font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Inter,Helvetica,Arial,sans-serif">',
           """<style>
.f{opacity:0;animation:fade .45s ease-out forwards}
.r{opacity:0;transform:translateY(14px);animation:rise .6s cubic-bezier(.2,.8,.2,1) forwards}
@keyframes fade{to{opacity:1}}
@keyframes rise{to{opacity:1;transform:none}}
@media (prefers-reduced-motion:reduce){.f,.r{animation:none;opacity:1;transform:none}}
</style>""",
           f'<rect x=".5" y=".5" width="{W-1}" height="{H-1}" rx="14" fill="{c["bg"]}" stroke="{c["border"]}"/>']

    for w, wd, n, _ in sorted(cells, key=lambda x: (x[0] * ay + x[1] * by, x[0])):
        x0, y0 = ox + w * ax + wd * bx, oy + w * ay + wd * by
        p, q, r, u = (x0, y0), (x0 + ax, y0 + ay), (x0 + ax + bx, y0 + ay + by), (x0 + bx, y0 + by)
        delay = f"animation-delay:{0.15 + w * 0.018:.2f}s"
        g = 0.8
        if n == 0:
            out.append(quad(*((a[0] + (1 - g) / 2 * (ax + bx), a[1] + (1 - g) / 2 * (ay + by)) for a in
                              [(p[0], p[1]), (p[0] + ax * g, p[1] + ay * g), (p[0] + (ax + bx) * g, p[1] + (ay + by) * g),
                               (p[0] + bx * g, p[1] + by * g)]), c["empty"], "f", delay))
            continue
        h = 3 + (n / peak) ** 0.5 * top
        qn = n / peak
        col = lv[0] if qn < .15 else lv[1] if qn < .4 else lv[2] if qn < .75 else lv[3]
        lift = lambda a: (a[0], a[1] - h)
        out.append(f'<g class="r" style="{delay}">'
                   + quad(u, r, lift(r), lift(u), shade(col, .78))
                   + quad(q, r, lift(r), lift(q), shade(col, .62))
                   + quad(lift(p), lift(q), lift(r), lift(u), col) + "</g>")

    year = [(dd, n) for *_, n, dd in cells if dd > today - timedelta(days=365)]
    total = sum(n for _, n in year)
    best_d, best_n = max(year, key=lambda x: x[1])
    by_month, by_wd = Counter(), Counter()
    for dd, n in year:
        by_month[(dd.year, dd.month)] += n
        by_wd[dd.weekday()] += n
    (by, bm), bm_n = by_month.most_common(1)[0]
    wd_best = by_wd.most_common(1)[0][0]
    weeks = Counter()
    for w, _, n, _ in cells:
        weeks[w] += n
    best_week = max(weeks.values())

    out.append(f'<text x="32" y="48" font-size="18" font-weight="700" fill="{c["text"]}">{t["title"]}</text>')
    rows = [
        (fmt(total, lang), t["total"]),
        (f"{best_n}", f'{t["best_day"]} · {best_d.strftime("%d.%m.%Y")}'),
        (fmt(bm_n, lang), f'{t["best_month"]} · {t["month_full"][bm - 1]} {by}'),
        (fmt(best_week, lang), t["active"]),
    ]
    for i, (big, label) in enumerate(rows):
        y = 104 + i * 62
        out.append(f'<g class="f" style="animation-delay:{0.1 + i * 0.1:.2f}s">'
                   f'<text x="32" y="{y}" font-size="26" font-weight="700" fill="{c["accent"] if i == 0 else c["text"]}">{big}</text>'
                   f'<text x="32" y="{y + 20}" font-size="12" fill="{c["muted"]}">{escape(label)}</text></g>')
    out.append(f'<text x="32" y="{H - 24}" font-size="12" fill="{c["muted"]}">{t["weekdays"]} '
               f'<tspan fill="{c["text"]}" font-weight="600">{t["day_names"][wd_best]}</tspan></text>')
    out.append("</svg>")
    return "\n".join(out)


def recent_block(data, lang):
    t = TEXT[lang]
    base, repos, *_ = data
    now = datetime.now(timezone.utc)
    fresh = [r for r in repos if r["name"] != base["login"] and (now - datetime.fromisoformat(r["pushedAt"].replace("Z", "+00:00"))).days <= 30]
    fresh.sort(key=lambda r: r["pushedAt"], reverse=True)
    public = [r for r in fresh if not r["isPrivate"]][:6]
    private = sum(1 for r in fresh if r["isPrivate"])
    lines = [f"<sub>{t['recent_title']}</sub>", ""]
    for r in public:
        ago = (now - datetime.fromisoformat(r["pushedAt"].replace("Z", "+00:00"))).days
        desc = (r["description"] or "").strip()
        if len(desc) > 100:
            desc = desc[:97].rstrip() + "…"
        lines.append(f"- [**{r['name']}**]({r['url']}) · {t['ago'](ago)}" + (f"<br><sub>{desc}</sub>" if desc else ""))
    if private:
        lines.append(f"- <sub>{t['private_note'].format(n=private)}</sub>")
    return "\n".join(lines)


def replace_block(path, body):
    text = path.read_text(encoding="utf-8")
    new = re.sub(r"(<!--RECENT:START-->).*?(<!--RECENT:END-->)", lambda m: f"{m[1]}\n{body}\n{m[2]}",
                 text, flags=re.S)
    path.write_text(new, encoding="utf-8", newline="\n")


def main():
    data = collect()
    ASSETS.mkdir(exist_ok=True)
    for lang in TEXT:
        for theme in THEMES:
            (ASSETS / f"stats-{lang}-{theme}.svg").write_text(stats_svg(data, lang, theme), encoding="utf-8", newline="\n")
            (ASSETS / f"year-{lang}-{theme}.svg").write_text(iso_svg(data, lang, theme), encoding="utf-8", newline="\n")
    replace_block(ROOT / "README.md", recent_block(data, "ru"))
    replace_block(ROOT / "README.en.md", recent_block(data, "en"))


if __name__ == "__main__":
    main()
