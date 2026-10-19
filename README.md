# FAST_YK

GNSS 多源下载工具，支持精密轨道、精密钟差、广播星历、SINEX、偏差产品和测站观测数据；支持按日期并行下载、解压、Compact RINEX 转换及可选短文件名。

## 运行准备

下面的命令均从仓库根目录执行，统一使用 `python3`。需要 Python 3、`wget`、`gzip`，观测文件转换还需要仓库内可执行的 `bin/crx2rnx`。

| 文件 | 用途 |
| --- | --- |
| `download.py` | 下载入口 |
| `download_sources.json` | 下载源和凭据配置名称，不保存实际账号密码 |
| `global_B1a` | 默认 MGEX/IGS 测站列表 |
| `global_B1I` | 默认 iGMAS 测站列表 |
| `bin/IGSNetwork.csv` | MGEX 四字符站名到九字符长站名的映射 |
| `.download_credentials.json` | 本地账号密码文件，被 Git 忽略 |

```bash
python3 download.py --help
python3 download.py --list-types
```

所有下载示例均显式指定 `--output-dir` 和 `--path-mode`，可按需要替换输出路径。相对输出路径相对于运行命令时的当前目录；默认测站列表、下载源和凭据文件则相对于 `download.py` 所在目录。

## 参数与日期

基本顺序为：`数据类型 起始年 起始年积日 结束年 结束年积日 [选项]`。

- 日期范围包含首尾。年积日可写为 `98` 或 `098`；只有闰年接受第 366 天。
- 多种类型用逗号分隔，例如 `brdc,snx` 或 `whu,whuclk`。
- `--output-dir` 与 `--data-dir` 是同一个参数的两种名称，选用一个即可。
- `--path-mode root` 是默认模式。下文示例仍显式写出，便于确认目录层级。
- `--rename` 启用短文件名，`--no-rename` 关闭可选的短名重命名；当前默认关闭。关闭重命名仍会解压并将 Compact RINEX 转为 RINEX。WHU OSB 始终保留解压后的长名；CAS DCB 在关闭短名重命名时仍会规范为统一长名。
- `--workers 8` 表示最多并行处理 8 个日期任务，默认值为 4。同一天内的产品和测站仍顺序处理，因此单日下载不会产生 8 个并行任务。
- SINEX 按 GPS 周下载，同一周内的多个日期只安排一次下载。

## 支持的数据类型

| 产品 | 类型 |
| --- | --- |
| 精密轨道 | `igs`、`whu`、`sha`、`cod`、`gfz` |
| 精密钟差 | `igsclk`、`whuclk`、`shaclk`、`codclk`、`gfzclk` |
| 广播星历 | `brdc`、`brdm` |
| 周 SINEX | `snx` |
| CAS 快速 DCB | `cas_dcb_rap` |
| WHU 最终 OSB | `whu_osb_finl` |
| MGEX/IGS 观测数据 | `mgex` |
| iGMAS 观测数据 | `igmas` |

别名：`wum` = `whu`，`wumclk` = `whuclk`，`igsobs` = `mgex`。

## 输出目录：root、product、exact

设 `D` 为传给 `--output-dir` 的目录，`YYYY` 为四位年份，`DDD` 为三位年积日。

| 产品 | `root`：D 是数据总目录 | `product`：D 是产品目录 | `exact`：D 是最终目录 |
| --- | --- | --- | --- |
| 精密轨道、精密钟差 | `D/sp3/前缀/YYYY` | `D/前缀/YYYY` | `D` |
| `brdc`、`brdm` | `D/brdc/YYYY` | `D/YYYY` | `D` |
| `mgex`、`igmas` | `D/rinex/YYYY/DDD` | `D/YYYY/DDD` | `D` |
| `cas_dcb_rap` | `D/dcb/cas/YYYY` | `D/YYYY` | `D` |
| `snx` | `D/snx` | `D` | `D` |
| `whu_osb_finl` | `D/osb` | `D` | `D` |

精密产品的中心前缀为 `igs`、`wum`、`sha`、`cod`、`gbm`。`whu` 与 `whuclk` 都使用 `wum`，`gfz` 与 `gfzclk` 都使用 `gbm`。**钟差和轨道共用 `sp3/前缀/YYYY` 目录，不另建 `clk` 目录。**

`product` 会根据产品继续追加目录：例如精密产品仍追加中心前缀和年份，不能把已经包含 `wum/2025` 的路径当作产品目录；这种情况应使用 `exact`。`snx` 和 OSB 不自动追加年份。

