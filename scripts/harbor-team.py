#!/usr/bin/env python3
"""Repository entry point for the shared Model Harbor team launcher."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ModelHarbor' / 'Support'))
from harbor_team import main

if __name__ == '__main__':
    sys.exit(main())
