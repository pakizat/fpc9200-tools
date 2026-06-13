# FPC 9200 Tools / FPC 9200 工具包

Tools for researching, collecting datasets, and calibrating Linux support for the FPC Sensor Controller fingerprint device `10a5:9200`.

用于 FPC Sensor Controller 指纹设备 `10a5:9200` 的 Linux 适配、数据采集和算法校准工具。

> Status: experimental research tooling. Do not use collected RAW image matching as high-value authentication without reviewing the security notes.
>
> 状态：实验性研究工具。RAW 图像匹配不建议直接用于高价值认证场景，使用前请阅读安全说明。

## Tools / 工具

### `fpc9200-dataset`

Electron GUI + Python CLI for collecting genuine/impostor fingerprint samples and evaluating matching rules.

Electron 图形界面 + Python CLI，用于采集正确/错误手指样本，并离线评估匹配算法。

Features / 功能：

- Export current libfprint/fprintd RAW template copy as `template.f9rm`
- Capture single 112x88 RAW probe images through `fpc9200-capture`
- Collect genuine and impostor datasets
- Run accelerated batch matching with a C NCC core
- Report legacy score, raw score, center score, edge score, block consistency, and v2 ACCEPT/RETRY/REJECT decisions
- Manage samples from the GUI

```bash
cd fpc9200-dataset
npm install
npm start
```

CLI:

```bash
python3 fpc9200-dataset.py --help
python3 fpc9200-dataset.py --template-info
python3 fpc9200-dataset.py --capture-once genuine
python3 fpc9200-dataset.py --capture-once impostor
python3 fpc9200-dataset.py --match
python3 fpc9200-dataset.py --status
```

### `fpc9200-capture`

Standalone C tool that performs USB/TLS capture and writes one decrypted 112x88 RAW grayscale image.

独立 C 采集工具，通过 USB/TLS 获取并保存单张 112x88 RAW 灰度图。

```bash
cd fpc9200-capture
make
sudo ./fpc9200-capture /tmp/fpc9200-sample.bin
```

### `linux-tls-probe`

Linux-side TLS PSK handshake probe used during protocol research.

Linux 侧 TLS PSK 握手探测工具，用于协议研究。

### `enclave-probe`

Windows driver DLL probing source/scripts used during early protocol research. Proprietary DLLs, generated binaries, Wine prefixes, and VM logs are intentionally not tracked.

Windows 驱动 DLL 探测源码/脚本，用于早期协议研究。专有 DLL、生成的二进制文件、Wine 环境和 VM 日志不会被提交。

## Screenshots / 截图

GUI screenshots can be added here later:

后续可在此处添加 GUI 截图：

- `docs/screenshots/dashboard.png`
- `docs/screenshots/capture.png`
- `docs/screenshots/report.png`

The placeholder directory is tracked, but real screenshots are optional.

已保留截图目录占位，实际截图可后续自行添加。

## Privacy / 隐私与数据

This repository must not contain real fingerprint data.

本仓库不应包含真实指纹数据。

Ignored by `.gitignore`:

- RAW fingerprint images: `*.bin`, `*.raw`
- Exported templates: `*.f9rm`, `*.fp3`, `template.*`
- Dataset folders: `data/`, `captures/`, `reports/`
- Generated reports: `report_*.json`
- Build outputs and local runtime caches

Before publishing, verify:

发布前请检查：

```bash
git status --short
git check-ignore -v fpc9200-dataset/data/template.f9rm || true
git check-ignore -v ../data/fpc9200-dataset/genuine/genuine_0001.bin || true
```

## Security Notes / 安全说明

The current RAW matching path is a device-specific fallback for enabling native Fedora/libfprint testing on `10a5:9200`. It is not a vendor-certified biometric template algorithm.

当前 RAW 匹配路径是面向 `10a5:9200` 的设备专用兜底方案，用于 Fedora/libfprint 原生测试。它不是厂商认证的生物特征模板算法。

Recommended use:

建议用途：

- Manual `fprintd-enroll` / `fprintd-verify` testing
- Offline dataset collection and algorithm calibration
- Research and development

Not recommended yet:

暂不建议：

- PAM login
- Screen unlock
- Disk unlock
- `sudo` or other high-value authentication

## Related Driver / 相关驱动

The matching algorithm intended for libfprint is maintained in:

libfprint 适配代码维护在：

`https://github.com/pakizat/libfprint-fpc9200`

## License / 许可证

Unless otherwise stated, source files in this repository are released under LGPL-2.1-or-later to match libfprint-oriented development.

除非文件另有说明，本仓库源码按 LGPL-2.1-or-later 发布，以匹配 libfprint 相关开发。
