# Ставит/обновляет WSL-дистрибутив jenkins-agent — Jenkins-агент pc на этом ПК
# (см. README.md). Повторный запуск безопасен: дистрибутив не пересоздаётся, provision.sh
# и секрет применяются заново, агент перезапускается.
#
#   powershell -ExecutionPolicy Bypass -File tools\jenkins-agent\setup.ps1
param(
  [string]$Distro = 'jenkins-agent',
  # Куда положить VHDX дистрибутива; пусто — каталог WSL по умолчанию.
  [string]$Location = ''
)
$ErrorActionPreference = 'Stop'
$env:WSL_UTF8 = '1'  # иначе wsl.exe -l печатает UTF-16 и имена не сравнить
$TaskName = 'jenkins-agent (WSL)'

$secretFile = Join-Path $PSScriptRoot 'secret'
if (-not (Test-Path $secretFile)) {
  throw "Нет $secretFile — скопируй туда секрет ноды pc со страницы ноды в Jenkins"
}

# Файл уходит в дистрибутив через stdin (cmd-редирект передаёт байты как есть):
# дисков Windows внутри нет, /mnt/c недоступен.
function Invoke-WithStdin([string]$File, [string]$Command) {
  cmd.exe /c "wsl.exe -d $Distro -u root -e sh -c `"$Command`" < `"$File`""
  if ($LASTEXITCODE) { throw "wsl: '$Command' завершился с кодом $LASTEXITCODE" }
}

$installed = wsl.exe -l -q | ForEach-Object { $_.Trim() } | Where-Object { $_ }
if ($installed -notcontains $Distro) {
  $installArgs = @('--install', 'Ubuntu-24.04', '--name', $Distro, '--no-launch', '--web-download')
  if ($Location) { $installArgs += @('--location', $Location) }
  wsl.exe @installArgs
  if ($LASTEXITCODE) { throw "wsl --install завершился с кодом $LASTEXITCODE" }
  # Разреженный VHDX отдаёт место Windows после docker prune / GC кэша.
  wsl.exe --manage $Distro --set-sparse true
}

Invoke-WithStdin (Join-Path $PSScriptRoot 'provision.sh') "tr -d '\r' > /root/provision.sh && bash /root/provision.sh"
Invoke-WithStdin $secretFile "umask 027 && tr -d '\r\n' > /etc/jenkins-agent/secret && chown root:jenkins /etc/jenkins-agent/secret"

# Перезапуск применяет wsl.conf (systemd, без дисков и interop).
wsl.exe --terminate $Distro | Out-Null

# Без живого процесса WSL гасит дистрибутив через несколько секунд, а с ним docker
# и агента. Задача при входе в Windows держит его запущенным; conhost --headless —
# чтобы не висело консольное окно.
$action = New-ScheduledTaskAction -Execute 'conhost.exe' -Argument "--headless wsl.exe -d $Distro -u root -e sleep infinity"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName $TaskName

# Агенту нужно несколько секунд: systemd, dockerd, скачать agent.jar, подключиться.
Start-Sleep -Seconds 20
wsl.exe -d $Distro -u root -e systemctl --no-pager --lines=15 status jenkins-agent
