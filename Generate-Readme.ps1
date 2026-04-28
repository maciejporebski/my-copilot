<#
.SYNOPSIS
    Generates README.md from the agents, skills, and extensions in this repository.
.DESCRIPTION
    Scans the .github/agents/, .github/skills/, and .github/extensions/
    directories, extracts metadata, and writes summary tables into README.md.
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

function Get-FirstRegexGroup {
    param(
        [string]$Content,
        [string[]]$Patterns
    )

    foreach ($pattern in $Patterns) {
        $match = [regex]::Match($Content, $pattern, [System.Text.RegularExpressions.RegexOptions]::Singleline)
        if ($match.Success) {
            return $match.Groups[1].Value.Trim()
        }
    }

    return $null
}

function Resolve-JsTemplateConstants {
    param(
        [string]$Text,
        [string]$Content
    )

    $constants = @{}
    [regex]::Matches($Content, 'const\s+(\w+)\s*=\s*["'']([^"'']+)["'']') | ForEach-Object {
        $constants[$_.Groups[1].Value] = $_.Groups[2].Value
    }

    foreach ($key in $constants.Keys) {
        $Text = $Text.Replace(('${' + $key + '}'), $constants[$key])
    }

    return $Text
}

# --- Collect agents ---
$githubDir = Join-Path $repoRoot '.github'
$agentsDir = Join-Path $githubDir 'agents'
$agents = @()
if (Test-Path $agentsDir) {
    Get-ChildItem -Path $agentsDir -Filter '*.agent.md' -File | ForEach-Object {
        $fm = Get-Frontmatter -Content (Get-Content $_.FullName -Raw -Encoding utf8)
        $agents += [PSCustomObject]@{
            Name        = if ($fm['name']) { $fm['name'] } else { $_.BaseName -replace '\.agent$', '' }
            Description = if ($fm['description']) { $fm['description'] } else { '—' }
            File        = ".github/agents/$($_.Name)"
        }
    }
}

# --- Collect skills ---
$skillsDir = Join-Path $githubDir 'skills'
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
                Directory   = ".github/skills/$($_.Name)"
            }
        }
    }
}

# --- Collect extensions ---
$extensionsDir = Join-Path $githubDir 'extensions'
$extensions = @()
if (Test-Path $extensionsDir) {
    Get-ChildItem -Path $extensionsDir -Directory | ForEach-Object {
        $extensionFile = Join-Path $_.FullName 'extension.mjs'
        if (Test-Path $extensionFile) {
            $content = Get-Content $extensionFile -Raw -Encoding utf8
            $commandNames = [regex]::Matches($content, 'name\s*:\s*["'']([^"'']+)["'']') |
                ForEach-Object { "/$($_.Groups[1].Value)" } |
                Select-Object -Unique
            $description = Get-FirstRegexGroup -Content $content -Patterns @(
                'description\s*:\s*"([^"]+)"',
                "description\s*:\s*'([^']+)'",
                'description\s*:\s*`([^`]+)`'
            )
            if ($description) {
                $description = Resolve-JsTemplateConstants -Text $description -Content $content
            }

            $extensions += [PSCustomObject]@{
                Name        = $_.Name
                Commands    = if ($commandNames) { $commandNames -join ', ' } else { '—' }
                Description = if ($description) { $description } else { '—' }
                Directory   = ".github/extensions/$($_.Name)"
            }
        }
    }
}

# --- Build README ---
$sb = [System.Text.StringBuilder]::new()
[void]$sb.AppendLine('# My Copilot')
[void]$sb.AppendLine()
[void]$sb.AppendLine('A collection of custom GitHub Copilot agents, skills, and extensions.')
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
[void]$sb.AppendLine()

# Extensions table
[void]$sb.AppendLine('## Extensions')
[void]$sb.AppendLine()
if ($extensions.Count -eq 0) {
    [void]$sb.AppendLine('_No extensions found._')
} else {
    [void]$sb.AppendLine('| Name | Commands | Description |')
    [void]$sb.AppendLine('|------|----------|-------------|')
    foreach ($e in $extensions) {
        [void]$sb.AppendLine("| [$($e.Name)]($($e.Directory)) | $($e.Commands) | $($e.Description) |")
    }
}

# Write file (UTF-8 no BOM, LF line endings)
$content = $sb.ToString() -replace '\r\n', "`n"
[System.IO.File]::WriteAllText($readmePath, $content, [System.Text.UTF8Encoding]::new($false))

Write-Host "README.md updated with $($agents.Count) agent(s), $($skills.Count) skill(s), and $($extensions.Count) extension(s)."
