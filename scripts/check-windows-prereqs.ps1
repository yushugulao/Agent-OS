$ErrorActionPreference = "Continue"

function Report-Ok($name, $detail) {
    Write-Host "[windows-deps] ok: $name $detail"
}

function Report-Missing($name, $detail) {
    Write-Host "[windows-deps] missing: $name $detail"
    $script:Missing = $true
}

$script:Missing = $false

$wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
if ($null -eq $wsl) {
    Report-Missing "wsl.exe" "Install WSL2 and an Ubuntu distribution."
    exit 1
}
Report-Ok "wsl.exe" $wsl.Source

$distros = (& wsl.exe -l -q 2>$null) | ForEach-Object { ($_ -replace "`0", "").Trim() } | Where-Object { $_.Length -gt 0 }
if ($distros.Count -eq 0) {
    Report-Missing "WSL distribution" "Install Ubuntu with: wsl --install -d Ubuntu"
    exit 1
}
Report-Ok "WSL distribution" ($distros -join ",")

$checks = @(
    "bash",
    "git",
    "make",
    "python3",
    "qemu-system-riscv64",
    "riscv64-linux-gnu-gcc",
    "riscv64-linux-gnu-ld",
    "riscv64-linux-gnu-objcopy",
    "riscv64-linux-gnu-objdump"
)

foreach ($cmd in $checks) {
    & wsl.exe bash -lc "command -v $cmd >/dev/null 2>&1" 2>$null
    if ($LASTEXITCODE -eq 0) {
        $path = (& wsl.exe bash -lc "command -v $cmd" 2>$null).Trim()
        Report-Ok $cmd $path
    } else {
        Report-Missing $cmd "not found inside WSL"
    }
}

$pythonModules = @("pandas", "seaborn", "matplotlib")
foreach ($module in $pythonModules) {
    & wsl.exe bash -lc "python3 -c 'import $module' >/dev/null 2>&1" 2>$null
    if ($LASTEXITCODE -eq 0) {
        Report-Ok "python module" $module
    } else {
        Write-Host "[windows-deps] recommended missing: python module $module"
    }
}

if ($script:Missing) {
    Write-Host ""
    Write-Host "[windows-deps] Open Ubuntu/WSL and run:"
    Write-Host "sudo apt update"
    Write-Host "sudo apt install -y git build-essential make python3 qemu-system-misc gcc-riscv64-linux-gnu binutils-riscv64-linux-gnu python3-pandas python3-seaborn python3-matplotlib"
    exit 1
}

Write-Host "[windows-deps] ready"
