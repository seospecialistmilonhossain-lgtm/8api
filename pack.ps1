$source = 'C:\Users\Google11\Desktop\apphub3\backend'
$destination = 'C:\Users\Google11\Desktop\apphub3\backend_5.0_stable.zip'
if (Test-Path $destination) { Remove-Item $destination }
Get-ChildItem -Path $source -Recurse | Where-Object { 
    $_.FullName -notmatch '\\\.venv(\\|$)' -and 
    $_.FullName -notmatch '\\\.git(\\|$)' -and 
    $_.FullName -notmatch '\\__pycache__(\\|$)' -and 
    $_.FullName -notmatch '\\\.idea(\\|$)' 
} | Compress-Archive -DestinationPath $destination
Write-Host "Zipping completed successfully."
