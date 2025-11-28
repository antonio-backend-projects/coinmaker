# Coinmaker - Multi-Strategy Trading Bot

Bot di trading algoritmico modulare che supporta diverse strategie su opzioni e futures crypto (BTC/ETH) tramite API Deribit e Binance.

## 🎯 Caratteristiche Principali

- **Architettura Multi-Strategia**: Esegui più strategie contemporaneamente (es. Iron Condor + Smart Money).
- **Strategia Iron Condor**: Short premium con rischio definito su opzioni.
- **Strategia Smart Money**: Trading direzionale basato su flussi "Whale" e caccia alla liquidità.
- **Gestione Rischio Avanzata**: Sizing dinamico con interesse composto.
- **Integrazione Binance**: Usa i volumi spot di Binance come segnale per i futures Deribit.
- **API-Friendly**: Integrazione completa con Deribit API e Binance Public API.

## 📋 Requisiti

- Python 3.8+
- Account Deribit (testnet o production)
- Capitale iniziale consigliato: $10,000

## 🚀 Installazione

1. **Clone del repository**
```bash
git clone <repository-url>
cd coinmaker
```

2. **Installa le dipendenze**
```bash
pip install -r requirements.txt
```

3. **Configura le variabili d'ambiente**
```bash
cp .env.example .env
```

Modifica il file `.env` con le tue credenziali Deribit:
```env
DERIBIT_API_KEY=your_api_key_here
DERIBIT_API_SECRET=your_api_secret_here
DERIBIT_ENV=test  # oppure 'prod' per produzione
```

4. **Crea la cartella logs**
```bash
mkdir logs
```

## ⚙️ Configurazione

Parametri chiave nel file `.env`:

### Abilitazione Strategie
| Parametro | Descrizione | Default |
|-----------|-------------|---------|
| `STRATEGY_IRON_CONDOR_ENABLED` | Abilita Iron Condor | true |
| `STRATEGY_SMART_MONEY_ENABLED` | Abilita Smart Money | true |

### Iron Condor
| Parametro | Descrizione | Default |
|-----------|-------------|---------|
| `INITIAL_EQUITY` | Capitale iniziale in USD | 10000 |
| `RISK_PER_CONDOR` | Rischio per condor (% equity) | 0.01 (1%) |
| `MAX_PORTFOLIO_RISK` | Rischio massimo portafoglio | 0.03 (3%) |

### Smart Money
| Parametro | Descrizione | Default |
|-----------|-------------|---------|
| `SM_TIME_WINDOW_START` | Ora inizio (UTC+1) | 14 |
| `SM_TIME_WINDOW_END` | Ora fine (UTC+1) | 17 |
| `SM_WHALE_MIN_VALUE` | Minimo valore trade whale ($) | 500000 |
| `SM_BINANCE_SYMBOL` | Simbolo Binance Spot | BTCUSDT |

## 🎮 Utilizzo

### 🐳 Modalità 1: Docker (Consigliato)

**Il modo più semplice e sicuro per eseguire il bot in un ambiente isolato.**

```bash
# Windows
docker-start.bat

# Linux/Mac
./docker-start.sh
```

**Comandi Docker:**
```bash
docker-compose up -d          # Avvia in background
docker-compose logs -f        # Visualizza logs
docker-compose stop           # Ferma bot
docker-compose restart        # Riavvia bot
docker-compose down           # Rimuovi container
```

📖 **[Leggi la Guida Docker Completa →](DOCKER_GUIDE.md)**

---

### 🐍 Modalità 2: Python Nativo

**Esecuzione diretta senza Docker.**

```bash
python -m src.trading_bot
```

Oppure usa gli script helper:
```bash
# Windows
run.bat

# Linux/Mac
./run.sh
```

Il bot eseguirà:
- **Scan giornaliero**: Alle 10:00 AM cerca nuove opportunità
- **Monitoraggio continuo**: Ogni 5 minuti controlla TP/SL/scadenze

**Per fermare il bot:**
```
Ctrl+C
```

## 📊 Come Funziona

### 1. Strategia Iron Condor (Opzioni)
Il bot costruisce strutture Iron Condor neutrali (Delta Neutral) vendendo volatilità.
- **Entrata**: Quando IV è alta (>30 percentile).
- **Struttura**: Short Put + Short Call (Delta 0.12) con ali protettive.
- **Uscita**: TP al 55% del credito o SL al 120%.

### 2. Strategia Smart Money (Futures/Perps)
Il bot cerca confluenza tra 3 fattori per trade direzionali intraday:
1.  **Time**: Solo durante l'overlap Londra/New York (14:00-17:00).
2.  **Whale Volume**: Analizza i trade su Binance Spot > $500k. Se i compratori aggressivi dominano -> Bullish.
3.  **Liquidity Sweep**: Cerca pattern di "caccia agli stop" (prezzo rompe un minimo ma chiude sopra).

**Segnale**: Se (Whale Bullish AND Liquidity Sweep Long) -> BUY Signal.

### 2. Selezione delle Posizioni

