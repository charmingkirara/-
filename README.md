# USD/JPY 15分足 自動売買ボット

OANDAを使ったUSD/JPYの15分足自動売買ツールです。

## 戦略

エントリー条件（**3つ全て揃ったところ**でエントリー）:

| 条件 | ロング | ショート |
|------|--------|---------|
| ① **3MA** | 価格がMAの上・MAが上昇 | 価格がMAの下・MAが下降 |
| ② **波形** | 高値・安値が切り上がり（上昇トレンド） | 高値・安値が切り下がり（下降トレンド） |
| ③ **RSI** | 50超（強気ゾーン） | 50未満（弱気ゾーン） |

### 徹底事項

- **夜中はエントリーしない**（22:00–07:00 JST = 13:00–22:00 UTC）
- **シグナルだけで入らない**（3条件必須）
- **よくわからない根拠で入らない**（レンジ相場はスキップ）
- **損切りの根拠を持たせる**（20pips / 直近スイング高値・安値）

### エントリーの考え方

```
波形 → 高値・安値更新 → 2点間の線をひく → 並行移動
  → 水平線 → シナリオ → シグナル（3条件） → エントリー
```

---

## セットアップ

### 1. 依存パッケージのインストール

```bash
pip install -r requirements.txt
```

### 2. OANDA APIキーの設定

`.env.example` を `.env` にコピーして編集:

```bash
cp .env.example .env
```

```
OANDA_API_KEY=your_api_key_here
OANDA_ACCOUNT_ID=your_account_id_here
OANDA_ENVIRONMENT=practice   # practice or live
```

---

## 使い方

### ライブ稼働（実際の注文あり）

```bash
python bot.py
```

### ドライラン（シグナル確認のみ、注文なし）

```bash
python bot.py --dry-run
```

### バックテスト（過去データでシグナル検証）

```bash
python backtest.py --count 500
```

---

## Windowsタスクスケジューラ設定（ログオン不要で自動起動）

PCを再起動してもボットを自動で起動し、**ログオンしていない状態でもバックグラウンドで動作**させるには `setup_scheduler.ps1` を使います。

### 手順

1. **PowerShellを管理者として実行**（スタートメニューで `powershell` を右クリック → 管理者として実行）

2. **スクリプトを実行**

   ```powershell
   cd C:\path\to\このフォルダ
   .\setup_scheduler.ps1
   ```

3. **Windowsパスワードを入力**
   - 「ログオンの有無にかかわらず実行」にするためにWindowsアカウントのパスワードが必要です
   - Microsoftアカウントでサインインしている場合は、先に**ローカルアカウントのパスワード**を設定してください（設定 → アカウント → サインインオプション）

4. **登録確認**
   - タスクスケジューラ（`taskschd.msc`）を開いて `USDJPYTradingBot` が登録されているか確認

### オプション

| オプション | 説明 | デフォルト |
|----------|------|---------|
| `-TaskName` | タスク名 | `USDJPYTradingBot` |
| `-BotDir` | bot.py があるフォルダ | スクリプトと同じフォルダ |
| `-PythonPath` | python.exe のパス | 自動検出 |
| `-Username` | 実行ユーザー | 現在のユーザー |
| `-DryRun` | 登録せずに設定内容を確認 | — |

```powershell
# 例: 設定内容を確認してから登録
.\setup_scheduler.ps1 -DryRun

# 例: フォルダとPythonパスを明示指定
.\setup_scheduler.ps1 -BotDir "C:\bots\usdjpy" -PythonPath "C:\Python312\python.exe"
```

### タスクの管理

```powershell
# 今すぐ起動
Start-ScheduledTask -TaskName "USDJPYTradingBot"

# 停止
Stop-ScheduledTask -TaskName "USDJPYTradingBot"

# 削除
Unregister-ScheduledTask -TaskName "USDJPYTradingBot"
```

---

## ファイル構成

```
├── bot.py                   # メインボット（エントリーポイント）
├── backtest.py              # 過去データでの検証ツール
├── setup_scheduler.ps1      # Windowsタスクスケジューラ設定スクリプト
├── requirements.txt
├── .env.example
└── trading_bot/
    ├── config.py            # 設定（パラメータ一覧）
    ├── indicators.py        # テクニカル指標（3MA, RSI, 波形検出）
    ├── strategy.py          # 売買ロジック・フィルター
    └── broker.py            # OANDA API連携
```

---

## 設定パラメータ（config.py）

| パラメータ | デフォルト | 説明 |
|-----------|-----------|------|
| `MA_PERIOD` | 3 | MAの期間 |
| `RSI_PERIOD` | 14 | RSIの期間 |
| `RSI_MID` | 50 | RSI中央値（ロング/ショート判定） |
| `WAVE_LOOKBACK` | 5 | スイング検出の参照期間 |
| `STOP_LOSS_PIPS` | 20 | ストップロス（pips） |
| `TAKE_PROFIT_PIPS` | 40 | テイクプロフィット（pips） |
| `UNITS` | 1000 | 取引単位 |
| `POLL_INTERVAL_SECONDS` | 60 | チェック間隔（秒） |

---

## 注意事項

- **本ツールは教育・研究目的のサンプルです。実際のトレードは自己責任で行ってください。**
- まず必ず `practice`（デモ口座）で動作確認してください。
- 相場環境によってはシグナルが出ない期間もあります（レンジ相場は意図的にスキップ）。
