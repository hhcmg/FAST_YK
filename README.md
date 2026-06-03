# FAST_YK

`FAST_YK` is a simplified GNSS downloader built for local workflows under `/data/yk/software/FAST_YK`.

It combines:
- FAST's multi-source URL templates
- the practical directory layout from `/data/yk/tools/download.py`
- optional short-name renaming controlled by a global default or CLI flag

## Main Script

`/data/yk/software/FAST_YK/download.py`

## Source Config

`/data/yk/software/FAST_YK/download_sources.json`

## Default Paths

- Data root: `/data/yk/work_hl/data`
- MGEX site list: `/data/yk/ctrl/mgex.sit`
- iGMAS site list: `/data/yk/ctrl/igmas.sit`
- Helper binaries: `/data/yk/software/FAST_YK/bin`

## Supported Types

- Precise orbit: `igs`, `whu`, `sha`, `cod`, `gbm`
- Precise clock: `igsclk`, `whuclk`, `shaclk`, `codclk`, `gbmclk`
- Broadcast ephemeris: `brdc`, `brdm`
- Weekly SINEX: `snx`
- Station observation: `mgex`, `igmas`

Aliases:
- `wum` -> `whu`
- `wumclk` -> `whuclk`
- `gfz` -> `gbm`
- `gfzclk` -> `gbmclk`
- `igsobs` -> `mgex`

## Examples

```bash
python3 /data/yk/software/FAST_YK/download.py igs 2024 100 2024 100
python3 /data/yk/software/FAST_YK/download.py gbmclk 2024 122 2024 122
nohup python /data/yk/software/FAST_YK/download.py shaclk --rename 2023 6 2023 36 > /data/yk/software/FAST_YK/log/download_clk.txt 2>&1 & #sp3
nohup python -u  /data/yk/software/FAST_YK/download.py mgex 2022 234 2022 234 --rename > /data/yk/software/FAST_YK/log/download_mgex.txt 2>&1 & #IGS测站
nohup python -u   /data/yk/software/FAST_YK/download.py brdm 2026 109 2026 110 --rename > /data/yk/software/FAST_YK/log/download_brdm.txt 2>&1 &  #广播星历
python /data/yk/software/FAST_YK/download.py snx 2022 234 2022 234 --no-rename
python /data/yk/software/FAST_YK/download.py brdc,snx 2024 200 2024 200
nohup python -u  /data/yk/software/FAST_YK/download.py mgex 2024 150 2024 150 --no-rename --igs-site-list /path/to/site.list > /data/yk/software/FAST_YK/log/log3.txt 2>&1 &
```

先运行python /data/yk/software/FAST_YK/download.py brdm 2022 234 2022 234 --no-rename
再运行python /data/yk/software/FAST_YK/download.py brdm 2022 234 2022 234 --rename
会导致文件已存在而无法下载，但重命名成功

## Notes

- When renaming is enabled, precise products use short prefixes like `igs`, `wum`, `sha`, `cod`, `gbm`.
- `mgex` uses `GCRE_MGEX_obs`. A local `IGSNetwork.csv` lookup is used to translate 4-char station names into 9-char long MGEX names.
- `igmas` keeps the original wildcard download logic but only downloads once per day when requested stations are missing.
- The script prints a final summary with expected targets, newly downloaded files, existing files skipped, and failed targets.