Il bot apre un condor solo se:
- ✅ L'IV è sopra il percentile minimo (default 30%)
- ✅ C'è una scadenza tra 7-10 giorni disponibile
- ✅ Il rischio totale è sotto il limite (3% equity)
- ✅ Trova strike con delta appropriati

### 3. Gestione delle Posizioni

Ogni condor viene chiuso automaticamente quando:
- **Take Profit**: Profitto ≥ 55% del credito incassato
- **Stop Loss**: Perdita ≥ 120% del credito incassato
- **Scadenza**: 24 ore prima della scadenza (evita gamma risk)

### 4. Risk Management & Compounding

- Ogni condor rischia **1% dell'equity corrente**
- Massimo rischio portafoglio: **3% dell'equity**
- Size ricalcolata automaticamente ad ogni nuovo trade ➔ **interesse composto**

## 📁 Struttura del Progetto

```
coinmaker/
├── src/
│   ├── core/
│   │   ├── deribit_client.py      # Client API Deribit
│   │   ├── order_manager.py       # Gestione ordini multi-leg
│   │   ├── position_monitor.py    # Monitoraggio P&L e TP/SL
│   │   └── risk_manager.py        # Gestione rischio e sizing
│   ├── strategies/
│   │   └── iron_condor.py         # Costruzione Iron Condor
│   ├── utils/
│   │   └── volatility.py          # Analisi volatilità e IV rank
│   └── trading_bot.py             # Bot principale
├── logs/                          # File di log
├── data/                          # Dati storici
├── .env                           # Configurazione (non committare!)
├── .env.example                   # Template configurazione
├── requirements.txt               # Dipendenze Python
└── README.md                      # Questo file
```

## 🛡️ Sicurezza & Best Practices

### Testnet Prima di Prod

**IMPORTANTE**: Usa sempre il testnet prima di andare in produzione!

1. Crea un account su [test.deribit.com](https://test.deribit.com)
2. Genera API keys per testnet
3. Imposta `DERIBIT_ENV=test` nel file `.env`
4. Testa il bot per almeno 1-2 settimane

### Monitoraggio

- Controlla i log in `logs/trading_bot.log`
- Verifica regolarmente le posizioni aperte su Deribit
- Monitora l'equity e il P&L

### Gestione del Rischio

- **Non modificare i parametri di rischio senza esperienza**
- Inizia con capitale ridotto anche in prod
- Il bot può avere drawdown, è normale per short premium
- In caso di problemi: premi Ctrl+C per fermare

## 📈 Esempio di Trade

```
Currency: BTC
Spot: $60,000
Expiration: 8 giorni

Strike Selection:
- Long Put:  $48,000 (protezione)
- Short Put: $54,000 (venduta, delta -0.12)
- Short Call: $66,000 (venduta, delta +0.12)
- Long Call: $72,000 (protezione)

Credito Ricevuto: $200
Max Loss: $500
Size: 0.2 contratti (per rischiare $100 = 1% di $10k)

Take Profit: $110 (55% di $200)
Stop Loss: -$240 (1.2x $200)
```

## 🔧 Utilities e Test

### Script di Test

Crea script per testare singoli componenti:

```python
# test_connection.py
from src.core.deribit_client import DeribitClient
from dotenv import load_dotenv
import os

load_dotenv()
client = DeribitClient(
    os.getenv("DERIBIT_API_KEY"),
    os.getenv("DERIBIT_API_SECRET"),
    "test"
)

if client.authenticate():
    print("✓ Autenticazione riuscita")
    btc_price = client.get_index_price("BTC")
    print(f"BTC Price: ${btc_price:,.2f}")
else:
    print("✗ Autenticazione fallita")
```

## 📊 Metriche & Performance

Il bot logga automaticamente:
- Equity corrente vs iniziale
- P&L per trade (TP, SL, motivo chiusura)
- Utilizzo del rischio (% del massimo)
- IV rank al momento dell'entrata
- Strike selezionati e delta effettivi

## ⚠️ Disclaimer

Questo bot è fornito **solo a scopo educativo e di ricerca**.

- Il trading di opzioni comporta **rischio di perdita del capitale**
- Le performance passate non garantiscono risultati futuri
- Short volatility ha **tail risk** (eventi estremi)
- Usa solo capitale che puoi permetterti di perdere
- Non è un consiglio finanziario

L'autore non si assume responsabilità per perdite derivanti dall'uso di questo software.

## 🤝 Contributi

Contributi, bug reports e feature requests sono benvenuti!

## 📄 Licenza

MIT License - vedi LICENSE file per dettagli

## 📚 Risorse Utili

- [Deribit API Documentation](https://docs.deribit.com/)
- [Deribit Testnet](https://test.deribit.com)
- [Options Greeks Explained](https://www.investopedia.com/options-greeks-4427781)
- [Iron Condor Strategy](https://www.investopedia.com/terms/i/ironcondor.asp)

---

**Buon Trading! 🚀**

*Ricorda: usa sempre il testnet per i primi test, monitora costantemente le posizioni, e rispetta il risk management.*
