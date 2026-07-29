# Media Monitor — návod

Skript `media_monitor.py` každý deň nazbiera zmienky o zadaných kľúčových slovách
z médií (Google News, GDELT, RSS SK/CZ médií) a Redditu, a pošle ti súhrn emailom.

## 1. Inštalácia

```bash
pip install -r requirements.txt
```

## 2. Nastavenie kľúčových slov

Otvor `media_monitor.py` a uprav na začiatku:

```python
KEYWORDS = ["Orange"]
```

Môžeš pridať aj viac slov naraz, napr. `["Orange", "Orange Slovensko"]`.

Do `MEDIA_RSS_FEEDS` môžeš pridať ďalšie RSS feedy médií, ktoré ťa zaujímajú.

## 3. Nastavenie emailu

Skript číta credentials z premenných prostredia (nedávaj heslo priamo do kódu):

```bash
export SMTP_HOST="smtp.gmail.com"
export SMTP_PORT="587"
export SMTP_USER="tvoj@email.com"
export SMTP_PASS="tvoje_heslo_alebo_app_password"
export EMAIL_FROM="tvoj@email.com"
export EMAIL_TO="komu@posielat.com"
```

Pri Gmaile treba vytvoriť "App Password" (nie bežné heslo účtu) — Google to
vyžaduje pri prihlásení cez SMTP z externých skriptov.

Ak email nenastavíš, skript report iba vypíše do konzoly (na testovanie).

## 4. Manuálne spustenie

```bash
python media_monitor.py
```

## 5. Automatický beh — denne alebo hodinovo

Frekvenciu behu si vyberáš len tým, ako často cron skript spúšťa.
V `media_monitor.py` k tomu nastav `HOURS_BACK` (časové okno hľadania)
a `SEND_ONLY_IF_RESULTS` (či posielať aj "prázdne" emaily):

| Frekvencia | Cron               | HOURS_BACK | SEND_ONLY_IF_RESULTS |
|------------|---------------------|-----------:|:---------------------:|
| Denne      | `0 7 * * *`         | 24         | False (alebo True)    |
| Hodinovo   | `0 * * * *`         | 2          | True                  |

**Cron (Linux/Mac):**

```bash
crontab -e
# denne o 7:00
0 7 * * * cd /cesta/k/projektu && /usr/bin/python3 media_monitor.py

# alebo hodinovo (na začiatku každej hodiny)
0 * * * * cd /cesta/k/projektu && /usr/bin/python3 media_monitor.py
```

**GitHub Actions** (ak chceš bežať bez vlastného servera) — vytvor
`.github/workflows/hourly.yml` s `schedule: cron: "0 * * * *"` a SMTP
credentials ako GitHub Secrets. Pozor: GitHub Actions cron beží na
zdieľanej infraštruktúre a môže mať oneskorenie pár minút — nevadí,
lebo `HOURS_BACK` má rezervu a dedupe (bod 6) rieši prekrývanie.

### Prečo HOURS_BACK = 2, keď beží každú hodinu?

Feedy (najmä RSS médií) majú niekedy oneskorenie alebo nekonzistentný
timestamp. Menšie prekrytie okien (2h namiesto presne 1h) zaručí, že sa
nič nestratí medzi behmi — duplicitné výsledky z prekrytia sa ale aj tak
neodošlú vďaka dedupe pamäti (bod 6).

## 6. Perzistentná dedupe pamäť

Skript si vedľa seba vytvára súbor `seen_items.json`, kde si pamätá ID
(hash URL) už odoslaných výsledkov za posledných `SEEN_RETENTION_HOURS`
(default 72h). Pri každom behu sa odošlú len skutočne nové položky —
takže pri hodinovej frekvencii ti nepríde 24× po sebe ten istý článok.

Ak chceš mať tento súbor zdieľaný medzi viacerými spusteniami (napr. pri
GitHub Actions, kde sa disk medzi behmi nezachováva), treba ho ukladať
mimo runnera — napr. commitovať späť do repa, alebo použiť malú databázu
(SQLite v pripojenom volume, S3, atď.). Pri behu na vlastnom serveri/cron
to funguje bez úprav, lebo súbor jednoducho ostáva na disku.

## 7. Čo skript NEZAHŔŇA a prečo

- **X/Twitter**: od 2/2026 je API výhradne platené (pay-per-use, cca $5
  za 1000 prečítaní + $0.20 navyše za post s URL, alebo enterprise zmluva
  od cca $42 000/mesiac za plný historický search). Dá sa dorobiť, ale
  treba počítať s reálnymi nákladmi za volanie.
- **Facebook/Instagram**: oficiálny nástroj na keyword search verejného
  obsahu (Meta Content Library) je dostupný len schváleným akademickým/
  neziskovým výskumníkom, nie bežným firmám — komerčná cesta by viedla
  cez platené monitoring nástroje (Brand24, Awario a pod.) alebo cez
  scraping, ktorý porušuje podmienky používania Meta.

Ak by si tieto zdroje chcel doplniť, najreálnejšia cesta je platený
tretostranný news/social monitoring nástroj napojený cez API, alebo si
zadefinovať rozpočet a ísť priamo cez oficiálne platené API.

## 8. Rozšírenia, ktoré sa dajú ľahko dorobiť

- Sumarizácia/sentiment cez Claude API (pridať jeden request na koniec
  `collect_all()`, ktorý zhrnie výsledky).
- Ukladanie histórie do CSV/SQLite namiesto len denného emailu.
- Web dashboard namiesto/popri emaile.

## 9. Manuálny Facebook monitoring (pilot — Právo na Pravdu)

Keďže automatizovaný keyword-monitoring Facebooku nie je pre bežné firmy
dostupný (pozri bod 7), toto sú len **priame odkazy na Facebook vyhľadávanie**
pre kľúčové slová monitoringu "Právo na Pravdu" — nič sa nespúšťa samo, treba
ich občas ručne otvoriť a prezrieť:

- [Právo na Pravdu — Top](https://www.facebook.com/search/top?q=Pr%C3%A1vo%20na%20Pravdu) · [len príspevky](https://www.facebook.com/search/posts?q=Pr%C3%A1vo%20na%20Pravdu)
- [Zoroslav Kollár — Top](https://www.facebook.com/search/top?q=Zoroslav%20Koll%C3%A1r) · [len príspevky](https://www.facebook.com/search/posts?q=Zoroslav%20Koll%C3%A1r)

Ak sa tento pilot osvedčí, dá sa rovnaký zoznam odkazov spraviť aj pre
ostatné monitoringy (Orange, Vláda, Primátor Bratislavy).
