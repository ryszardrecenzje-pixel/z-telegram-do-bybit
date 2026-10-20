# Telegram Signal Trader

Aplikacja, która nasłuchuje sygnałów z Twojego prywatnego bota Telegram (np. **TraderRicziV2**) za pomocą **Telethon (userbot)** i automatycznie wykonuje transakcje na koncie **Paper (demo)** lub Binance.

Zaprojektowana pod:
- sygnały w formacie Twojego bota (`[PAPER] SIGNAL BUY / CLOSE`)
- ochronę sekretów (Streamlit secrets / `.env`)
- łatwe wrzucenie na GitHub + uruchomienie lokalnie lub na VPS
- panel kontrolny w Streamlit

---

## Szybki start (lokalnie)

### 1. Sklonuj / skopiuj projekt

```bash
cd telegram-signal-trader
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Ustaw sekrety

**Opcja A – plik `.env` (najwygodniejsze lokalnie)**

```bash
cp .env.example .env
# edytuj .env
```

**Opcja B – Streamlit secrets**

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# edytuj secrets.toml
```

Przykład `secrets.toml` (zagnieżdżony – zalecany):

```toml
[telegram]
api_id = 31647829
api_hash = "twój_hash_z_my.telegram.org"
phone = "+48514934698"
signal_bot_username = "TraderRicziV2"

[binance]
api_key = "..."
api_secret = "..."
testnet = true

[risk]
position_size_pct = 10.0
sl_pct = 1.0
tp_pct = 3.0
invert_signal = true
```

| Klucz | Skąd wziąć |
|-------|------------|
| `api_id` / `api_hash` | https://my.telegram.org/apps |
| `phone` | numer w formacie `+48...` |
| `signal_bot_username` | `TraderRicziV2` (bez @) |

### Strategia (domyślna)

- Wielkość pozycji = **10%** dostępnego kapitału
- Sygnał jest **odwracany**: signal LONG → otwieramy **SHORT** (i odwrotnie)
- Stały **SL = 1%**, **TP = 3%** od ceny wejścia (SL/TP z sygnału są ignorowane)

### 3. Pierwsze logowanie Telethon

Przy pierwszym uruchomieniu Telethon poprosi o kod z SMS / aplikacji Telegram.  
Sesja zapisze się w `data/telethon.session` – nie commituj tego pliku.

```bash
streamlit run app.py
```

W panelu kliknij **Start bota**. W terminalu pojawi się prośba o kod – wpisz go.

### 4. Działanie

1. Twój bot **TraderRicziV2** wysyła sygnał.
2. Telethon (zalogowany Twoim kontem) odbiera wiadomość.
3. Parser rozpoznaje `SIGNAL BUY` / `SIGNAL CLOSE`.
4. Risk Manager liczy wielkość pozycji.
5. PaperBroker otwiera/zamyka pozycję w lokalnej bazie SQLite.
6. Wszystko widać na żywo w zakładkach Streamlit.

---

## Format sygnałów (obsługiwany)

```
[PAPER] SIGNAL BUY
WMT LONG
price=119.269997 score=76
SL=118.472333 TP=120.757514
LONG | S=76 | Meta=0.65 | range | MTF=bullish
```

```
[PAPER] SIGNAL CLOSE
NOW LONG
price=149.639999 score=80
SL=-- TP=--
Odwrócenie long RSI+MACD
```

Parser jest elastyczny – jeśli format się zmieni, wystarczy poprawić `bot/signal_parser.py`.

---

## Struktura projektu

```
telegram-signal-trader/
├── app.py                  # Streamlit UI
├── config.py               # ładowanie sekretów
├── bot/
│   ├── telegram_listener.py
│   ├── signal_parser.py
│   ├── risk_manager.py
│   ├── executor.py
│   └── brokers/
│       ├── paper_broker.py   ← domyślny tryb demo
│       └── binance_broker.py
├── data/                   # baza + sesja Telethon (gitignored)
├── .streamlit/secrets.toml.example
├── .env.example
└── requirements.txt
```

---

## Ważne uwagi

### XTB
Oficjalne API XTB zostało wyłączone 14 marca 2025. Nie ma już stabilnego, oficjalnego sposobu automatyzacji. Dlatego w projekcie jest tylko Paper + Binance.

### Binance a akcje (WMT, BA, NOW…)
Klasyczny Binance Spot **nie handluje** zwykłymi akcjami US. Tokenizowane wersje istnieją tylko w wybranych regionach.  
Dla sygnałów na akcje **zawsze używaj Paper (Demo)**.

### Bezpieczeństwo
- Nigdy nie commituj `.env`, `secrets.toml`, plików `*.session`.
- Na Streamlit Cloud sekrety ustawiaj w dashboardzie aplikacji.
- Paper mode nie łączy się z żadnym prawdziwym kontem brokerskim.

### Uruchomienie 24/7
Streamlit Cloud nie jest idealny do długo działających background tasków.  
Najlepsze rozwiązanie: VPS (Hetzner / Contabo) + `screen` / `tmux` / systemd:

```bash
screen -S trader
streamlit run app.py --server.port 8501
# Ctrl+A, D – odłącz
```

---

## Rozwój

- Chcesz dodać Futures Binance → rozszerz `binance_broker.py` (`defaultType: future`).
- Inny format sygnału → edytuj `signal_parser.py`.
- Alerty na Telegrama po wykonaniu transakcji → łatwo dodać w `executor.py`.

Powodzenia i handluj ostrożnie – nawet na paperze warto najpierw obserwować, jak bot reaguje na sygnały.
