#!/usr/bin/env python3
"""FPC 9200 Dataset Tool - CLI Entry Point"""
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fpc9200_dataset import main

if __name__ == "__main__":
    main()
