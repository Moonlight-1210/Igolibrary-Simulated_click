[Console]::InputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes

$controlWalker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
$invokePatternId = [System.Windows.Automation.InvokePattern]::Pattern

function Write-JsonLine($obj) {
    $json = $obj | ConvertTo-Json -Compress -Depth 6
    [Console]::Out.WriteLine($json)
    [Console]::Out.Flush()
}

function Rect-ToArray($r) {
    try {
        if ([double]::IsInfinity($r.Left) -or [double]::IsNaN($r.Left) -or $r.IsEmpty) {
            return $null
        }
        return @([int]$r.Left, [int]$r.Top, [int]$r.Right, [int]$r.Bottom)
    } catch {
        return $null
    }
}

function Get-DocRoot($hwnd) {
    $el = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr][long]$hwnd)
    if ($null -eq $el) { return $null }
    try {
        $ch = $controlWalker.GetFirstChild($el)
        if ($null -eq $ch) {
            Start-Sleep -Milliseconds 600
        }
    } catch {
    }
    return $el
}

function Try-InvokeWithAncestors($node, [int]$maxLevels = 3) {
    $cur = $node
    for ($lvl = 0; $lvl -le $maxLevels; $lvl++) {
        if ($null -eq $cur) { break }
        $pat = $null
        try {
            if ($cur.TryGetCurrentPattern($invokePatternId, [ref]$pat)) {
                $pat.Invoke()
                return $true
            }
        } catch {
            return $false
        }
        try {
            $cur = $controlWalker.GetParent($cur)
        } catch {
            break
        }
    }
    return $false
}

function Handle-Scan([long]$hwnd, [bool]$textsOnly) {
    try {
        $root = Get-DocRoot $hwnd
        if ($null -eq $root) {
            return @{ ok = $false; error = 'AutomationElement is null' }
        }

        $docName = ''
        $docRect = $null
        $seats = New-Object 'System.Collections.Generic.List[object]'
        $texts = New-Object 'System.Collections.Generic.List[object]'

        $stack = New-Object System.Collections.Stack
        $stack.Push(@{ Node = $root; Depth = 0 })
        $visited = 0
        $maxNodes = 1500
        $maxDepth = 20

        while ($stack.Count -gt 0 -and $visited -lt $maxNodes) {
            $item = $stack.Pop()
            $node = $item.Node
            $depth = [int]$item.Depth
            $visited++

            $cls = ''
            $ctype = ''
            $name = ''
            $aid = ''
            $rectArr = $null
            $skipChildren = $false

            try {
                $cur = $node.Current
                $ctype = $cur.ControlType.ProgrammaticName -replace '^ControlType\.', ''
                $cls = if ($null -ne $cur.ClassName) { $cur.ClassName.Trim() } else { '' }
                $name = if ($null -ne $cur.Name) { ($cur.Name -replace '[\r\n]+', ' ').Trim() } else { '' }
                $aid = if ($null -ne $cur.AutomationId) { $cur.AutomationId.Trim() } else { '' }
                $rectArr = Rect-ToArray $cur.BoundingRectangle

                if ($ctype -eq 'Document' -or $aid -eq 'RootWebArea') {
                    if ($docName -eq '' -and $name -ne '') { $docName = $name }
                    if ($null -eq $docRect -and $null -ne $rectArr) { $docRect = $rectArr }
                } elseif ($textsOnly -and $cls -eq 'seatImgClass') {
                    $skipChildren = $true
                } elseif ($name -ne '') {
                    $isSeatLabel = $false
                    if ($ctype -eq 'Text' -and $name -match '^\d{2,4}$' -and $null -ne $rectArr) {
                        $w = $rectArr[2] - $rectArr[0]
                        $h = $rectArr[3] - $rectArr[1]
                        if ($w -gt 0 -and $w -le 40 -and $h -gt 0 -and $h -le 25) {
                            $isSeatLabel = $true
                        }
                    }
                    if ($isSeatLabel) {
                        if (-not $textsOnly) {
                            $seats.Add(@{ id = $name; rect = $rectArr })
                        }
                    } elseif ($ctype -in @('Text', 'Button', 'Hyperlink', 'Alert', 'StatusBar')) {
                        $texts.Add(@{ text = $name; rect = $rectArr; type = $ctype })
                    }
                }
            } catch {
                continue
            }

            if (-not $skipChildren -and $depth -lt $maxDepth) {
                $children = New-Object System.Collections.ArrayList
                try {
                    $ch = $controlWalker.GetFirstChild($node)
                    while ($null -ne $ch) {
                        [void]$children.Add($ch)
                        $ch = $controlWalker.GetNextSibling($ch)
                        if ($children.Count -ge 400) { break }
                    }
                } catch {
                }
                for ($i = $children.Count - 1; $i -ge 0; $i--) {
                    $stack.Push(@{ Node = $children[$i]; Depth = ($depth + 1) })
                }
            }
        }

        $res = @{
            ok = $true
            doc_name = $docName
            doc_rect = $docRect
            texts = @($texts.ToArray())
        }
        if (-not $textsOnly) {
            $res['seats'] = @($seats.ToArray())
        }
        return $res
    } catch {
        return @{ ok = $false; error = $_.Exception.Message }
    }
}

