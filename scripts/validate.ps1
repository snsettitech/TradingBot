Write-Host "🚧 Starting Local Validation..." -ForegroundColor Yellow

$env:PYTHONUTF8 = 1

Write-Host "`n🔍 Running Ruff Lint..." -ForegroundColor Cyan
python -m ruff check src/tsxbot tests/
if ($LASTEXITCODE -ne 0) { Write-Error "Lint failed"; exit 1 }

Write-Host "`n🎨 Running Ruff Format Check..." -ForegroundColor Cyan
python -m ruff format --check src/tsxbot tests/
if ($LASTEXITCODE -ne 0) { Write-Error "Format failed"; exit 1 }

Write-Host "`nTypes Running Mypy..." -ForegroundColor Cyan
python -m mypy src/tsxbot
if ($LASTEXITCODE -ne 0) { Write-Error "Type check failed"; exit 1 }

Write-Host "`n🧪 Running Tests..." -ForegroundColor Cyan
python -m pytest tests/ -v -m "not integration"
if ($LASTEXITCODE -ne 0) { Write-Error "Tests failed"; exit 1 }

Write-Host "`nAll checks passed! Ready for PR." -ForegroundColor Green
