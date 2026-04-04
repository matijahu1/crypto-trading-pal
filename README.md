# crypto-trading-pal

A Python-based trading assistant designed for cryptocurrency futures trading on the **Bybit** exchange. This tool streamlines the process of fetching account data, managing positions, and exporting trading activity for further analysis.

---

## 📄 Project Description

`crypto-trading-pal` serves as a bridge between the Bybit API and your local data analysis workflow. Currently, it provides an interactive CLI for real-time inspection and a batch processing engine for exporting account snapshots to CSV.

The **CLI** is primarily intended for quick testing and public data exploration. The core roadmap and all upcoming advanced features—such as PnL calculations and history tracking—are focused exclusively on the **Batch Export** engine to facilitate deep data analysis.

**Upcoming Features (Batch Export Only):**
* Trade history 
* LIFO-based (Last-In, First-Out) profit and loss calculations

---

## ✨ Features

### 1. Interactive CLI (Testing & Exploration)
Used for quick connectivity checks and market snapshots.
* `balance`: View account equity (requires API key).
* `show <symbol>`: Displays the **last 8 funding rates** for a specific pair. 
    * *Note: `show` do not require an API key.*

### 2. CSV Export (Batch Mode)
Automate data collection for external analysis.
* Exports balance data to `data/balance.csv`.
* Exports active futures positions to `data/futures_positions.csv`.

### 3. Bybit Integration
* Powered by the `pybit` SDK for robust API communication.
* Secure credential management using `python-dotenv`.

---

## ⚙️ Setup & Installation

### Configuration
1. **Prepare Data Folder:** Create a '/data' subfolder in the project root.
2. **Config Setup:** Copy `config.json.example` into your new `/data` folder and rename it to `config.json`.
Adjust logging levels and folder paths if necessary.
3. **Environment Secrets:**
Copy .env.example from the root directory to a new file named .env.
Open .env and enter your Bybit API Key and Secret.
Note: Never commit your .env file to version control.

---

## ⚠️ Disclaimer

**Risk Warning:** Cryptocurrency trading, particularly futures and derivatives, carries a high level of risk and may not be suitable for all investors. You may lose more than your initial investment.

**No Responsibility:** This software is provided "as is" for educational and personal use. The author(s) and contributors take **no responsibility** for any financial losses, technical bugs, or account-related issues arising from the use of this tool. Use at your own risk.

---

## 🔒 Security Notes

Credential Handling: Never commit your .env file. It is included in .gitignore to prevent accidental leaks of your API keys.

Minimal Permissions: When creating Bybit API keys, use Read-Only permissions for data analysis. If using trading features, ensure keys have no withdrawal permissions.

Local Data Only: The data/ directory is excluded from version control to ensure your financial history remains private and local to your machine.