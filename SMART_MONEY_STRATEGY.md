# Smart Money Strategy 🐋

Questa strategia è progettata per seguire i flussi del "denaro intelligente" (Smart Money) e sfruttare le manipolazioni di mercato intraday.

## 🧠 La Logica

La strategia si basa sulla confluenza di 3 fattori chiave. Il bot entra in posizione SOLO se tutti e 3 i semafori sono verdi.

### 1. Time Window (Il "Quando")
**Obiettivo**: Operare solo quando c'è massima volatilità e liquidità.
- **Finestra**: Overlap Londra / New York.
- **Orario Default**: 14:00 - 17:00 (Ora Italiana/CET).
- **Perché**: È il momento in cui le banche centrali e gli hedge fund muovono i volumi maggiori. Fuori da questo orario, i segnali sono spesso falsi.

### 2. Binance Whale Volume (Il "Chi")
**Obiettivo**: Capire cosa stanno facendo le "balene" in tempo reale.
- **Fonte Dati**: Binance Spot Market (API Pubbliche).
- **Filtro**: Consideriamo solo trade singoli > $500.000 (o valore configurabile).
- **Calcolo**:
    - `Taker Buy Volume`: Volume di chi compra aggressivamente (a market).
    - `Taker Sell Volume`: Volume di chi vende aggressivamente.
    - `Net Flow` = Buy Vol - Sell Vol.
- **Segnale**:
    - **BULLISH**: Se Net Flow > Soglia (es. +$1M).
    - **BEARISH**: Se Net Flow < Soglia (es. -$1M).

### 3. Liquidity Hunter (Il "Dove")
**Obiettivo**: Entrare dopo che gli stop loss dei retail sono stati cacciati.
- **Pattern**: "Sweep & Reclaim".
- **Bullish Sweep**:
    1. Il prezzo scende sotto un minimo recente (Low < PrevLow).
    2. Ma la candela chiude SOPRA quel minimo (Close > PrevLow).
    3. Significa che la discesa era una trappola per prendere liquidità.
- **Bearish Sweep**:
    1. Il prezzo sale sopra un massimo recente.
    2. Ma chiude SOTTO quel massimo.

---

## 🛠️ Configurazione

Aggiungi queste variabili al tuo file `.env`:

```bash
# Abilita la strategia
STRATEGY_SMART_MONEY_ENABLED=true

# Orari (UTC+1 se il server è in Italia/Europa)
SM_TIME_WINDOW_START=14
SM_TIME_WINDOW_END=17

# Filtro Balene
SM_WHALE_MIN_VALUE=500000  # Considera solo trade > $500k
SM_BINANCE_SYMBOL=BTCUSDT  # Simbolo su Binance Spot

# Analisi Tecnica
# Numero di candele passate per cercare minimi/massimi
SM_LIQUIDITY_LOOKBACK=20
```

## 📊 Esempio di Trade

**Scenario Bullish:**
1.  **Ore 15:30**: Apre Wall Street. Siamo nella finestra temporale.
2.  **Binance**: Vediamo 3 trade da $2M l'uno in acquisto su BTC. Il Net Flow è fortemente positivo. -> **Bias BULLISH**.
3.  **Grafico**: BTC scende rapidamente a $59.800 (sotto il minimo di $60.000), ma alle 15:45 chiude la candela a $60.100. -> **Bullish Sweep**.
4.  **Azione**: Il bot apre un **LONG** su BTC-PERPETUAL.

---

## ⚠️ Rischi e Note

- **Slippage**: Operare sui breakout o sweep può comportare slippage.
- **Falsi Segnali**: A volte le balene comprano spot per vendere futures (hedging). Il volume spot da solo non è garanzia al 100%.
- **High Frequency**: Questa strategia opera su timeframe veloci (15m). Assicurati di avere una connessione stabile.
