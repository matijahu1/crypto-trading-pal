# crypto-trading-pal

A Python-based trading assistant designed for cryptocurrency futures trading on the **Bybit** exchange. This tool streamlines the process of fetching account data, managing positions, and exporting trading activity for further analysis.

---

## 📄 Project Description

`crypto-trading-pal` serves as a bridge between the Bybit API and your local data analysis workflow. Currently, it provides an interactive CLI for real-time inspection and a batch processing engine for exporting account snapshots to CSV.

The **CLI** is primarily intended for quick testing and public data exploration. The core roadmap and all upcoming advanced features—such as PnL calculations and history tracking—are focused exclusively on the **Batch Export** engine to facilitate deep data analysis.

---

## ✨ Features

### 1. CSV Export (Batch Mode)
Automate data collection for external analysis.
* Exports balance data to `data/balance.csv`.
* Exports active futures positions to `data/futures_positions.csv`.
* Exports historical trade data to `data/{SYMBOL}_tradeHistory.csv`.
* Exports order history to `data/{SYMBOL}_orderHistory.csv`.
    * *Smart Update:* Automatically detects existing records in the CSV and only adds new "Filled" orders to avoid duplicates.
* Exports recent trade executions to `data/{SYMBOL}_executions.csv`.
* Exports open orders data to `data/{SYMBOL}_open_orders.csv`.
* Generate report with closed and open lots based on LIFO logic including Profit and Loss to `data/{SYMBOL}_lifo_inventory.csv`.

### 2. Interactive CLI (Testing & Exploration)
Used for quick connectivity checks and market snapshots.
* `balance`: View account equity (requires API key).
* `show <symbol>`: Displays the **last 8 funding rates** for a specific pair. 
    * *Note: `show` do not require an API key.*

### 3. Bybit Integration
* Powered by the `pybit` SDK for robust API communication.

---

## ⚙️ Configuration

To ensure the application runs correctly and your data remains private, follow these configuration steps:

### 1. Prepare Data Folder
Create a directory named `/data` in the project root. This folder will store your exported CSV files and your local configuration.

### 2. Config Setup
1. Locate `config.json.example` in the project root.
2. Copy this file into your newly created `/data` folder.
3. Rename the copy to `config.json`.
4. **Customize Actions:** Open `config.json` and remove any actions you do not currently need. The following actions are available:
   * `"balances"`
   * `"futures_positions"`
   * `"trade_history"`
   * `"order_history"`
   * `"recent_executions"`
   * `"generate_lifo_report"`
   * `"open_orders"`   

> [!NOTE]
> These actions are currently functional but are considered "in-progress." Feel free to test which data exports work best for your needs and provide feedback via GitHub Issues.

### 3. Environment Secrets
1. Copy the `.env.example` file from the root directory to a new file named `.env`.
2. Open the `.env` file and enter your Bybit API Key and Secret.
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