多种不同产品一起下载时建议使用 `root`，由脚本分别归类。`exact` 会把指定日期范围内的所有文件放进同一个目录，不追加产品名、中心前缀、年份或年积日。

### 同一份 MGEX 数据的三种等价写法

以下三个命令任选一个，最终都写入 `./work/data/rinex/2025/152`。

```bash
# root：提供数据总目录，补 rinex/2025/152
python3 download.py mgex 2025 152 2025 152 \
  --rename --workers 8 --igs-site-list ./global_B1a \
  --output-dir ./work/data --path-mode root

# product：提供 rinex 产品目录，补 2025/152
python3 download.py mgex 2025 152 2025 152 \
  --rename --workers 8 --igs-site-list ./global_B1a \
  --output-dir ./work/data/rinex --path-mode product

# exact：提供最终目录，不再追加任何层级
python3 download.py mgex 2025 152 2025 152 \
  --rename --workers 8 --igs-site-list ./global_B1a \
  --output-dir ./work/data/rinex/2025/152 --path-mode exact
```

### 同一份 WHU 钟差的三种等价写法

以下三个命令任选一个，最终都写入 `./data/sp3/wum/2025`。将 `whuclk` 换成 `whu` 即下载轨道，目录规则相同。

```bash
# root：补 sp3/wum/2025
python3 download.py whuclk 2025 98 2025 173 \
  --no-rename --workers 8 --output-dir ./data --path-mode root

# product：补 wum/2025；此处传入 sp3，不是 sp3/wum
python3 download.py whuclk 2025 98 2025 173 \
  --no-rename --workers 8 --output-dir ./data/sp3 --path-mode product

# exact：不再追加目录
python3 download.py whuclk 2025 98 2025 173 \
  --no-rename --workers 8 --output-dir ./data/sp3/wum/2025 --path-mode exact
```

## 常用下载示例

以下示例分别使用独立日志名。后台命令前先创建日志目录；Shell 在程序启动前就会打开日志文件，程序自身无法补建重定向所需的父目录。`python3 -u` 关闭 Python 输出缓冲，便于及时查看日志；`>` 会覆盖同名日志，需要追加时改为 `>>`。

### WHU 精密钟差：前台下载

结果目录：`./data/sp3/wum/2025`。

```bash
python3 download.py whuclk 2025 98 2025 173 \
  --no-rename --workers 8 --output-dir ./data --path-mode root
```

### WHU 精密轨道：后台下载

结果目录：`./data/sp3/wum/2025`。

```bash
mkdir -p ./log
nohup python3 -u download.py whu 2025 98 2025 173 \
  --no-rename --workers 8 --output-dir ./data --path-mode root \
  > ./log/download_sp3_2025_098_173.txt 2>&1 &
```

### MGEX/IGS 测站：数据总目录 root

结果目录：`./data/rinex/2025/149`。

```bash
mkdir -p ./log
nohup python3 -u download.py mgex 2025 149 2025 149 \
  --rename --workers 8 --igs-site-list ./global_B1a \
  --output-dir ./data --path-mode root \
  > ./log/download_mgex_2025_149.txt 2>&1 &
```

### MGEX/IGS 测站：直接传入 rinex 产品目录

结果目录：`./work/data/rinex/2025/152`。

```bash
mkdir -p ./log
nohup python3 -u download.py mgex 2025 152 2025 152 \
  --rename --workers 8 --igs-site-list ./global_B1a \
  --output-dir ./work/data/rinex --path-mode product \
  > ./log/download_mgex_product_2025_152.txt 2>&1 &
```

### iGMAS 测站：显式指定站表

结果目录：`./data/rinex/2025/152` 和 `./data/rinex/2025/153`。需要配置 `igmas` 凭据。站表用于完整性检查和统计；当前 iGMAS 下载源使用当日通配符，可能同时下载站表以外的测站。

```bash
python3 download.py igmas 2025 152 2025 153 \
  --rename --workers 8 --igmas-site-list ./global_B1I \
  --output-dir ./data --path-mode root
```

### BRDM 广播星历：后台下载

结果目录：`./data/brdc/2025`。

```bash
mkdir -p ./log
nohup python3 -u download.py brdm 2025 97 2025 174 \
  --rename --workers 8 --output-dir ./data --path-mode root \
  > ./log/download_brdm_2025_097_174.txt 2>&1 &
```

