# FPC 9200 Fingerprint Tools

FPC Sensor Controller (USB 10a5:9200) fingerprint device toolset for Linux.

## Tools

### fpc9200-dataset — Dataset Collection & Matching Analysis

Electron GUI + Python CLI tool for collecting fingerprint samples and analyzing match scores.

```bash
cd fpc9200-dataset
npm install
npm start          # GUI mode
python3 fpc9200-dataset.py --help  # CLI mode
```

**GUI Features:**
- Dashboard with statistics
- Enroll template fingerprint (7 samples)
- Capture genuine/impostor sample sets
- Run batch matching with score reports
- ROC curve analysis for threshold calibration
- Sample management with quality metrics

**CLI Commands:**
```bash
python3 fpc9200-dataset.py --enroll-template    # Enroll template
python3 fpc9200-dataset.py --capture genuine     # Record genuine samples
python3 fpc9200-dataset.py --capture impostor    # Record impostor samples
python3 fpc9200-dataset.py --match               # Run matching analysis
python3 fpc9200-dataset.py --list                # List samples
python3 fpc9200-dataset.py --report              # List reports
```

### fpc9200-capture — Single Image Capture

Direct USB protocol capture of single 112×88 fingerprint image.

```bash
cd fpc9200-capture
make
sudo ./fpc9200-capture output.bin
```

### enclave-probe — Windows Driver Probe

Probe Windows FPC driver enclave DLL exports.

### linux-tls-probe — TLS Handshake Probe

Probe FPC 9200 TLS PSK handshake process.

## Dependencies

- libusb-1.0
- OpenSSL 3.x
- Python 3.10+
- Node.js 18+ (for GUI)

## License

LGPL 2.1
