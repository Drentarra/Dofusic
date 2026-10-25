# Contrôle facultatif à exécuter APRÈS BUILD_PORTABLE.bat sur Windows x64.
# N'arrête jamais de force un processus ; un échec laisse les PID pour diagnostic.
param(
    [ValidateRange(1, 30)][int]$Iterations = 10,
    [ValidateRange(5, 90)][int]$TimeoutSeconds = 25
)
$ErrorActionPreference = 'Stop'
$exe = Join-Path $PSScriptRoot 'Release\Dofusic\Dofusic.exe'
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
    throw "Exécutable introuvable. Lancez d'abord BUILD_PORTABLE.bat : $exe"
}
$exe = (Resolve-Path -LiteralPath $exe).Path
$workingDirectory = Split-Path -Parent $exe
$failures = New-Object System.Collections.Generic.List[string]
Write-Host '[1/2] Auto-test du binaire portable et de ses dépendances OCR...'
$selfTest = Start-Process -FilePath $exe -ArgumentList '--self-test' -WorkingDirectory $workingDirectory -PassThru
if (-not $selfTest.WaitForExit(180000)) {
    $failures.Add('Auto-test : délai de 180 s dépassé')
} elseif ($selfTest.ExitCode -ne 0) {
    $failures.Add("Auto-test : code d'erreur $($selfTest.ExitCode)")
}
Write-Host "[2/2] $Iterations cycles de démarrage / fermeture via WM_CLOSE..."
for ($i = 1; $i -le $Iterations; $i++) {
    $proc = Start-Process -FilePath $exe -WorkingDirectory $workingDirectory -PassThru
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        $proc.Refresh()
        if ($proc.HasExited -or $proc.MainWindowHandle -ne [IntPtr]::Zero) { break }
        Start-Sleep -Milliseconds 200
    }
    $proc.Refresh()
    if ($proc.HasExited) {
        $failures.Add("Cycle $i : Dofusic s'est arrêté avant d'ouvrir une fenêtre (PID $($proc.Id))")
        continue
    }
    if ($proc.MainWindowHandle -eq [IntPtr]::Zero) {
        $failures.Add("Cycle $i : fenêtre absente ou figée (PID $($proc.Id))")
        continue
    }
    Start-Sleep -Milliseconds 800
    $children = @(Get-CimInstance -ClassName Win32_Process -Filter "ParentProcessId=$($proc.Id)" |
        Select-Object -ExpandProperty ProcessId)
    if (-not $proc.CloseMainWindow()) {
        $failures.Add("Cycle $i : la fenêtre n'accepte pas WM_CLOSE (PID $($proc.Id))")
        continue
    }
    if (-not $proc.WaitForExit($TimeoutSeconds * 1000)) {
        $failures.Add("Cycle $i : fermeture non terminée (PID $($proc.Id))")
        continue
    }
    Start-Sleep -Milliseconds 1000
    $orphans = @($children | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
    if ($orphans.Count -gt 0) {
        $failures.Add("Cycle $i : processus enfant(s) encore présent(s) : $($orphans -join ', ')")
    }
    Write-Host "  Cycle $i/$Iterations : terminé"
}
if ($failures.Count -gt 0) {
    Write-Host '[ECHEC] Vérification Windows :' -ForegroundColor Red
    foreach ($failure in $failures) { Write-Host "  - $failure" }
    Write-Host 'Les processus restants ne sont pas arrêtés automatiquement afin de préserver vos données.'
    exit 1
}
Write-Host '[OK] Auto-test et cycles de démarrage / fermeture terminés.' -ForegroundColor Green
Write-Host 'À compléter par un test de 1 à 2 h dans le véritable Dofus (CPU, RAM, combats, audio).'
exit 0
