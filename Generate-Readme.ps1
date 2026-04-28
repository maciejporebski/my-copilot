<#
.SYNOPSIS
    Generates README.md from the agents and skills in this repository.
.DESCRIPTION
    Scans the agents/ and skills/ directories, extracts name and description
    from YAML frontmatter, and writes a summary table into README.md.
#>
[CmdletBinding()]
param()

$repoRoot = $PSScriptRoot
$readmePath = Join-Path $repoRoot 'README.md'

function Get-Frontmatter {
    <#
    .SYNOPSIS
        Parses YAML frontmatter (between --- delimiters) and returns a hashtable.
    #>
    param([string]$Content)

    $result = @{}
    if ($Content -match '(?s)^---\r?\n(.*?)\r?\n---') {
        $yamlBlock = $Matches[1]
        foreach ($line in $yamlBlock -split '\r?\n') {
            if ($line -match '^\s*(\w[\w\-]*)\s*:\s*"?(.+?)"?\s*$') {
                $result[$Matches[1]] = $Matches[2]
            }
        }
    }
    return $result
}

# --- Collect agents ---
$agentsDir = Join-Path $repoRoot 'agents'
$agents = @()
if (Test-Path $agentsDir) {
    Get-ChildItem -Path $agentsDir -Filter '*.agent.md' -File | ForEach-Object {
        $fm = Get-Frontmatter -Content (Get-Content $_.FullName -Raw -Encoding utf8)
        $agents += [PSCustomObject]@{
            Name        = if ($fm['name']) { $fm['name'] } else { $_.BaseName -replace '\.agent$', '' }
            Description = if ($fm['description']) { $fm['description'] } else { '—' }
            File        = "agents/$($_.Name)"
        }
    }
}

# --- Collect skills ---
$skillsDir = Join-Path $repoRoot 'skills'
$skills = @()
if (Test-Path $skillsDir) {
    Get-ChildItem -Path $skillsDir -Directory | ForEach-Object {
        $skillMd = Join-Path $_.FullName 'SKILL.md'
        if (Test-Path $skillMd) {
            $fm = Get-Frontmatter -Content (Get-Content $skillMd -Raw -Encoding utf8)
            $rawDesc = if ($fm['description']) { $fm['description'] } else { '—' }
            # Truncate long descriptions (strip WHEN clauses) for the table
            $shortDesc = ($rawDesc -split '\. WHEN:')[0] -replace '\s+$', ''
            $skills += [PSCustomObject]@{
                Name        = if ($fm['name']) { $fm['name'] } else { $_.Name }
                Description = $shortDesc
                Directory   = "skills/$($_.Name)"
            }
        }
    }
}

# --- Build README ---
$sb = [System.Text.StringBuilder]::new()
[void]$sb.AppendLine('# My Copilot')
[void]$sb.AppendLine()
[void]$sb.AppendLine('A collection of custom GitHub Copilot agents and skills.')
[void]$sb.AppendLine()

# Agents table
[void]$sb.AppendLine('## Agents')
[void]$sb.AppendLine()
if ($agents.Count -eq 0) {
    [void]$sb.AppendLine('_No agents found._')
} else {
    [void]$sb.AppendLine('| Name | Description |')
    [void]$sb.AppendLine('|------|-------------|')
    foreach ($a in $agents) {
        [void]$sb.AppendLine("| [$($a.Name)]($($a.File)) | $($a.Description) |")
    }
}
[void]$sb.AppendLine()

# Skills table
[void]$sb.AppendLine('## Skills')
[void]$sb.AppendLine()
if ($skills.Count -eq 0) {
    [void]$sb.AppendLine('_No skills found._')
} else {
    [void]$sb.AppendLine('| Name | Description |')
    [void]$sb.AppendLine('|------|-------------|')
    foreach ($s in $skills) {
        [void]$sb.AppendLine("| [$($s.Name)]($($s.Directory)) | $($s.Description) |")
    }
}

# Write file (UTF-8 no BOM, LF line endings)
$content = $sb.ToString() -replace '\r\n', "`n"
[System.IO.File]::WriteAllText($readmePath, $content, [System.Text.UTF8Encoding]::new($false))

Write-Host "README.md updated with $($agents.Count) agent(s) and $($skills.Count) skill(s)."
