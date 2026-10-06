$env:PORT = "8888"
$env:CACHE_ENABLED = "true"
$env:CACHE_PATH = "pansou\cache"
$env:CACHE_MAX_SIZE = "100"
$env:CACHE_TTL = "60"
$env:ASYNC_PLUGIN_ENABLED = "true"
$env:ASYNC_RESPONSE_TIMEOUT = "4"
$env:ASYNC_MAX_BACKGROUND_WORKERS = "20"
$env:ASYNC_MAX_BACKGROUND_TASKS = "100"
$env:ASYNC_CACHE_TTL_HOURS = "1"
$env:CHANNELS = "tgsearchers3,tgsearchers7,yunpanxunlei,QuarkFree,Quark_Movies,Baidu_netdisk,shareAliyun,ucquark,leoziyuan"
$env:ENABLED_PLUGINS = "wanou,pansearch,quark4k,quarksoo,quarkres,dyyjpro,shandian,zhizhen,duoduo,huban,labi,muou,susu,u3c3,clmao,feikuai,haitunsou,miaoso,xiaoyu,xiaokupan,qqpd,panlian,buerchen"

if (!(Test-Path "pansou\cache")) { New-Item -ItemType Directory -Path "pansou\cache" | Out-Null }

Write-Host "PanSou starting on http://localhost:8888" -ForegroundColor Cyan
.\pansou\pansou.exe
