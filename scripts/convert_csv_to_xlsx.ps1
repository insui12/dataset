param(
    [Parameter(Mandatory = $true)]
    [string]$InputCsv,

    [Parameter(Mandatory = $true)]
    [string]$OutputXlsx,

    [string]$SheetName = "Sheet1"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Escape-Xml {
    param([AllowNull()][string]$Value)
    if ($null -eq $Value) {
        return ""
    }
    return [System.Security.SecurityElement]::Escape([string]$Value)
}

function Write-Utf8File {
    param(
        [string]$Path,
        [string]$Content
    )
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $utf8)
}

function Get-ColumnName {
    param([int]$Index)
    $name = ""
    $n = $Index
    while ($n -gt 0) {
        $remainder = ($n - 1) % 26
        $name = [char](65 + $remainder) + $name
        $n = [math]::Floor(($n - 1) / 26)
    }
    return $name
}

function New-WorksheetXml {
    param(
        [object[]]$Rows,
        [string[]]$Headers
    )

    $sb = New-Object System.Text.StringBuilder
    [void]$sb.AppendLine('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>')
    [void]$sb.AppendLine('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">')
    [void]$sb.AppendLine('  <sheetData>')

    $rowIndex = 1

    [void]$sb.AppendLine("    <row r=`"$rowIndex`">")
    for ($i = 0; $i -lt $Headers.Count; $i++) {
        $cellRef = "$(Get-ColumnName ($i + 1))$rowIndex"
        $value = Escape-Xml $Headers[$i]
        [void]$sb.AppendLine("      <c r=`"$cellRef`" t=`"inlineStr`"><is><t>$value</t></is></c>")
    }
    [void]$sb.AppendLine('    </row>')

    foreach ($row in $Rows) {
        $rowIndex++
        [void]$sb.AppendLine("    <row r=`"$rowIndex`">")
        for ($i = 0; $i -lt $Headers.Count; $i++) {
            $header = $Headers[$i]
            $cellRef = "$(Get-ColumnName ($i + 1))$rowIndex"
            $rawValue = $row.$header
            $value = Escape-Xml $rawValue
            [void]$sb.AppendLine("      <c r=`"$cellRef`" t=`"inlineStr`"><is><t xml:space=`"preserve`">$value</t></is></c>")
        }
        [void]$sb.AppendLine('    </row>')
    }

    [void]$sb.AppendLine('  </sheetData>')
    [void]$sb.AppendLine('</worksheet>')
    return $sb.ToString()
}

$resolvedInput = (Resolve-Path $InputCsv).Path
$resolvedOutput = [System.IO.Path]::GetFullPath($OutputXlsx)
$outputDir = Split-Path -Parent $resolvedOutput
if (-not (Test-Path $outputDir)) {
    New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
}

$rows = Import-Csv -Path $resolvedInput
$headers = @()
if ($rows.Count -gt 0) {
    $headers = $rows[0].PSObject.Properties.Name
} else {
    $firstLine = Get-Content -Path $resolvedInput -TotalCount 1
    if ($firstLine) {
        $headers = $firstLine.Split(",")
    }
}

$sheetNameEscaped = Escape-Xml $SheetName
$sheetXml = New-WorksheetXml -Rows $rows -Headers $headers

$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("xlsx_" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tempRoot | Out-Null
New-Item -ItemType Directory -Path (Join-Path $tempRoot "_rels") | Out-Null
New-Item -ItemType Directory -Path (Join-Path $tempRoot "docProps") | Out-Null
New-Item -ItemType Directory -Path (Join-Path $tempRoot "xl") | Out-Null
New-Item -ItemType Directory -Path (Join-Path $tempRoot "xl\_rels") | Out-Null
New-Item -ItemType Directory -Path (Join-Path $tempRoot "xl\worksheets") | Out-Null

Write-Utf8File -Path (Join-Path $tempRoot "[Content_Types].xml") -Content $(
@'
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>
'@)

Write-Utf8File -Path (Join-Path $tempRoot "_rels\.rels") -Content $(
@'
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>
'@)

Write-Utf8File -Path (Join-Path $tempRoot "xl\workbook.xml") -Content $(
@"
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
          xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="$sheetNameEscaped" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>
"@)

Write-Utf8File -Path (Join-Path $tempRoot "xl\_rels\workbook.xml.rels") -Content $(
@'
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>
'@)

Write-Utf8File -Path (Join-Path $tempRoot "xl\styles.xml") -Content $(
@'
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="1">
    <font>
      <sz val="11"/>
      <name val="Calibri"/>
    </font>
  </fonts>
  <fills count="1">
    <fill><patternFill patternType="none"/></fill>
  </fills>
  <borders count="1">
    <border/>
  </borders>
  <cellStyleXfs count="1">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>
  </cellStyleXfs>
  <cellXfs count="1">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
  </cellXfs>
  <cellStyles count="1">
    <cellStyle name="Normal" xfId="0" builtinId="0"/>
  </cellStyles>
</styleSheet>
'@)

Write-Utf8File -Path (Join-Path $tempRoot "xl\worksheets\sheet1.xml") -Content $sheetXml

$created = (Get-Date).ToUniversalTime().ToString("s") + "Z"
Write-Utf8File -Path (Join-Path $tempRoot "docProps\core.xml") -Content $(
@"
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
                   xmlns:dc="http://purl.org/dc/elements/1.1/"
                   xmlns:dcterms="http://purl.org/dc/terms/"
                   xmlns:dcmitype="http://purl.org/dc/dcmitype/"
                   xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:creator>Codex</dc:creator>
  <cp:lastModifiedBy>Codex</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">$created</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">$created</dcterms:modified>
</cp:coreProperties>
"@)

Write-Utf8File -Path (Join-Path $tempRoot "docProps\app.xml") -Content $(
@"
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
            xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>Codex</Application>
  <Sheets>1</Sheets>
  <TitlesOfParts>
    <vt:vector size="1" baseType="lpstr">
      <vt:lpstr>$sheetNameEscaped</vt:lpstr>
    </vt:vector>
  </TitlesOfParts>
</Properties>
"@)

$zipPath = [System.IO.Path]::ChangeExtension($resolvedOutput, ".zip")
if (Test-Path $zipPath) {
    Remove-Item $zipPath -Force
}
if (Test-Path $resolvedOutput) {
    Remove-Item $resolvedOutput -Force
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory($tempRoot, $zipPath)
Move-Item -Path $zipPath -Destination $resolvedOutput
Remove-Item -Path $tempRoot -Recurse -Force

Write-Output "Created: $resolvedOutput"
