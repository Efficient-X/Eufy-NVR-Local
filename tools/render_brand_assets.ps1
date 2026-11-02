# Original EFX geometric camera/recorder artwork. SPDX-License-Identifier: MIT
# Copyright 2026 Efficient X (EFX). Uses Windows System.Drawing, no downloads.
Add-Type -AssemblyName System.Drawing
$assetRoot = Split-Path -Parent $PSScriptRoot
$brandDirectory = Join-Path $assetRoot 'custom_components\eufy_nvr\brand'
$appDirectory = Join-Path $assetRoot 'eufy_nvr'
$artDirectory = Join-Path $assetRoot 'artwork'
New-Item -ItemType Directory -Force -Path $brandDirectory,$artDirectory | Out-Null
$navy = [Drawing.ColorTranslator]::FromHtml('#14283F')
$cyan = [Drawing.ColorTranslator]::FromHtml('#38C9DE')
$green = [Drawing.ColorTranslator]::FromHtml('#7AE3AE')
function RoundedPath([float]$x,[float]$y,[float]$w,[float]$h,[float]$radius) {
    $p = [Drawing.Drawing2D.GraphicsPath]::new()
    $d = 2 * $radius
    $p.AddArc($x,$y,$d,$d,180,90)
    $p.AddArc(($x+$w-$d),$y,$d,$d,270,90)
    $p.AddArc(($x+$w-$d),($y+$h-$d),$d,$d,0,90)
    $p.AddArc($x,($y+$h-$d),$d,$d,90,90)
    $p.CloseFigure()
    return $p
}
function DrawOriginalIcon($g,[float]$x,[float]$y,[float]$size) {
    $state=$g.Save();$g.TranslateTransform($x,$y);$g.ScaleTransform(($size/256),($size/256))
    $fill=[Drawing.SolidBrush]::new($navy);$edge=[Drawing.Pen]::new($cyan,8)
    $edge.LineJoin=[Drawing.Drawing2D.LineJoin]::Round
    $edge.StartCap=$edge.EndCap=[Drawing.Drawing2D.LineCap]::Round
    $cable=[Drawing.Pen]::new($cyan,8)
    $g.DrawLines($cable,[Drawing.PointF[]]@([Drawing.PointF]::new(90,144),[Drawing.PointF]::new(90,176),[Drawing.PointF]::new(128,176),[Drawing.PointF]::new(128,196)))
    $body=RoundedPath 4 22 172 122 15
    $g.FillPath($fill,$body);$g.DrawPath($edge,$body);$body.Dispose()
    $nose=[Drawing.PointF[]]@([Drawing.PointF]::new(176,58),[Drawing.PointF]::new(248,4),[Drawing.PointF]::new(248,164),[Drawing.PointF]::new(176,112))
    $g.FillPolygon($fill,$nose);$g.DrawPolygon($edge,$nose)
    $white=[Drawing.Pen]::new([Drawing.Color]::White,9)
    $g.DrawEllipse($white,52,45,76,76)
    $g.FillEllipse([Drawing.Brushes]::White,80,73,20,20)
    $recorder=RoundedPath 4 196 248 56 12
    $g.FillPath($fill,$recorder);$g.DrawPath($edge,$recorder);$recorder.Dispose()
    $led=[Drawing.SolidBrush]::new($green)
    $g.FillEllipse($led,25,217,14,14);$g.FillEllipse($led,53,217,14,14)
    $slot=[Drawing.Pen]::new($cyan,4);$g.DrawRectangle($slot,99,211,130,26)
    foreach($item in @($fill,$edge,$cable,$white,$led,$slot)){$item.Dispose()};$g.Restore($state)
}
function NewImage([int]$width,[int]$height) {
    $bitmap=[Drawing.Bitmap]::new($width,$height,[Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $graphics=[Drawing.Graphics]::FromImage($bitmap)
    $graphics.SmoothingMode=[Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.TextRenderingHint=[Drawing.Text.TextRenderingHint]::AntiAliasGridFit
    return @($bitmap,$graphics)
}
foreach($item in @(@('icon.png',256),@('icon@2x.png',512))) {
    $im=NewImage $item[1] $item[1];DrawOriginalIcon $im[1] 0 0 $item[1]
    $im[0].Save((Join-Path $brandDirectory $item[0]),[Drawing.Imaging.ImageFormat]::Png)
    $im[1].Dispose();$im[0].Dispose()
}
$im=NewImage 128 128;DrawOriginalIcon $im[1] 0 0 128
$im[0].Save((Join-Path $appDirectory 'icon.png'),[Drawing.Imaging.ImageFormat]::Png);$im[1].Dispose();$im[0].Dispose()
function DrawLogo($g,[float]$size,[bool]$dark) {
    $state=$g.Save();$g.ScaleTransform($size,$size);DrawOriginalIcon $g 0 0 200
    $ink=[Drawing.SolidBrush]::new($(if($dark){[Drawing.Color]::White}else{$navy}))
    $accent=[Drawing.SolidBrush]::new($cyan)
    $title=[Drawing.Font]::new('Segoe UI',31,[Drawing.FontStyle]::Bold,[Drawing.GraphicsUnit]::Pixel)
    $sub=[Drawing.Font]::new('Segoe UI',21,[Drawing.FontStyle]::Regular,[Drawing.GraphicsUnit]::Pixel)
    $g.DrawString('EUFY NVR LOCAL',$title,$ink,218,52)
    $g.DrawString('Efficient X · EFX',$sub,$accent,220,101)
    foreach($item in @($ink,$accent,$title,$sub)){$item.Dispose()};$g.Restore($state)
}
foreach($dark in @($false,$true)) {
    foreach($scale in @(1,2)) {
        $im=NewImage (660*$scale) (200*$scale);DrawLogo $im[1] $scale $dark
        $name=$(if($dark){'dark_logo'}else{'logo'})+$(if($scale-eq 2){'@2x'}else{''})+'.png'
        $im[0].Save((Join-Path $brandDirectory $name),[Drawing.Imaging.ImageFormat]::Png);$im[1].Dispose();$im[0].Dispose()
    }
}
# Supervisor app page is dark in HA; the brand package also includes light-logo variants.
$im=NewImage 330 100;DrawLogo $im[1] .5 $true
$im[0].Save((Join-Path $appDirectory 'logo.png'),[Drawing.Imaging.ImageFormat]::Png);$im[1].Dispose();$im[0].Dispose()
$svg=@'
<!-- SPDX-License-Identifier: MIT. Original artwork, Copyright 2026 Efficient X (EFX). -->
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256">
 <g stroke="#38C9DE" stroke-width="8" stroke-linejoin="round" fill="#14283F">
  <path d="M90 144V176H128V196" fill="none"/>
  <rect x="4" y="22" width="172" height="122" rx="15"/>
  <path d="M176 58L248 4V164L176 112Z"/>
  <rect x="4" y="196" width="248" height="56" rx="12"/>
 </g>
 <circle cx="90" cy="83" r="38" fill="none" stroke="white" stroke-width="9"/>
 <circle cx="90" cy="83" r="10" fill="white"/>
 <circle cx="32" cy="224" r="7" fill="#7AE3AE"/>
 <circle cx="60" cy="224" r="7" fill="#7AE3AE"/>
 <rect x="99" y="211" width="130" height="26" fill="none" stroke="#38C9DE" stroke-width="4"/>
</svg>
'@
$svg | Set-Content -LiteralPath (Join-Path $artDirectory 'efx-camera-recorder.svg') -Encoding utf8
# Honest design preview, not a screenshot of an applied HA update.
$im=NewImage 1400 850;$g=$im[1];$g.Clear([Drawing.ColorTranslator]::FromHtml('#101721'))
$fontTitle=[Drawing.Font]::new('Segoe UI',38,[Drawing.FontStyle]::Bold,[Drawing.GraphicsUnit]::Pixel)
$fontBody=[Drawing.Font]::new('Segoe UI',24,[Drawing.FontStyle]::Regular,[Drawing.GraphicsUnit]::Pixel)
$fontSmall=[Drawing.Font]::new('Segoe UI',20,[Drawing.FontStyle]::Regular,[Drawing.GraphicsUnit]::Pixel)
$muted=[Drawing.SolidBrush]::new([Drawing.ColorTranslator]::FromHtml('#ADC1D5'))
$cyanBrush=[Drawing.SolidBrush]::new($cyan)
$g.DrawString('EUFY NVR LOCAL',$fontTitle,[Drawing.Brushes]::White,52,38)
$g.DrawString('EFX · LOCAL DESIGN PREVIEW — NOT INSTALLED OR RELEASED',$fontSmall,$cyanBrush,54,94)
$panel=[Drawing.SolidBrush]::new([Drawing.ColorTranslator]::FromHtml('#1D2B3B'))
$shape=RoundedPath 48 160 820 600 24;$g.FillPath($panel,$shape);$shape.Dispose()
DrawOriginalIcon $g 84 193 112
$g.DrawString('Experimental review candidate',$fontBody,[Drawing.Brushes]::White,224,202)
$g.DrawString('Independent project by Efficient X (EFX)',$fontSmall,$muted,224,242)
$lines=@('Guided setup + shared fixed/tracking camera tiles.','Offline parser and browser-registration fixes tested.','','NOT a stable release.','Fresh-install live-camera acceptance is still pending.','Native containers, tablet and recovery gates remain.','','Eufy internet login/signalling are still required.','Keep an existing working camera engine in place.')
$y=344;foreach($line in $lines){$g.DrawString($line,$fontSmall,$muted,84,$y);$y+=36}
$g.DrawString('Existing release status ↗',$fontBody,$cyanBrush,84,690)
$g.FillRectangle([Drawing.Brushes]::White,920,160,432,255);DrawOriginalIcon $g 1045 183 196
$g.DrawString('Light surface',$fontSmall,[Drawing.Brushes]::Black,1048,385)
DrawOriginalIcon $g 1045 452 196;$g.DrawString('Dark surface',$fontSmall,$muted,1048,660)
$g.DrawString('No Eufy or Home Assistant endorsement',$fontSmall,$muted,52,798)
$im[0].Save((Join-Path $artDirectory 'release-presentation-preview.png'),[Drawing.Imaging.ImageFormat]::Png)
foreach($item in @($fontTitle,$fontBody,$fontSmall,$muted,$cyanBrush,$panel,$g,$im[0])){$item.Dispose()}
Write-Output 'Original SVG, brand/app PNG assets and preview rendered locally.'
