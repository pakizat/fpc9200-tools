# FPC 9200 指纹工具集

FPC Sensor Controller (USB 10a5:9200) 指纹设备工具集，包含：

## 工具列表

### 1. fpc9200-capture — 单次指纹图像采集

直接通过 USB 协议从 FPC 9200 传感器采集单张 112×88 灰度指纹图像。

```bash
cd fpc9200-capture
make
sudo ./fpc9200-capture output.bin
```

### 2. fpc9200-dataset — 数据集收集与匹配分析

收集指纹样本数据集并执行匹配分析，输出分数报告。

```bash
cd fpc9200-dataset

# 录入模板指纹（7 张）
python3 fpc9200-dataset.py --enroll-template

# 录制正确手指样本集
python3 fpc9200-dataset.py --capture genuine

# 录制错误手指样本集
python3 fpc9200-dataset.py --capture impostor

# 匹配计算并输出报告
python3 fpc9200-dataset.py --match

# 查看样本列表
python3 fpc9200-dataset.py --list

# 查看报告列表
python3 fpc9200-dataset.py --report
```

### 3. enclave-probe — Windows 驱动探测工具

用于探测 Windows FPC 驱动中的 enclave DLL 导出函数。

```bash
cd enclave-probe
gcc -o enclave_probe enclave_probe.c -lws2_32
./enclave_probe [mode]
```

### 4. linux-tls-probe — Linux TLS 握手探测

用于探测 FPC 9200 的 TLS PSK 握手过程。

```bash
cd linux-tls-probe
gcc -o fpc9200_tls_probe fpc9200_tls_probe.c -lusb-1.0 -lssl -lcrypto
sudo ./fpc9200_tls_probe
```

## 依赖

- libusb-1.0
- OpenSSL 3.x
- Python 3.10+ (for fpc9200-dataset)

## 许可证

LGPL 2.1 (与 libfprint 一致)
