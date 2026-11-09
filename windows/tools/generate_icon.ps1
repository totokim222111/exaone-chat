$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$output = Join-Path $PSScriptRoot '../src/ExaoneChat/app.ico'
$images = [System.Collections.Generic.List[byte[]]]::new()
$sizes = @(16,32,48,256)
foreach ($size in $sizes) {
    $bitmap = [System.Drawing.Bitmap]::new($size,$size)
    $g = [System.Drawing.Graphics]::FromImage($bitmap)
    $g.Clear([System.Drawing.ColorTranslator]::FromHtml('#123527'))
    $brush = [System.Drawing.SolidBrush]::new([System.Drawing.ColorTranslator]::FromHtml('#ECF0ED'))
    # Pixel-aligned E stays legible at every icon size.
    $x = [int]($size * .27); $y = [int]($size * .22)
    $w = [int]($size * .48); $h = [int]($size * .56); $s = [Math]::Max(2,[int]($size * .11))
    $g.FillRectangle($brush,$x,$y,$s,$h)
    $g.FillRectangle($brush,$x,$y,$w,$s)
    $g.FillRectangle($brush,$x,$y+[int](($h-$s)/2),[int]($w*.85),$s)
    $g.FillRectangle($brush,$x,$y+$h-$s,$w,$s)
    $stream = [System.IO.MemoryStream]::new()
    $bitmap.Save($stream,[System.Drawing.Imaging.ImageFormat]::Png)
    $images.Add($stream.ToArray())
    $stream.Dispose(); $brush.Dispose(); $g.Dispose(); $bitmap.Dispose()
}
$file = [System.IO.File]::Create($output)
$writer = [System.IO.BinaryWriter]::new($file)
try {
    $writer.Write([uint16]0); $writer.Write([uint16]1); $writer.Write([uint16]$sizes.Count)
    $offset = 6 + 16 * $sizes.Count
    for ($i=0;$i -lt $sizes.Count;$i++) {
        $v = if ($sizes[$i] -eq 256) { 0 } else { $sizes[$i] }
        $writer.Write([byte]$v); $writer.Write([byte]$v); $writer.Write([byte]0); $writer.Write([byte]0)
        $writer.Write([uint16]1); $writer.Write([uint16]32)
        $writer.Write([uint32]$images[$i].Length); $writer.Write([uint32]$offset)
        $offset += $images[$i].Length
    }
    foreach ($bytes in $images) { $writer.Write($bytes) }
} finally { $writer.Dispose(); $file.Dispose() }