### SINEX，以及广播星历与 SINEX 联合下载

`snx` 的结果目录为 `./data/snx`；联合下载时，`brdc` 写入 `./data/brdc/2024`，`snx` 写入 `./data/snx`。

```bash
python3 download.py snx 2023 6 2023 6 \
  --no-rename --workers 8 --output-dir ./data --path-mode root

python3 download.py brdc,snx 2024 200 2024 200 \
  --no-rename --workers 8 --output-dir ./data --path-mode root
```

### CAS 快速 DCB 与 WHU 最终 OSB

DCB 结果目录：`./data/dcb/cas/2023`；OSB 结果目录：`./data/osb`。

```bash
python3 download.py cas_dcb_rap 2023 128 2023 130 \
  --no-rename --workers 8 --output-dir ./data --path-mode root

python3 download.py whu_osb_finl 2025 140 2025 149 \
  --no-rename --workers 8 --output-dir ./data --path-mode root
```

如果已有产品目录，DCB 可改用 `--output-dir ./data/dcb/cas --path-mode product`，OSB 可改用 `--output-dir ./data/osb --path-mode product`，结果目录保持相同。

### MGEX：指定测站列表并保留长名

结果目录：`./data/rinex/2024/150`。下面显式使用仓库已有站表；使用自己的站表时，替换 `--igs-site-list` 后面的文件路径即可。站名按空格或换行分隔，站表不支持 `#` 注释。

```bash
python3 download.py mgex 2024 150 2024 150 \
  --no-rename --workers 8 --igs-site-list ./global_B1a \
  --output-dir ./data --path-mode root
```

### 查看后台日志

```bash
tail -f ./log/download_sp3_2025_098_173.txt
```

`Ctrl+C` 只退出这个日志查看命令；之前用 `nohup ... &` 启动的下载继续运行。

## 已有文件与转换

- 开始下载前检查所需输出目录；目录无法创建或不可写时，退出并报告错误。
- 已有可用目标文件会跳过下载。对适用产品，先用 `--no-rename` 下载、再在同一目录用 `--rename` 运行时，程序可复用已有文件并完成重命名；不一定再次下载。
- 观测文件只有具备有效观测 RINEX 头及 `END OF HEADER` 的 `.rnx` 或 `.YYo` 才算完成。缓存的 `.crx`、`.YYd` 和压缩文件会继续转换处理。
- `crx2rnx` 先写临时文件，通过退出状态和 RINEX 头检查后才发布结果；转换失败保留输入文件。
- iGMAS 按日期和下载源使用独立临时目录，在 `exact` 模式下也只处理当前日期的输入。
- 损坏的压缩包会删除并重试当前源或备用源；本地环境导致的解压失败保留压缩包。失败的下载临时文件会丢弃，不进行断点续传。
- 结束后打印应下载、新下载、已存在和失败数量，以及失败目标的日期、文件名和输出目录。

## 账号密码

账号密码集中放在 `download.py` 同目录的 `.download_credentials.json` 中，该文件被 Git 忽略。新克隆且尚未创建凭据文件时执行：

```bash
cp -n ./download_credentials.example.json ./.download_credentials.json
chmod 600 ./.download_credentials.json
```

随后编辑 `.download_credentials.json`，填写各配置的 `username` 和 `password`。`cp -n` 保留已有文件；已有配置直接编辑即可。

| 配置名 | 用途 |
| --- | --- |
| `cddis` | CDDIS HTTPS 的 Earthdata 账号 |
| `cddis_ftp` | CDDIS FTP/FTPS 的匿名登录参数 |
| `igmas` | iGMAS FTP 账号 |

旧的 `.cddis_credentials.json` 不再读取。CDDIS 会话 Cookie 保存在当前用户的 `~/.urs_cookies`。

`download_sources.json` 只引用配置名，例如：

```json
{
  "url": "ftp://server.example/path/to/file.gz",
  "credentials": "igmas"
}
```

无需认证的下载源仍可使用 URL 字符串。缺少或无效的凭据配置会使相应下载源被跳过，并尝试下一备用源。命令和带认证的 `wget` 诊断输出会隐藏登录值；带认证的 `wget` 输出在该命令结束后统一打印。

## 离线检查

从仓库根目录运行：

```bash
python3 -B -m unittest discover -s tests -v
```

测试使用临时文件、模拟下载源和本地 `gzip`/`bin/crx2rnx`，不连接远端下载服务器。
