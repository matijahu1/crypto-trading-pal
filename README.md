# crypto-trading-pal

A Python-based trading assistant designed for cryptocurrency futures trading on the **Bybit** exchange. This tool streamlines the process of fetching account data, managing positions, and exporting trading activity for further analysis.

---

## ⚠️ Disclaimer

**Risk Warning:** Cryptocurrency trading, particularly futures and derivatives, carries a high level of risk and may not be suitable for all investors. You may lose more than your initial investment.

**No Responsibility:** This software is provided "as is" for educational and personal use. The author(s) and contributors take **no responsibility** for any financial losses, technical bugs, or account-related issues arising from the use of this tool. Use at your own risk.

---

## 📄 Project Description

`crypto-trading-pal` serves as a bridge between the Bybit API and your local data analysis workflow. Currently, it provides an interactive CLI for real-time inspection and a batch processing engine for exporting account snapshots to CSV.

The project is designed to evolve into a comprehensive analysis suite, with upcoming support for:
* Detailed trade history processing.
* Real-time position tracking.
* LIFO-based (Last-In, First-Out) profit and loss calculations.

---

## ✨ Features

### 1. Interactive CLI
Perform quick inspections and verify API connectivity through a dedicated command-line interface.
* **Balance:** View current account equity and asset distribution.
* **Show:** Inspect active futures positions.

### 2. CSV Export (Batch Mode)
Automate data collection for external analysis.
* Exports balance data to `data/balance.csv`.
* Exports active futures positions to `data/futures_positions.csv`.
* **Smart I/O:** Automatically manages the `/data` directory and ensures exports are current.

### 3. Bybit Integration
* Powered by the `pybit` SDK for robust API communication.
* Secure credential management using `python-dotenv`.

---

## 📂 Project Structure

The project follows a clean, modular architecture:

```text
crypto-trading-pal/
├── clients/          # API client wrappers and Bybit SDK initialization
├── commands/         # Logic for CLI commands
├── exporters/        # CSV generation and file handling logic
├── services/         # Business logic and data processing
├── tests/            # Unit and integration tests (pytest)
├── data/             # Local storage for CSV exports (git-ignored)
├── cli.py            # Entry point for interactive mode
├── main.py           # Entry point for batch export mode
├── .env              # Private API credentials (never committed)
├── .env.example      # Template for environment variables
└── requirements.txt  # Project dependencies
