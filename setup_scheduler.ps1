# ============================================================
# Windows タスクスケジューラ 設定スクリプト
# 「ログオンの有無にかかわらず実行」に設定します
# ============================================================
# 管理者権限で実行してください:
#   右クリック → "PowerShellとして管理者権限で実行"
# ============================================================

param(
    [string]$TaskName    = "USDJPYTradingBot",
    [string]$BotDir      = $PSScriptRoot,
    [string]$PythonPath  = "",          # 空なら自動検出
    [string]$Username    = "",          # 空なら現在のユーザー
    [switch]$DryRun      = $false
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── 1. 管理者権限チェック ─────────────────────────────────────────────────────
$currentPrincipal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "このスクリプトは管理者権限で実行してください。`n右クリック → 管理者として実行"
    exit 1
}

# ── 2. Pythonパス解決 ──────────────────────────────────────────────────────────
if (-not $PythonPath) {
    $PythonPath = (Get-Command python -ErrorAction SilentlyContinue)?.Source
    if (-not $PythonPath) {
        $PythonPath = (Get-Command python3 -ErrorAction SilentlyContinue)?.Source
    }
    if (-not $PythonPath) {
        Write-Error "Pythonが見つかりません。--PythonPath オプションで指定してください。"
        exit 1
    }
}
Write-Host "Python: $PythonPath"

# ── 3. ボットスクリプトのパス確認 ─────────────────────────────────────────────
$BotScript = Join-Path $BotDir "bot.py"
if (-not (Test-Path $BotScript)) {
    Write-Error "bot.py が見つかりません: $BotScript`n--BotDir オプションで正しいパスを指定してください。"
    exit 1
}
Write-Host "Bot: $BotScript"

# ── 4. 実行ユーザー確認 ────────────────────────────────────────────────────────
if (-not $Username) {
    $Username = "$env:USERDOMAIN\$env:USERNAME"
}
Write-Host "実行ユーザー: $Username"

# ── 5. Windowsパスワード入力（ログオン不要実行に必要）────────────────────────
Write-Host ""
Write-Host "タスクスケジューラで「ログオンの有無にかかわらず実行」に設定するには"
Write-Host "Windowsアカウントのパスワードが必要です。"
Write-Host ""
$SecurePassword = Read-Host "パスワードを入力してください ($Username)" -AsSecureString
$Credential = New-Object System.Management.Automation.PSCredential($Username, $SecurePassword)

# パスワードが空でないか確認
$PlainPassword = $Credential.GetNetworkCredential().Password
if ([string]::IsNullOrEmpty($PlainPassword)) {
    Write-Error "パスワードが空です。Microsoftアカウント利用時はローカルアカウントのパスワードを設定してください。"
    exit 1
}

# ── 6. タスク定義 ─────────────────────────────────────────────────────────────
$Action = New-ScheduledTaskAction `
    -Execute $PythonPath `
    -Argument "bot.py" `
    -WorkingDirectory $BotDir

# PC起動時に開始、その後毎1分チェック（bot.py自身がループするので単純なトリガーでOK）
$Trigger = New-ScheduledTaskTrigger -AtStartup

$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `   # 無制限
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable

$Principal = New-ScheduledTaskPrincipal `
    -UserId $Username `
    -LogonType Password `               # ← ログオン不要実行のキー設定
    -RunLevel Highest

$Task = New-ScheduledTask `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description "USD/JPY 自動売買ボット (ログオン不要実行)"

# ── 7. 登録 or ドライラン ─────────────────────────────────────────────────────
if ($DryRun) {
    Write-Host ""
    Write-Host "[DryRun] 以下の設定でタスクを登録します（実際には登録しません）:"
    Write-Host "  タスク名    : $TaskName"
    Write-Host "  実行ファイル: $PythonPath bot.py"
    Write-Host "  作業ディレクトリ: $BotDir"
    Write-Host "  実行ユーザー: $Username"
    Write-Host "  LogonType   : Password (ログオン不要)"
    Write-Host "  RunLevel    : Highest (管理者相当)"
    exit 0
}

# 既存タスクがあれば削除して再登録
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "既存タスク '$TaskName' を削除して再登録します..."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -InputObject $Task `
    -User $Username `
    -Password $PlainPassword `
    -Force | Out-Null

# ── 8. 結果確認 ───────────────────────────────────────────────────────────────
$registered = Get-ScheduledTask -TaskName $TaskName
Write-Host ""
Write-Host "=== 登録完了 ===" -ForegroundColor Green
Write-Host "タスク名   : $($registered.TaskName)"
Write-Host "状態       : $($registered.State)"
Write-Host "LogonType  : $($registered.Principal.LogonType)"
Write-Host ""
Write-Host "今すぐ起動テスト: Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "タスク削除      : Unregister-ScheduledTask -TaskName '$TaskName'"