function Handle-Invoke([long]$hwnd, [string]$targetName, [bool]$seatOnly) {
    try {
        $root = Get-DocRoot $hwnd
        if ($null -eq $root) {
            return @{ ok = $false; found = $false; invoked = $false; error = 'AutomationElement is null' }
        }

        $stack = New-Object System.Collections.Stack
        $stack.Push(@{ Node = $root; Depth = 0 })
        $visited = 0
        $maxNodes = 1500
        $maxDepth = 20

        while ($stack.Count -gt 0 -and $visited -lt $maxNodes) {
            $item = $stack.Pop()
            $node = $item.Node
            $depth = [int]$item.Depth
            $visited++

            try {
                $cur = $node.Current
                $ctype = $cur.ControlType.ProgrammaticName -replace '^ControlType\.', ''
                $name = if ($null -ne $cur.Name) { ($cur.Name -replace '[\r\n]+', ' ').Trim() } else { '' }
                if ($name -eq $targetName -and ((-not $seatOnly) -or $ctype -eq 'Text')) {
                    $rectArr = Rect-ToArray $cur.BoundingRectangle
                    $invoked = Try-InvokeWithAncestors -node $node -maxLevels 3
                    return @{
                        ok = $invoked
                        found = $true
                        invoked = $invoked
                        rect = $rectArr
                        error = if ($invoked) { '' } else { 'InvokePattern not found or invoke failed' }
                    }
                }
            } catch {
                continue
            }

            if ($depth -lt $maxDepth) {
                $children = New-Object System.Collections.ArrayList
                try {
                    $ch = $controlWalker.GetFirstChild($node)
                    while ($null -ne $ch) {
                        [void]$children.Add($ch)
                        $ch = $controlWalker.GetNextSibling($ch)
                        if ($children.Count -ge 400) { break }
                    }
                } catch {
                }
                for ($i = $children.Count - 1; $i -ge 0; $i--) {
                    $stack.Push(@{ Node = $children[$i]; Depth = ($depth + 1) })
                }
            }
        }

        return @{ ok = $false; found = $false; invoked = $false; rect = $null; error = 'Target not found' }
    } catch {
        return @{ ok = $false; found = $false; invoked = $false; rect = $null; error = $_.Exception.Message }
    }
}

Write-JsonLine @{ ready = $true }

while ($true) {
    $line = [Console]::In.ReadLine()
    if ($null -eq $line) { break }
    $trimmed = $line.Trim()
    if ($trimmed -eq '') { continue }

    try {
        $req = $trimmed | ConvertFrom-Json
    } catch {
        Write-JsonLine @{ ok = $false; error = ('Invalid JSON: ' + $_.Exception.Message) }
        continue
    }

    $cmd = [string]$req.cmd
    if ($cmd -eq 'quit') {
        Write-JsonLine @{ ok = $true; bye = $true }
        break
    } elseif ($cmd -eq 'scan') {
        $resp = Handle-Scan -hwnd ([long]$req.render_hwnd) -textsOnly $false
        Write-JsonLine $resp
    } elseif ($cmd -eq 'texts') {
        $resp = Handle-Scan -hwnd ([long]$req.render_hwnd) -textsOnly $true
        Write-JsonLine $resp
    } elseif ($cmd -eq 'invoke_text') {
        $resp = Handle-Invoke -hwnd ([long]$req.render_hwnd) -targetName ([string]$req.text) -seatOnly $false
        Write-JsonLine $resp
    } elseif ($cmd -eq 'invoke_seat') {
        $resp = Handle-Invoke -hwnd ([long]$req.render_hwnd) -targetName ([string]$req.seat) -seatOnly $true
        Write-JsonLine $resp
    } else {
        Write-JsonLine @{ ok = $false; error = ('Unknown cmd: ' + $cmd) }
    }
}
